"""M2 jobs — SPEC.md §13: eod_snapshot, activities_sync, db_backup, bootstrap.
(DECISION: one module instead of four files; each job is a small make_* factory.)
"""

import asyncio
import logging
import sqlite3
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import DATA_DIR, TZ, settings, utcnow
from ..fx import FxService
from ..models import Symbol
from ..questrade.client import QuestradeClient
from ..yf import YfService
from ..services import candles as candle_svc
from ..services import history
from ..services.activities import sync_recent
from .runner import Job

log = logging.getLogger(__name__)


def _last_market_close_utc(now_utc: datetime) -> datetime:
    """Most recent weekday 16:00 ET, as naive UTC. Stat holidays are ignored —
    a redundant snapshot on a holiday is harmless (DECISION)."""
    from datetime import timezone

    now_et = now_utc.replace(tzinfo=timezone.utc).astimezone(TZ)
    candidate = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if now_et < candidate:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:  # Sat/Sun
        candidate -= timedelta(days=1)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def make_eod_snapshot(sessions: async_sessionmaker, qt: QuestradeClient, fx: FxService, yf: YfService) -> Job:
    async def run() -> str:
        ids = await history.tracked_ids(sessions)
        if not ids:
            return "skipped: no tracked accounts"
        await history.take_snapshots(sessions, qt, ids)
        try:
            await fx.latest()
        except Exception as exc:
            log.warning("fx refresh failed: %s", exc)
        # Tail-refresh daily candles for current holdings + benchmark history.
        async with sessions() as session:
            held = list((await session.execute(select(Symbol).where(Symbol.qt_symbol_id.is_not(None)))).scalars())
        for s in held:
            try:
                await candle_svc.backfill_daily(qt, sessions, s.ticker, s.qt_symbol_id, date.today() - timedelta(days=10))
            except Exception as exc:
                log.warning("candle tail refresh failed for %s: %s", s.ticker, exc)
        for b in await history.get_setting(sessions, "benchmarks", history.DEFAULT_BENCHMARKS):
            await yf.refresh_daily_closes(b, date.today() - timedelta(days=10))
        result = await history.recompute(sessions, yf)
        return f"snapshot taken; series: {result}"

    def due(last_success, now_utc) -> bool:
        return last_success is None or last_success < _last_market_close_utc(now_utc)

    return Job(
        name="eod_snapshot",
        func=run,
        interval=timedelta(hours=20),
        cron={"day_of_week": "mon-fri", "hour": 17, "minute": 30},
        due=due,
    )


def make_activities_sync(sessions: async_sessionmaker, qt: QuestradeClient, yf: YfService) -> Job:
    async def run() -> str:
        ids = await history.tracked_ids(sessions)
        if not ids:
            return "skipped: no tracked accounts"
        new = await sync_recent(qt, sessions, ids)
        if new:
            await history.repair_missing_tickers(sessions, qt)
            await history.recompute(sessions, yf)
        return f"{new} new activity rows"

    return Job(
        name="activities_sync",
        func=run,
        interval=timedelta(hours=20),
        cron={"hour": 18, "minute": 0},
    )


def make_db_backup(sessions: async_sessionmaker) -> Job:
    KEEP = 8

    def backup_sync() -> str:
        backups = DATA_DIR / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        dst_path = backups / f"qtfolio-{date.today():%Y%m%d}.db"
        src = sqlite3.connect(settings.db_path)
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        existing = sorted(backups.glob("qtfolio-*.db"))
        for old in existing[:-KEEP]:
            old.unlink()
        return f"backed up to {dst_path.name}; keeping {min(len(existing), KEEP)}"

    async def run() -> str:
        return await asyncio.to_thread(backup_sync)

    return Job(
        name="db_backup",
        func=run,
        interval=timedelta(days=6, hours=12),
        cron={"day_of_week": "sun", "hour": 12, "minute": 0},
    )


def make_bootstrap(sessions: async_sessionmaker, qt: QuestradeClient, fx: FxService, yf: YfService) -> Job:
    async def run(progress) -> str:
        return await history.full_bootstrap(sessions, qt, fx, yf, progress=progress)

    # Manual-only: no cron, no catch-up (§13 — triggered from Settings).
    return Job(name="bootstrap", func=run, interval=None, cron=None, wants_progress=True)
