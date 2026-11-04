from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PasswordReset(Base):
    __tablename__ = "password_resets"
    __table_args__ = (
        Index("ix_password_resets_user_expiry", "user_id", "expires_at_utc"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    expires_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    used_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
