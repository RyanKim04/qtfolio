"""Deep daily-candle backfill from Questrade, chunked (§4.2: assume ≲2,000 bars
per request; we chunk conservatively at ~1 year)."""

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Candle
from ..questrade.client import QuestradeClient

log = logging.getLogger(__name__)

CHUNK_DAYS = 360


async def backfill_daily(
    qt: QuestradeClient, sessions: async_sessionmaker, ticker: str, symbol_id: int, start: date
) -> int:
    """Fetch [start, today] in chunks, skipping ranges already covered."""
    async with sessions() as session:
        row = (
            await session.execute(
                select(func.min(Candle.ts), func.max(Candle.ts)).where(
                    Candle.ticker == ticker, Candle.interval == "OneDay"
                )
            )
        ).one()
    have_min, have_max = row
    total = 0
    ranges: list[tuple[date, date]] = []
    today = date.today()
    if have_min is None:
        ranges.append((start, today))
    else:
        if start < have_min.date() - timedelta(days=4):
            ranges.append((start, have_min.date()))
        if have_max.date() < today:
            ranges.append((have_max.date() - timedelta(days=2), today))
    for r_start, r_end in ranges:
        chunk_start = r_start
        while chunk_start <= r_end:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), r_end)
            s = datetime.combine(chunk_start, time.min, tzinfo=timezone.utc)
            e = datetime.combine(chunk_end, time.max.replace(microsecond=0), tzinfo=timezone.utc)
            bars = await qt.candles(symbol_id, s, e, "OneDay")
            total += await _upsert(sessions, ticker, bars)
            chunk_start = chunk_end + timedelta(days=1)
    return total


async def _upsert(sessions: async_sessionmaker, ticker: str, bars: list[dict]) -> int:
    return await upsert_bars(sessions, ticker, "OneDay", bars)


async def upsert_bars(sessions: async_sessionmaker, ticker: str, interval: str, bars: list[dict]) -> int:
    if not bars:
        return 0
    async with sessions() as session:
        for bar in bars:
            start = datetime.fromisoformat(bar["start"])
            if interval == "OneDay":
                # Daily bars are keyed by their exchange-local DATE at midnight —
                # raw start timestamps vary per request (chunk boundaries clamp to
                # the requested time; the in-progress bar restamps every fetch)
                # and would duplicate the same trading day.
                ts = datetime.combine(start.date(), time.min)
            else:
                ts = start.astimezone(timezone.utc).replace(tzinfo=None)
            stmt = sqlite_insert(Candle).values(
                ticker=ticker, interval=interval, ts=ts,
                o=bar.get("open"), h=bar.get("high"), l=bar.get("low"),
                c=bar.get("close"), v=bar.get("volume"),
            ).on_conflict_do_update(
                index_elements=["ticker", "interval", "ts"],
                set_={"o": bar.get("open"), "h": bar.get("high"), "l": bar.get("low"),
                      "c": bar.get("close"), "v": bar.get("volume")},
            )
            await session.execute(stmt)
        await session.commit()
    return len(bars)


async def daily_closes_cents(sessions: async_sessionmaker, ticker: str) -> dict[date, int]:
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Candle.ts, Candle.c)
                .where(Candle.ticker == ticker, Candle.interval == "OneDay", Candle.c.is_not(None))
                .order_by(Candle.ts)
            )
        ).all()
    return {ts.date(): round(c * 100) for ts, c in rows}
