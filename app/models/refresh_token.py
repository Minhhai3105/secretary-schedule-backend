from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint(
            "expires_at_utc > created_at_utc",
            name="ck_refresh_tokens_expiry",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("auth_sessions.id"), nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    expires_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    used_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    revoked_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
