"""add broker to accounts (Wealthsimple ledger accounts alongside Questrade)

Revision ID: b7c4e9a2d1f0
Revises: a1f2c3d4e5f6
Create Date: 2026-10-27 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c4e9a2d1f0'
down_revision: Union[str, None] = 'a1f2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('broker', sa.String(), nullable=False, server_default='questrade')
        )


def downgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('broker')
