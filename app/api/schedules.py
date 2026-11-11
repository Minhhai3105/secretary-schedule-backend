from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.schedule import Schedule
from app.models.user import User
from app.schemas.schedule import (
    PageInfo,
    ScheduleCancel,
    ScheduleConflictResponse,
    ScheduleCreate,
    ScheduleHistoryResponse,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdate,
)

router = APIRouter(prefix="/api/schedules", tags=["Schedules"])
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
HISTORY_PAGE_SIZE = 20


def owned_schedule(
    db: Session, user_id: UUID, schedule_id: UUID, *, lock: bool = False
) -> Schedule:
    statement = select(Schedule).where(
        Schedule.id == schedule_id, Schedule.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update()
    schedule = db.scalar(statement)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Schedule:
    schedule = Schedule(
        user_id=user.id,
        title=payload.title,
        starts_at_utc=payload.starts_at_utc,
        ends_at_utc=payload.ends_at_utc,
        meeting_mode=payload.meeting_mode,
        location=payload.location,
        meeting_url=str(payload.meeting_url) if payload.meeting_url else None,
        related_person=payload.related_person,
        preparation=payload.preparation,
        notes=payload.notes,
    )
    db.add(schedule)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(schedule)
    return schedule


@router.get("", response_model=ScheduleListResponse)
def list_schedules(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    related_person: str | None = Query(default=None, max_length=255),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleListResponse:
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="'to' must be on or after 'from'")

    start_utc = datetime.combine(
        from_date, time.min, tzinfo=VIETNAM_TZ
    ).astimezone(timezone.utc)
    end_utc = datetime.combine(
        to_date + timedelta(days=1), time.min, tzinfo=VIETNAM_TZ
    ).astimezone(timezone.utc)

    statement = select(Schedule).where(
        Schedule.user_id == user.id,
        Schedule.cancelled_at_utc.is_(None),
        Schedule.starts_at_utc >= start_utc,
        Schedule.starts_at_utc < end_utc,
    )
    if related_person and related_person.strip():
        statement = statement.where(
            Schedule.related_person == related_person.strip()
        )

    schedules = db.scalars(
        statement.order_by(Schedule.starts_at_utc, Schedule.id)
    ).all()
    people = db.scalars(
        select(Schedule.related_person)
        .where(Schedule.user_id == user.id)
        .distinct()
        .order_by(Schedule.related_person)
    ).all()
    return ScheduleListResponse(
        schedules=[ScheduleResponse.model_validate(item) for item in schedules],
        related_people=sorted(set(people) | {"Tôi"}),
    )


@router.get("/history", response_model=ScheduleHistoryResponse)
def schedule_history(
    q: str | None = Query(default=None, max_length=255),
    history_type: Literal["all", "past", "cancelled"] = Query(
        default="all", alias="type"
    ),
    page: int = Query(default=1, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleHistoryResponse:
    now = datetime.now(timezone.utc)
    statement = select(Schedule).where(Schedule.user_id == user.id)

    if history_type == "past":
        statement = statement.where(
            Schedule.cancelled_at_utc.is_(None),
            Schedule.starts_at_utc < now,
        )
    elif history_type == "cancelled":
        statement = statement.where(Schedule.cancelled_at_utc.is_not(None))
    else:
        statement = statement.where(
            or_(
                Schedule.cancelled_at_utc.is_not(None),
                Schedule.starts_at_utc < now,
            )
        )

    if q and q.strip():
        statement = statement.where(
            Schedule.title.icontains(q.strip(), autoescape=True)
        )

    total = db.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    schedules = db.scalars(
        statement.order_by(
            case((Schedule.cancelled_at_utc.is_not(None), 0), else_=1),
            func.coalesce(
                Schedule.cancelled_at_utc, Schedule.starts_at_utc
            ).desc(),
            Schedule.id.desc(),
        )
        .offset((page - 1) * HISTORY_PAGE_SIZE)
        .limit(HISTORY_PAGE_SIZE)
    ).all()
    return ScheduleHistoryResponse(
        schedules=[ScheduleResponse.model_validate(item) for item in schedules],
        paging=PageInfo(
            page=page,
            page_size=HISTORY_PAGE_SIZE,
            total=total,
            total_pages=(total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE,
        ),
    )


@router.get("/conflicts", response_model=ScheduleConflictResponse)
def check_schedule_conflicts(
    starts_at_utc: datetime = Query(...),
    ends_at_utc: datetime | None = Query(default=None),
    exclude_id: UUID | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleConflictResponse:
    if starts_at_utc.tzinfo is None:
        starts_at_utc = starts_at_utc.replace(tzinfo=timezone.utc)
    else:
        starts_at_utc = starts_at_utc.astimezone(timezone.utc)

    if ends_at_utc is not None:
        if ends_at_utc.tzinfo is None:
            ends_at_utc = ends_at_utc.replace(tzinfo=timezone.utc)
        else:
            ends_at_utc = ends_at_utc.astimezone(timezone.utc)
        if ends_at_utc <= starts_at_utc:
            raise HTTPException(
                status_code=422, detail="End time must be after start time"
            )

    statement = select(Schedule).where(
        Schedule.user_id == user.id,
        Schedule.cancelled_at_utc.is_(None),
    )
    if exclude_id is not None:
        statement = statement.where(Schedule.id != exclude_id)

    if ends_at_utc is not None:
        overlap_condition = or_(
            and_(
                Schedule.ends_at_utc.is_not(None),
                Schedule.starts_at_utc < ends_at_utc,
                Schedule.ends_at_utc > starts_at_utc,
            ),
            and_(
                Schedule.ends_at_utc.is_(None),
                Schedule.starts_at_utc >= starts_at_utc,
                Schedule.starts_at_utc < ends_at_utc,
            ),
        )
    else:
        overlap_condition = or_(
            and_(
                Schedule.ends_at_utc.is_not(None),
                Schedule.starts_at_utc <= starts_at_utc,
                Schedule.ends_at_utc > starts_at_utc,
            ),
            and_(
                Schedule.ends_at_utc.is_(None),
                Schedule.starts_at_utc == starts_at_utc,
            ),
        )

    conflicts = db.scalars(
        statement.where(overlap_condition).order_by(
            Schedule.starts_at_utc, Schedule.id
        )
    ).all()
    return ScheduleConflictResponse(
        has_conflict=bool(conflicts),
        conflicts=[ScheduleResponse.model_validate(item) for item in conflicts],
    )


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule(
    schedule_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Schedule:
    return owned_schedule(db, user.id, schedule_id)


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
def update_schedule(
    schedule_id: UUID,
    payload: ScheduleUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Schedule:
    schedule = owned_schedule(db, user.id, schedule_id, lock=True)
    if schedule.cancelled_at_utc is not None or schedule.starts_at_utc <= datetime.now(
        timezone.utc
    ):
        raise HTTPException(status_code=409, detail="Schedule cannot be edited")
    if schedule.version != payload.version:
        raise HTTPException(status_code=409, detail="Schedule version conflict")

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if changes.get("meeting_url") is not None:
        changes["meeting_url"] = str(changes["meeting_url"])

    new_start = changes.get("starts_at_utc", schedule.starts_at_utc)
    new_end = changes.get("ends_at_utc", schedule.ends_at_utc)
    if new_end is not None and new_end <= new_start:
        raise HTTPException(
            status_code=422, detail="End time must be after start time"
        )

    for field, value in changes.items():
        setattr(schedule, field, value)
    schedule.version += 1
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(schedule)
    return schedule


@router.post("/{schedule_id}/cancel", response_model=ScheduleResponse)
def cancel_schedule(
    schedule_id: UUID,
    payload: ScheduleCancel,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Schedule:
    schedule = owned_schedule(db, user.id, schedule_id, lock=True)
    if schedule.version != payload.version:
        raise HTTPException(status_code=409, detail="Schedule version conflict")
    if schedule.cancelled_at_utc is not None:
        raise HTTPException(status_code=409, detail="Schedule already cancelled")

    schedule.cancelled_at_utc = datetime.now(timezone.utc)
    schedule.version += 1
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(schedule)
    return schedule
