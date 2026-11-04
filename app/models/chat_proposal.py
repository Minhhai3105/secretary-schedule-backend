from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatProposal(Base):
    __tablename__ = "chat_proposals"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'update', 'cancel')",
            name="ck_chat_proposals_action",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'cancelled')",
            name="ck_chat_proposals_status",
        ),
        CheckConstraint(
            "(action = 'create' AND schedule_id IS NULL AND schedule_version IS NULL)"
            " OR (action IN ('update', 'cancel') AND schedule_id IS NOT NULL"
            " AND schedule_version IS NOT NULL)",
            name="ck_chat_proposals_target",
        ),
        Index("ix_chat_proposals_user_status_expiry", "user_id", "status", "expires_at_utc"),
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
    schedule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    proposed_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    expires_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    processed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
