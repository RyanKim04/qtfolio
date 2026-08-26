"""Full SPEC.md §5 schema. Conventions (§2, §5):

- Money = INTEGER cents in the row's native currency; unit prices and share
  quantities = INTEGER micro-units (1e-6) where sub-cent / fractional precision
  matters. Never SQLAlchemy Numeric on SQLite (silent float round-trip).
- Datetimes stored naive UTC; dates are calendar dates in America/Toronto terms.
- The whole schema ships in the initial migration; M1 only writes settings,
  qt_tokens, accounts, symbols, candles, fx_rates, job_runs.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)


class QtToken(Base):
    __tablename__ = "qt_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # single row, id=1
    refresh_token: Mapped[str | None] = mapped_column(Text)
    access_token: Mapped[str | None] = mapped_column(Text)
    api_server: Mapped[str | None] = mapped_column(String)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, default="absent")  # absent | ok | broken
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Account(Base):
    __tablename__ = "accounts"
    qt_account_id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    tracked: Mapped[bool] = mapped_column(default=True)


class Symbol(Base):
    __tablename__ = "symbols"
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    qt_symbol_id: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String)
    exchange: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(String)
    is_benchmark: Mapped[bool] = mapped_column(default=False)


class Activity(Base):
    __tablename__ = "activities"
    # DECISION: qty/price in integer micro-units (spec §5 allows micro-units where
    # sub-cent precision matters — fractional DRIP shares, sub-cent unit prices).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, index=True)
    hash: Mapped[str] = mapped_column(String, unique=True)
    occurrence_idx: Mapped[int] = mapped_column(Integer, default=0)
    trade_date: Mapped[date | None] = mapped_column(Date)
    transaction_date: Mapped[date | None] = mapped_column(Date, index=True)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    type: Mapped[str] = mapped_column(String)
    action: Mapped[str | None] = mapped_column(String)
    ticker: Mapped[str | None] = mapped_column(String, index=True)
    qty_micro: Mapped[int | None] = mapped_column(Integer)
    price_micro: Mapped[int | None] = mapped_column(Integer)
    gross_amount_cents: Mapped[int | None] = mapped_column(Integer)
    commission_cents: Mapped[int | None] = mapped_column(Integer)
    net_amount_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    qty_micro: Mapped[int] = mapped_column(Integer)
    price_micro: Mapped[int | None] = mapped_column(Integer)
    market_value_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String)


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    currency: Mapped[str] = mapped_column(String, primary_key=True)
    cash_cents: Mapped[int | None] = mapped_column(Integer)
    market_value_cents: Mapped[int | None] = mapped_column(Integer)
    total_equity_cents: Mapped[int | None] = mapped_column(Integer)


class FxRate(Base):
    __tablename__ = "fx_rates"
    # DECISION: rate stored as INTEGER micro-units (USDCAD × 1e6) so ledger
    # conversions stay exact integer/Decimal math.
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    usdcad_micro: Mapped[int] = mapped_column(Integer)


class Candle(Base):
    __tablename__ = "candles"
    # Chart series — floats are fine here per spec §2.
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    interval: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    o: Mapped[float | None] = mapped_column(Float)
    h: Mapped[float | None] = mapped_column(Float)
    l: Mapped[float | None] = mapped_column(Float)
    c: Mapped[float | None] = mapped_column(Float)
    v: Mapped[int | None] = mapped_column(Integer)


class HoldingsLedgerRow(Base):
    __tablename__ = "holdings_ledger"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    qty_micro: Mapped[int] = mapped_column(Integer)


class CashLedgerRow(Base):
    __tablename__ = "cash_ledger"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String, primary_key=True)
    balance_cents: Mapped[int] = mapped_column(Integer)


class SeriesDaily(Base):
    __tablename__ = "series_daily"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    portfolio_value_cad_cents: Mapped[int | None] = mapped_column(Integer)
    capital_in_cad_cents: Mapped[int | None] = mapped_column(Integer)
    pnl_cad_cents: Mapped[int | None] = mapped_column(Integer)
    twr_index: Mapped[float | None] = mapped_column(Float)
    flows_cad_cents: Mapped[int | None] = mapped_column(Integer)


class AnalystSnapshot(Base):
    __tablename__ = "analyst_snapshots"
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    target_low_cents: Mapped[int | None] = mapped_column(Integer)
    target_mean_cents: Mapped[int | None] = mapped_column(Integer)
    target_high_cents: Mapped[int | None] = mapped_column(Integer)
    n_analysts: Mapped[int | None] = mapped_column(Integer)
    rec_mean: Mapped[float | None] = mapped_column(Float)
    rec_counts_json: Mapped[str | None] = mapped_column(Text)


class AnalystAction(Base):
    __tablename__ = "analyst_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[date | None] = mapped_column(Date)
    firm: Mapped[str | None] = mapped_column(String)
    action: Mapped[str | None] = mapped_column(String)
    from_grade: Mapped[str | None] = mapped_column(String)
    to_grade: Mapped[str | None] = mapped_column(String)
    pt_old_cents: Mapped[int | None] = mapped_column(Integer)
    pt_new_cents: Mapped[int | None] = mapped_column(Integer)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String)  # running | success | error
    detail: Mapped[str | None] = mapped_column(Text)


Index("ix_job_runs_name_started", JobRun.job_name, JobRun.started_at.desc())
