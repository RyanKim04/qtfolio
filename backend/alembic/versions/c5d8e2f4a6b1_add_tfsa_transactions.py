"""add tfsa_transactions (hand-entered TFSA contribution-room ledger)

Revision ID: c5d8e2f4a6b1
Revises: b7c4e9a2d1f0
Create Date: 2026-10-29 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d8e2f4a6b1'
down_revision: Union[str, None] = 'b7c4e9a2d1f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tfsa_transactions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('institution', sa.String(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('amount_cad_cents', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_tfsa_transactions_date', 'tfsa_transactions', ['date'])


def downgrade() -> None:
    op.drop_index('ix_tfsa_transactions_date', table_name='tfsa_transactions')
    op.drop_table('tfsa_transactions')
