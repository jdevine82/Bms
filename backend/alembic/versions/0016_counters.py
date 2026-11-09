"""Maintenance counters (main spec 5.10)

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09 08:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('equip', sa.Column('service', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        'equip_counter',
        sa.Column('equip_id', sa.UUID(), nullable=False),
        sa.Column('run_s', sa.Double(), server_default='0', nullable=False),
        sa.Column('starts', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('unseen_s', sa.Double(), server_default='0', nullable=False),
        sa.Column('last_state', sa.Boolean(), nullable=True),
        sa.Column('service_hours_mark', sa.Double(), server_default='0', nullable=False),
        sa.Column('service_starts_mark', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('serviced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('serviced_by', sa.String(length=64), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['equip_id'], ['equip.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('equip_id'),
    )


def downgrade() -> None:
    op.drop_table('equip_counter')
    op.drop_column('equip', 'service')
