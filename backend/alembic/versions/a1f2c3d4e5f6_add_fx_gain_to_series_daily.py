"""add fx_gain_cad_cents to series_daily

Revision ID: a1f2c3d4e5f6
Revises: 7383d26c1cae
Create Date: 2026-09-21 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1f2c3d4e5f6'
down_revision: Union[str, None] = '7383d26c1cae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('series_daily', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fx_gain_cad_cents', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('series_daily', schema=None) as batch_op:
        batch_op.drop_column('fx_gain_cad_cents')
