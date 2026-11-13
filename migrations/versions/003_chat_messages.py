"""Persist user chat messages for cursor based history.

Revision ID: 003
Revises: 002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("chat_messages"):
        # The old chat migration also used 002. Preserve its existing messages
        # and apply the related-person default that this history may have skipped.
        expected_columns = {
            "id", "user_id", "role", "content", "response_data", "created_at_utc"
        }
        columns = {column["name"] for column in inspector.get_columns("chat_messages")}
        if columns != expected_columns:
            raise RuntimeError("Unexpected chat_messages schema; inspect it before upgrading")
        indexes = {index["name"] for index in inspector.get_indexes("chat_messages")}
        if "ix_chat_messages_user_created_id" not in indexes:
            op.create_index(
                "ix_chat_messages_user_created_id", "chat_messages",
                ["user_id", "created_at_utc", "id"], unique=False,
            )
        op.alter_column("schedules", "related_person", server_default="Me")
        return

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("response_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_messages_user_created_id", "chat_messages",
        ["user_id", "created_at_utc", "id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_user_created_id", table_name="chat_messages")
    op.drop_table("chat_messages")
