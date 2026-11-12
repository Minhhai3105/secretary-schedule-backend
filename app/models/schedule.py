from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_user_starts_at", "user_id", "starts_at_utc"),
        CheckConstraint(
            "ends_at_utc IS NULL OR ends_at_utc > starts_at_utc",
            name="ck_schedules_time",
        ),
        CheckConstraint(
            "meeting_mode IN ('online', 'offline')",
            name="ck_schedules_mode",
        ),
        CheckConstraint(
            "btrim(related_person) <> ''",
            name="ck_schedules_related_person",
        ),
        CheckConstraint("version >= 1", name="ck_schedules_version"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    ends_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    meeting_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    related_person: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="Me",
    )
    preparation: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"),
    )
