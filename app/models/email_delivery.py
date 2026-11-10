from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailDelivery(Base):
    __tablename__ = "email_deliveries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('daily', 'near', 'update')",
            name="ck_email_deliveries_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'unknown', 'cancelled')",
            name="ck_email_deliveries_status",
        ),
        Index("ix_email_deliveries_user_due", "user_id", "due_at_utc"),
        Index("ix_email_deliveries_status_due", "status", "due_at_utc"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    schedule_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("schedules.id"), nullable=True,
    )
    event_key: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    applicable_date_vietnam: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    due_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    sent_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
