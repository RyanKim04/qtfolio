"""yfinance wrapper — SPEC.md §3/§16. yfinance is synchronous and unofficial:
every call runs in an executor, retries ×2, degrades to cached data, and never
crashes the app. Benchmark/comparison closes are auto-adjusted and cached in
`candles` under interval "1d_yf" — never mixed with Questrade candles on a chart.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import utcnow
from .models import Candle

log = logging.getLogger(__name__)

YF_INTERVAL = "1d_yf"


def to_yf_symbol(ticker: str, exchange: str | None) -> str:
    """Map a Questrade ticker to Yahoo's convention."""
    t = ticker.upper()
    if exchange in ("TSX", "TSE"):
        return t.replace(".", "-") + ".TO" if not t.endswith(".TO") else t
    if exchange in ("TSXV", "CDNX"):
        return t + ".V"
    return t.replace(".", "-")  # BRK.B -> BRK-B


def _retry_sync(fn, attempts: int = 3):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # yfinance raises all sorts
            last = exc
            log.warning("yfinance call failed (attempt %d/%d): %s", i + 1, attempts, exc)
    raise last


class YfService:
    def __init__(self, sessions: async_sessionmaker):
        self._sessions = sessions
        self._fetched_at: dict[str, float] = {}
        self._splits_cache: dict[str, dict[date, float]] = {}

    async def splits(self, yf_symbol: str) -> dict[date, float]:
        """date -> split factor (2.0 for 2:1 forward, 1/15 for a 1:15 reverse)."""
        if yf_symbol in self._splits_cache:
            return self._splits_cache[yf_symbol]

        def fetch():
            import yfinance as yf

            s = yf.Ticker(yf_symbol).splits
            return {ts.date(): float(v) for ts, v in s.items()} if s is not None else {}

        try:
            result = await asyncio.to_thread(_retry_sync, fetch)
        except Exception:
            log.warning("could not fetch splits for %s; assuming none", yf_symbol)
            result = {}
        self._splits_cache[yf_symbol] = result
        return result

    async def refresh_daily_closes(self, yf_symbol: str, start: date) -> None:
        """Fetch auto-adjusted daily closes into the candles cache (idempotent)."""
        now_ts = utcnow().timestamp()
        if now_ts - self._fetched_at.get(yf_symbol, 0) < 3600:
            return

        def fetch():
            import yfinance as yf

            df = yf.Ticker(yf_symbol).history(start=start.isoformat(), auto_adjust=True, interval="1d")
            out = []
            for ts, row in df.iterrows():
                out.append((ts.date(), float(row["Open"]), float(row["High"]), float(row["Low"]),
                            float(row["Close"]), int(row.get("Volume") or 0)))
            return out

        try:
            bars = await asyncio.to_thread(_retry_sync, fetch)
        except Exception as exc:
            log.warning("yfinance closes unavailable for %s (%s); serving cache", yf_symbol, exc)
            return
        self._fetched_at[yf_symbol] = now_ts
        if not bars:
            return
        async with self._sessions() as session:
            for d, o, h, l, c, v in bars:
                ts = datetime.combine(d, time.min)
                stmt = sqlite_insert(Candle).values(
                    ticker=yf_symbol, interval=YF_INTERVAL, ts=ts, o=o, h=h, l=l, c=c, v=v,
                ).on_conflict_do_update(
                    index_elements=["ticker", "interval", "ts"],
                    set_={"o": o, "h": h, "l": l, "c": c, "v": v},
                )
                await session.execute(stmt)
            await session.commit()

    async def daily_closes(self, yf_symbol: str, start: date) -> dict[date, float]:
        await self.refresh_daily_closes(yf_symbol, start - timedelta(days=7))
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(Candle.ts, Candle.c)
                    .where(Candle.ticker == yf_symbol, Candle.interval == YF_INTERVAL,
                           Candle.ts >= datetime.combine(start - timedelta(days=7), time.min))
                    .order_by(Candle.ts)
                )
            ).all()
        return {ts.date(): c for ts, c in rows if c is not None}
