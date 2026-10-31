"""The full schema. Conventions:

- Money = INTEGER cents in the row's native currency; unit prices and share
  quantities = INTEGER micro-units (1e-6) where sub-cent / fractional precision
  matters. Never SQLAlchemy Numeric on SQLite (silent float round-trip).
- Datetimes stored naive UTC; dates are calendar dates in America/Toronto terms.
- Questrade (retired 2026-10-27) survives only as frozen history: its
  activities, balance/position snapshots and `accounts.broker = 'questrade'`.
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


class Account(Base):
    __tablename__ = "accounts"
    # DECISION: the PK keeps its historical name; for Wealthsimple accounts it
    # holds the WS account id (alphanumeric, not a QT number). Renaming it would touch every query.
    qt_account_id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    tracked: Mapped[bool] = mapped_column(default=True)
    # questrade = retired account, frozen snapshots are its ground truth;
    # wealthsimple = CSV ledger account (state derived from its activities).
    broker: Mapped[str] = mapped_column(String, default="questrade", server_default="questrade")


class Symbol(Base):
    __tablename__ = "symbols"
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    currency: Mapped[str | None] = mapped_column(String)
    exchange: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(String)
    is_benchmark: Mapped[bool] = mapped_column(default=False)


class Activity(Base):
    __tablename__ = "activities"
    # DECISION: qty and price in integer micro-units, because sub-cent precision
    # matters for fractional DRIP shares and sub-cent unit prices.
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


class RiskFreeRate(Base):
    __tablename__ = "risk_free_daily"
    # BoC 3-month T-bill mid yield (TB.CDN.90D.MID), INTEGER basis points: the
    # source publishes two decimals of a percent, so bp are exact.
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate_bp: Mapped[int] = mapped_column(Integer)


class Candle(Base):
    __tablename__ = "candles"
    # Chart series, so floats are fine here.
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
    # Cumulative CAD gain/loss from USD/CAD moves on USD exposure plus conversion
    # spread (since inception). Investment P&L = pnl_cad_cents − fx_gain_cad_cents.
    # See series.fx_translation_series and series.conversion_cost_series.
    fx_gain_cad_cents: Mapped[int | None] = mapped_column(Integer)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String)  # running | success | error
    detail: Mapped[str | None] = mapped_column(Text)


class TfsaTransaction(Base):
    """Hand-entered TFSA room ledger (services/tfsa.py) covering every institution the
    owner has used, independent of the broker activities above."""
    __tablename__ = "tfsa_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    institution: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # contribution | withdrawal | transfer
    amount_cad_cents: Mapped[int] = mapped_column(Integer)  # positive; `type` carries the direction
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


Index("ix_job_runs_name_started", JobRun.job_name, JobRun.started_at.desc())
