"""drop Questrade API state and the unused analyst tables

Questrade is retired (2026-10-27): its token table, symbol ids and API-era
settings go; its activities and balance/position snapshots stay as history.
The M5 analyst tables were never written.

Revision ID: d9e1f3a5b7c2
Revises: c5d8e2f4a6b1
Create Date: 2026-10-30 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9e1f3a5b7c2'
down_revision: Union[str, None] = 'c5d8e2f4a6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STALE_SETTINGS = ("qt_retired", "snapshot_meta", "candle_adjustment_probe", "candle_adjustment_regime",
                  "investor_profile", "ai_last_analysis")
STALE_JOBS = ("token_keepalive", "activities_sync", "bootstrap")


def upgrade() -> None:
    op.drop_table('qt_tokens')
    with op.batch_alter_table('analyst_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_analyst_actions_ticker'))
    op.drop_table('analyst_actions')
    op.drop_table('analyst_snapshots')
    with op.batch_alter_table('symbols', schema=None) as batch_op:
        batch_op.drop_column('qt_symbol_id')
    settings = sa.table('settings', sa.column('key', sa.String))
    op.execute(settings.delete().where(settings.c.key.in_(STALE_SETTINGS)))
    job_runs = sa.table('job_runs', sa.column('job_name', sa.String))
    op.execute(job_runs.delete().where(job_runs.c.job_name.in_(STALE_JOBS)))


def downgrade() -> None:
    with op.batch_alter_table('symbols', schema=None) as batch_op:
        batch_op.add_column(sa.Column('qt_symbol_id', sa.Integer(), nullable=True))
    op.create_table('analyst_snapshots',
    sa.Column('ticker', sa.String(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('target_low_cents', sa.Integer(), nullable=True),
    sa.Column('target_mean_cents', sa.Integer(), nullable=True),
    sa.Column('target_high_cents', sa.Integer(), nullable=True),
    sa.Column('n_analysts', sa.Integer(), nullable=True),
    sa.Column('rec_mean', sa.Float(), nullable=True),
    sa.Column('rec_counts_json', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('ticker', 'date', 'source')
    )
    op.create_table('analyst_actions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ticker', sa.String(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('firm', sa.String(), nullable=True),
    sa.Column('action', sa.String(), nullable=True),
    sa.Column('from_grade', sa.String(), nullable=True),
    sa.Column('to_grade', sa.String(), nullable=True),
    sa.Column('pt_old_cents', sa.Integer(), nullable=True),
    sa.Column('pt_new_cents', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('analyst_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_analyst_actions_ticker'), ['ticker'], unique=False)
    op.create_table('qt_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('refresh_token', sa.Text(), nullable=True),
    sa.Column('access_token', sa.Text(), nullable=True),
    sa.Column('api_server', sa.String(), nullable=True),
    sa.Column('access_expires_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
