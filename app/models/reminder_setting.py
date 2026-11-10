from datetime import time
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Time, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReminderSetting(Base):
    __tablename__ = "reminder_settings"
    __table_args__ = (
        CheckConstraint(
            "lead_minutes BETWEEN 1 AND 10080",
            name="ck_reminder_settings_lead_minutes",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True,
    )
    daily_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    daily_time_vietnam: Mapped[time] = mapped_column(
        Time, nullable=False, server_default=text("'07:00:00'"),
    )
    near_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    lead_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("30"),
    )
