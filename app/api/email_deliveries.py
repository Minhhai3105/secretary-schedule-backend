from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.email_attempt import EmailAttempt
from app.models.email_delivery import EmailDelivery
from app.models.user import User
from app.schemas.email_audit import (
    EmailAttemptResponse,
    EmailDeliveryDetail,
    EmailDeliveryPage,
    EmailDeliveryResponse,
    EmailPageInfo,
)

router = APIRouter(prefix="/api/email-deliveries", tags=["Email audit"])
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
PAGE_SIZE = 20


@router.get("", response_model=EmailDeliveryPage)
def list_email_deliveries(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    delivery_status: Literal[
        "pending", "sending", "sent", "failed", "unknown", "cancelled"
    ] | None = Query(default=None, alias="status"),
    kind: Literal["daily", "near", "update"] | None = None,
    page: int = Query(default=1, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailDeliveryPage:
    if from_date is not None and to_date is not None and to_date < from_date:
        raise HTTPException(status_code=422, detail="'to' must be on or after 'from'")

    statement = select(EmailDelivery).where(EmailDelivery.user_id == user.id)
    if from_date is not None:
        start_utc = datetime.combine(
            from_date, time.min, tzinfo=VIETNAM_TZ
        ).astimezone(timezone.utc)
        statement = statement.where(EmailDelivery.due_at_utc >= start_utc)
    if to_date is not None:
        end_utc = datetime.combine(
            to_date + timedelta(days=1), time.min, tzinfo=VIETNAM_TZ
        ).astimezone(timezone.utc)
        statement = statement.where(EmailDelivery.due_at_utc < end_utc)
    if delivery_status is not None:
        statement = statement.where(EmailDelivery.status == delivery_status)
    if kind is not None:
        statement = statement.where(EmailDelivery.kind == kind)

    total = db.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    deliveries = db.scalars(
        statement.order_by(EmailDelivery.due_at_utc.desc(), EmailDelivery.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return EmailDeliveryPage(
        deliveries=[
            EmailDeliveryResponse.model_validate(item) for item in deliveries
        ],
        paging=EmailPageInfo(
            page=page,
            page_size=PAGE_SIZE,
            total=total,
            total_pages=(total + PAGE_SIZE - 1) // PAGE_SIZE,
        ),
    )


@router.get("/{delivery_id}", response_model=EmailDeliveryDetail)
def get_email_delivery(
    delivery_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailDeliveryDetail:
    delivery = db.scalar(
        select(EmailDelivery).where(
            EmailDelivery.id == delivery_id,
            EmailDelivery.user_id == user.id,
        )
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Email delivery not found")

    attempts = db.scalars(
        select(EmailAttempt)
        .where(EmailAttempt.delivery_id == delivery.id)
        .order_by(EmailAttempt.attempt_number)
    ).all()
    data = EmailDeliveryResponse.model_validate(delivery).model_dump()
    return EmailDeliveryDetail(
        **data,
        attempts=[EmailAttemptResponse.model_validate(item) for item in attempts],
    )
