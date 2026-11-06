"""Accounts, sessions, and the setup change log becomes the audit log (milestone 6A).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-05 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('site', sa.Column('security', postgresql.JSONB(astext_type=sa.Text()), server_default='{}',
                                    nullable=False))
    op.create_table(
        'app_user',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('site_id', sa.UUID(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('dis', sa.Text(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('platform', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('totp_secret', sa.String(length=64), nullable=True),
        sa.Column('totp_pending', sa.String(length=64), nullable=True),
        sa.Column('failed_logins', sa.Integer(), server_default='0', nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('viewer', 'operator', 'admin')", name='ck_user_role'),
        sa.ForeignKeyConstraint(['site_id'], ['site.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_id', 'username'),
    )
    op.create_index(op.f('ix_app_user_site_id'), 'app_user', ['site_id'], unique=False)
    op.create_table(
        'user_session',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('client', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index(op.f('ix_user_session_user_id'), 'user_session', ['user_id'], unique=False)
    # The milestone 5 change log keeps its entries and becomes the audit log.
    op.rename_table('setup_change', 'audit_log')
    op.execute('ALTER INDEX ix_setup_change_site_id RENAME TO ix_audit_log_site_id')
    op.execute('ALTER INDEX ix_setup_change_ts RENAME TO ix_audit_log_ts')
    op.execute('ALTER TABLE audit_log RENAME CONSTRAINT setup_change_pkey TO audit_log_pkey')
    op.execute('ALTER TABLE audit_log RENAME CONSTRAINT setup_change_site_id_fkey TO audit_log_site_id_fkey')
    op.add_column('audit_log', sa.Column('user_id', sa.UUID(), nullable=True))
    op.add_column('audit_log', sa.Column('username', sa.String(length=64), nullable=True))
    op.create_foreign_key('audit_log_user_id_fkey', 'audit_log', 'app_user', ['user_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_audit_log_user_id'), 'audit_log', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_log_user_id'), table_name='audit_log')
    op.drop_constraint('audit_log_user_id_fkey', 'audit_log', type_='foreignkey')
    op.drop_column('audit_log', 'username')
    op.drop_column('audit_log', 'user_id')
    op.execute('ALTER TABLE audit_log RENAME CONSTRAINT audit_log_site_id_fkey TO setup_change_site_id_fkey')
    op.execute('ALTER TABLE audit_log RENAME CONSTRAINT audit_log_pkey TO setup_change_pkey')
    op.execute('ALTER INDEX ix_audit_log_ts RENAME TO ix_setup_change_ts')
    op.execute('ALTER INDEX ix_audit_log_site_id RENAME TO ix_setup_change_site_id')
    op.rename_table('audit_log', 'setup_change')
    op.drop_index(op.f('ix_user_session_user_id'), table_name='user_session')
    op.drop_table('user_session')
    op.drop_index(op.f('ix_app_user_site_id'), table_name='app_user')
    op.drop_table('app_user')
    op.drop_column('site', 'security')
