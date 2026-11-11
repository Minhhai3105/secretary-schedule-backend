"""Use Me as the default related person for new schedules.

Revision ID: 002
Revises: 001
"""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("schedules", "related_person", server_default="Me")


def downgrade() -> None:
    op.alter_column("schedules", "related_person", server_default="Tôi")
