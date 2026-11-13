from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.schedules import owned_schedule
from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.chat_message import ChatMessage
from app.models.chat_proposal import ChatProposal
from app.models.schedule import Schedule
from app.models.user import User
from app.schemas.chat import (
    ChatConfirmResponse,
    ChatHistoryItem,
    ChatHistoryPage,
    ChatHistoryQuery,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatProposalResponse,
)
from app.schemas.schedule import ScheduleCreate, ScheduleResponse, ScheduleUpdate
from app.services.chat_ai import ask_schedule_ai, stream_schedule_ai

router = APIRouter(prefix="/api/chat", tags=["Chat"])
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PROPOSAL_TTL = timedelta(minutes=15)
MAX_CONTEXT_SCHEDULES = 200


class AIResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: Literal["answer", "create", "update", "cancel"]
    answer: str = Field(min_length=1)
    schedule_ids: list[UUID] = Field(default_factory=list, max_length=20)
    schedule_id: UUID | None = None
    schedule: dict | None = None


def _proposal_response(proposal: ChatProposal) -> ChatProposalResponse:
    return ChatProposalResponse(
        id=proposal.id,
        action=proposal.action,
        schedule_id=proposal.schedule_id,
        schedule_version=proposal.schedule_version,
        proposed_data=proposal.proposed_data,
        expires_at_utc=proposal.expires_at_utc,
    )


def _save_exchange(
    db: Session, user_id: UUID, message: str, response: ChatMessageResponse,
    started_at: datetime,
) -> None:
    answered_at = max(datetime.now(timezone.utc), started_at + timedelta(microseconds=1))
    db.add_all([
        ChatMessage(
            user_id=user_id, role="user", content=message, created_at_utc=started_at,
        ),
        ChatMessage(
            user_id=user_id, role="assistant", content=response.answer,
            response_data=response.model_dump(mode="json"), created_at_utc=answered_at,
        ),
    ])
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


@router.get("/messages", response_model=ChatHistoryPage)
def list_chat_messages(
    pagination: Annotated[ChatHistoryQuery, Query()],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatHistoryPage:
    limit = pagination.limit
    cursor = pagination.cursor
    query = select(ChatMessage).where(ChatMessage.user_id == user.id)
    if cursor is not None:
        anchor = db.scalar(query.where(ChatMessage.id == cursor))
        if anchor is None:
            raise HTTPException(status_code=400, detail="Invalid chat cursor")
        query = query.where(or_(
            ChatMessage.created_at_utc < anchor.created_at_utc,
            and_(
                ChatMessage.created_at_utc == anchor.created_at_utc,
                ChatMessage.id < anchor.id,
            ),
        ))
    rows = db.scalars(
        query.order_by(ChatMessage.created_at_utc.desc(), ChatMessage.id.desc())
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    proposal_ids = [
        UUID(row.response_data["proposal"]["id"])
        for row in rows
        if row.response_data and row.response_data.get("proposal")
    ]
    proposals = {
        item.id: item for item in db.scalars(
            select(ChatProposal).where(
                ChatProposal.user_id == user.id, ChatProposal.id.in_(proposal_ids)
            )
        ).all()
    } if proposal_ids else {}
    now = datetime.now(timezone.utc)
    items = []
    for row in rows:
        saved = ChatMessageResponse.model_validate(row.response_data) if row.response_data else None
        proposal = proposals.get(saved.proposal.id) if saved and saved.proposal else None
        proposal_state = None
        if saved and saved.proposal:
            proposal_state = (
                "stale" if proposal is None or (
                    proposal.status == "pending" and proposal.expires_at_utc <= now
                ) else proposal.status
            )
        items.append(ChatHistoryItem(
            id=row.id, role=row.role, content=row.content,
            created_at_utc=row.created_at_utc,
            schedules=saved.schedules if saved else [],
            proposal=saved.proposal if saved else None,
            proposal_state=proposal_state,
        ))
    return ChatHistoryPage(
        messages=items,
        next_cursor=rows[-1].id if has_more else None,
    )


@router.post("/messages", response_model=ChatMessageResponse)
def chat_message(
    payload: ChatMessageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatMessageResponse:
    now, by_id, history, context = _chat_context(user, db)
    raw = ask_schedule_ai(payload.message, history, context, now.astimezone(VIETNAM_TZ).isoformat())
    return _complete_exchange(payload, user, db, now, by_id, raw)


def _chat_context(user: User, db: Session):
    now = datetime.now(timezone.utc)
    schedules = db.scalars(
        select(Schedule)
        .where(Schedule.user_id == user.id)
        .order_by(Schedule.starts_at_utc.desc(), Schedule.id)
        .limit(MAX_CONTEXT_SCHEDULES)
    ).all()
    by_id = {item.id: item for item in schedules}
    context = [
        {
            "id": str(item.id),
            "title": item.title,
            "starts_at_utc": item.starts_at_utc.isoformat(),
            "ends_at_utc": item.ends_at_utc.isoformat() if item.ends_at_utc else None,
            "meeting_mode": item.meeting_mode,
            "location": item.location,
            "related_person": item.related_person,
            "cancelled": item.cancelled_at_utc is not None,
            "version": item.version,
        }
        for item in schedules
    ]
    recent = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at_utc.desc(), ChatMessage.id.desc())
        .limit(5)
    ).all()
    return now, by_id, [
        {"role": turn.role, "content": turn.content} for turn in reversed(recent)
    ], context


def stream_chat_message(payload: ChatMessageRequest, user: User, db: Session):
    now, by_id, history, context = _chat_context(user, db)
    with_stream = stream_schedule_ai(
        payload.message, history, context, now.astimezone(VIETNAM_TZ).isoformat(),
    )
    try:
        for kind, value in with_stream:
            if kind == "result":
                yield "result", _complete_exchange(payload, user, db, now, by_id, value)
            else:
                yield kind, value
    finally:
        with_stream.close()


def _complete_exchange(
    payload: ChatMessageRequest, user: User, db: Session, now: datetime,
    by_id: dict[UUID, Schedule], raw: dict,
) -> ChatMessageResponse:
    try:
        result = AIResult.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail="Invalid chat AI response") from exc

    if result.intent == "answer":
        selected = [by_id[item] for item in result.schedule_ids if item in by_id]
        response = ChatMessageResponse(
            answer=result.answer,
            schedules=[ScheduleResponse.model_validate(item) for item in selected],
        )
        _save_exchange(db, user.id, payload.message, response, now)
        return response

    target = None
    if result.intent in ("update", "cancel"):
        target = by_id.get(result.schedule_id)
        if target is None:
            raise HTTPException(status_code=422, detail="Schedule target is unavailable")
        if target.cancelled_at_utc is not None:
            raise HTTPException(status_code=409, detail="Schedule already cancelled")

    try:
        if result.intent == "create":
            if not isinstance(result.schedule, dict):
                raise ValueError("Missing schedule")
            proposed_data = ScheduleCreate.model_validate(result.schedule).model_dump(
                mode="json"
            )
        elif result.intent == "update":
            if target.starts_at_utc <= now or not isinstance(result.schedule, dict):
                raise ValueError("Schedule cannot be edited")
            proposed_data = ScheduleUpdate.model_validate(
                {**result.schedule, "version": target.version}
            ).model_dump(mode="json", exclude_unset=True, exclude={"version"})
            new_start = datetime.fromisoformat(
                proposed_data.get("starts_at_utc", target.starts_at_utc.isoformat())
            )
            end_value = proposed_data.get("ends_at_utc", target.ends_at_utc)
            new_end = (
                datetime.fromisoformat(end_value)
                if isinstance(end_value, str)
                else end_value
            )
            if new_end is not None and new_end <= new_start:
                raise ValueError("End time must be after start time")
        else:
            proposed_data = {}
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid schedule proposal") from exc

    proposal = ChatProposal(
        user_id=user.id,
        schedule_id=target.id if target else None,
        schedule_version=target.version if target else None,
        action=result.intent,
        proposed_data=proposed_data,
        expires_at_utc=now + PROPOSAL_TTL,
    )
    try:
        db.add(proposal)
        db.flush()
        response = ChatMessageResponse(
            answer=result.answer,
            proposal=_proposal_response(proposal),
        )
        _save_exchange(db, user.id, payload.message, response, now)
    except Exception:
        db.rollback()
        raise
    return response


@router.post(
    "/proposals/{proposal_id}/confirm",
    response_model=ChatConfirmResponse,
)
def confirm_proposal(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatConfirmResponse:
    proposal = db.scalar(
        select(ChatProposal)
        .where(ChatProposal.id == proposal_id, ChatProposal.user_id == user.id)
        .with_for_update()
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending" or proposal.expires_at_utc <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="Proposal is no longer available")

    if proposal.action == "create":
        try:
            data = ScheduleCreate.model_validate(proposal.proposed_data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Invalid schedule proposal") from exc
        schedule = Schedule(
            user_id=user.id,
            title=data.title,
            starts_at_utc=data.starts_at_utc,
            ends_at_utc=data.ends_at_utc,
            meeting_mode=data.meeting_mode,
            location=data.location,
            meeting_url=str(data.meeting_url) if data.meeting_url else None,
            related_person=data.related_person,
            preparation=data.preparation,
            notes=data.notes,
        )
        db.add(schedule)
    else:
        schedule = owned_schedule(db, user.id, proposal.schedule_id, lock=True)
        if schedule.version != proposal.schedule_version:
            raise HTTPException(status_code=409, detail="Schedule version conflict")
        if schedule.cancelled_at_utc is not None:
            raise HTTPException(status_code=409, detail="Schedule already cancelled")
        if proposal.action == "update":
            if schedule.starts_at_utc <= datetime.now(timezone.utc):
                raise HTTPException(status_code=409, detail="Schedule cannot be edited")
            try:
                changes = ScheduleUpdate.model_validate(
                    {**proposal.proposed_data, "version": schedule.version}
                ).model_dump(exclude_unset=True, exclude={"version"})
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail="Invalid schedule proposal") from exc
            new_start = changes.get("starts_at_utc", schedule.starts_at_utc)
            new_end = changes.get("ends_at_utc", schedule.ends_at_utc)
            if new_end is not None and new_end <= new_start:
                raise HTTPException(status_code=422, detail="End time must be after start time")
            if changes.get("meeting_url") is not None:
                changes["meeting_url"] = str(changes["meeting_url"])
            for field, value in changes.items():
                setattr(schedule, field, value)
        elif proposal.action == "cancel":
            schedule.cancelled_at_utc = datetime.now(timezone.utc)
        else:
            raise HTTPException(status_code=422, detail="Invalid proposal action")
        schedule.version += 1

    proposal.status = "confirmed"
    proposal.processed_at_utc = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(schedule)
    return ChatConfirmResponse(schedule=ScheduleResponse.model_validate(schedule))


@router.post("/proposals/{proposal_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_proposal(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    proposal = db.scalar(
        select(ChatProposal)
        .where(ChatProposal.id == proposal_id, ChatProposal.user_id == user.id)
        .with_for_update()
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="Proposal already processed")
    proposal.status = "cancelled"
    proposal.processed_at_utc = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
