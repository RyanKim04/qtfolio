"""add risk_free_daily (BoC 3-month T-bill) for risk-adjusted performance

Revision ID: e3a7c9b1d5f2
Revises: d9e1f3a5b7c2
Create Date: 2026-10-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3a7c9b1d5f2'
down_revision: Union[str, None] = 'd9e1f3a5b7c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'risk_free_daily',
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('rate_bp', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('date'),
    )


def downgrade() -> None:
    op.drop_table('risk_free_daily')
