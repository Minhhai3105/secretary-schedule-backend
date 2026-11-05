"""Initial PostgreSQL schema for the secretary schedule backend.

Revision ID: 001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('schedules',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('starts_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('meeting_mode', sa.String(length=20), nullable=False),
    sa.Column('location', sa.String(length=500), nullable=True),
    sa.Column('meeting_url', sa.String(length=2048), nullable=True),
    sa.Column('related_person', sa.String(length=255), server_default='Tôi', nullable=False),
    sa.Column('preparation', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('cancelled_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("btrim(related_person) <> ''", name='ck_schedules_related_person'),
    sa.CheckConstraint("meeting_mode IN ('online', 'offline')", name='ck_schedules_mode'),
    sa.CheckConstraint('ends_at_utc IS NULL OR ends_at_utc > starts_at_utc', name='ck_schedules_time'),
    sa.CheckConstraint('version >= 1', name='ck_schedules_version'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('auth_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('expires_at_utc > created_at_utc', name='ck_auth_sessions_expiry'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('refresh_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('created_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('expires_at_utc > created_at_utc', name='ck_refresh_tokens_expiry'),
    sa.ForeignKeyConstraint(['session_id'], ['auth_sessions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index('ix_schedules_user_starts_at', 'schedules', ['user_id', 'starts_at_utc'], unique=False)
    op.create_table('password_resets',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('expires_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index('ix_password_resets_user_expiry', 'password_resets', ['user_id', 'expires_at_utc'], unique=False)
    op.create_table('reminder_settings',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('daily_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('daily_time_vietnam', sa.Time(), server_default=sa.text("'07:00:00'"), nullable=False),
    sa.Column('near_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('lead_minutes', sa.Integer(), server_default=sa.text('30'), nullable=False),
    sa.CheckConstraint('lead_minutes BETWEEN 1 AND 10080', name='ck_reminder_settings_lead_minutes'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.execute('INSERT INTO reminder_settings (user_id) SELECT id FROM users')
    op.create_table('chat_proposals',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('schedule_id', sa.UUID(), nullable=True),
    sa.Column('schedule_version', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('proposed_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('expires_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('processed_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(action = 'create' AND schedule_id IS NULL AND schedule_version IS NULL) OR (action IN ('update', 'cancel') AND schedule_id IS NOT NULL AND schedule_version IS NOT NULL)", name='ck_chat_proposals_target'),
    sa.CheckConstraint("action IN ('create', 'update', 'cancel')", name='ck_chat_proposals_action'),
    sa.CheckConstraint("status IN ('pending', 'confirmed', 'cancelled')", name='ck_chat_proposals_status'),
    sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chat_proposals_user_status_expiry', 'chat_proposals', ['user_id', 'status', 'expires_at_utc'], unique=False)
    op.create_table('email_deliveries',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('schedule_id', sa.UUID(), nullable=True),
    sa.Column('event_key', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('applicable_date_vietnam', sa.Date(), nullable=True),
    sa.Column('due_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('sent_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('daily', 'near', 'update')", name='ck_email_deliveries_kind'),
    sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed', 'unknown', 'cancelled')", name='ck_email_deliveries_status'),
    sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_key')
    )
    op.create_index('ix_email_deliveries_status_due', 'email_deliveries', ['status', 'due_at_utc'], unique=False)
    op.create_index('ix_email_deliveries_user_due', 'email_deliveries', ['user_id', 'due_at_utc'], unique=False)
    op.create_table('email_attempts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('delivery_id', sa.UUID(), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('sending', 'success', 'failed', 'unknown')", name='ck_email_attempts_status'),
    sa.CheckConstraint('attempt_number BETWEEN 1 AND 4', name='ck_email_attempts_number'),
    sa.ForeignKeyConstraint(['delivery_id'], ['email_deliveries.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('delivery_id', 'attempt_number', name='uq_email_attempts_delivery_number')
    )


def downgrade() -> None:
    op.drop_table('email_attempts')
    op.drop_index('ix_email_deliveries_user_due', table_name='email_deliveries')
    op.drop_index('ix_email_deliveries_status_due', table_name='email_deliveries')
    op.drop_table('email_deliveries')
    op.drop_index('ix_chat_proposals_user_status_expiry', table_name='chat_proposals')
    op.drop_table('chat_proposals')
    op.drop_table('reminder_settings')
    op.drop_index('ix_password_resets_user_expiry', table_name='password_resets')
    op.drop_table('password_resets')
    op.drop_index('ix_schedules_user_starts_at', table_name='schedules')
    op.drop_table('refresh_tokens')
    op.drop_table('auth_sessions')
    op.drop_table('schedules')
    op.drop_table('users')
