from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailAttempt(Base):
    __tablename__ = "email_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_id", "attempt_number",
            name="uq_email_attempts_delivery_number",
        ),
        CheckConstraint(
            "attempt_number BETWEEN 1 AND 4",
            name="ck_email_attempts_number",
        ),
        CheckConstraint(
            "status IN ('sending', 'success', 'failed', 'unknown')",
            name="ck_email_attempts_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    delivery_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("email_deliveries.id"), nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    finished_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
