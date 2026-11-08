"""Haystack API client accounts (milestone 12a-Haystack)

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-08 09:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('app_user', sa.Column('api_client', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('app_user', sa.Column('scram', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('app_user', 'scram')
    op.drop_column('app_user', 'api_client')
