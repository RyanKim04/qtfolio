"""yfinance wrapper — SPEC.md §3/§16. yfinance is synchronous and unofficial:
every call runs in an executor, retries ×2, degrades to cached data, and never
crashes the app. Benchmark/comparison closes are auto-adjusted and cached in
`candles` under interval "1d_yf".

Since the move to Wealthsimple (2026-10-27) yfinance is also the market-data
source for everything Questrade used to serve: OHLC bars (split-adjusted, NOT
dividend-adjusted — the portfolio keeps dividends as cash) cached under the
app ticker with the legacy interval names (OneDay/OneHour/…), live quotes, and
symbol resolution. Split-adjusted closes ⇒ the back-adjusted regime (§6.0) by
construction.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import TZ, utcnow
from .models import Candle

log = logging.getLogger(__name__)

YF_INTERVAL = "1d_yf"


def to_yf_symbol(ticker: str, exchange: str | None) -> str:
    """Map an app (Questrade-convention) ticker to Yahoo's convention."""
    t = ticker.upper()
    if exchange in ("TSX", "TSE"):
        return t.replace(".", "-") + ".TO" if not t.endswith(".TO") else t
    if exchange == "NEO":  # Cboe Canada (CDRs) — Yahoo suffix .NE
        return t.removesuffix(".TO").replace(".", "-") + ".NE"
    if exchange in ("TSXV", "CDNX"):
        return t + ".V"
    if t.endswith(".TO"):  # already Yahoo-form (app convention for CAD listings)
        return t
    return t.replace(".", "-")  # BRK.B -> BRK-B


# Yahoo exchange codes → the exchange names the symbols table already uses.
_YF_EXCHANGE = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "AMEX",
    "PCX": "ARCA", "BTS": "BATS", "TOR": "TSX", "NEO": "NEO", "VAN": "TSXV",
}

# Legacy (Questrade) interval names the candles table and charts are keyed by.
YF_BAR_INTERVAL = {"OneDay": "1d", "OneHour": "60m", "FifteenMinutes": "15m", "FiveMinutes": "5m"}
QUOTE_TTL = 15  # pre-market → after-hours: prices move, so the dashboard ticks every ~15 s
QUOTE_TTL_CLOSED = 300  # overnight and weekends nothing trades on Yahoo's feed


def quotes_live(now: datetime | None = None) -> bool:
    """Inside Yahoo's US trading window — pre-market 04:00 through after-hours
    20:00 ET, weekdays (holidays not modelled; they just poll for nothing)."""
    et = (now or datetime.now(timezone.utc)).astimezone(TZ)
    return et.weekday() < 5 and time(4) <= et.time() < time(20)
BAR_TTL = {"OneDay": 30 * 60, "OneHour": 5 * 60, "FifteenMinutes": 5 * 60, "FiveMinutes": 5 * 60}


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
        self._bars_at: dict[tuple[str, str], float] = {}
        self._quotes: dict[str, tuple[dict, float]] = {}
        # Polled baselines per symbol: (last regular close, previous close) — the
        # day-change bases a streamed tick is measured against.
        self._bases: dict[str, tuple[float | None, float | None]] = {}
        # Yahoo streamer (the feed Yahoo's own pages tick from): ~1–3 s behind the
        # tape vs ~1–2 min for the polled quoteSummary, which can also freeze on a
        # symbol for tens of minutes. Newest wins; polling stays the fallback.
        self._ticks: dict[str, dict] = {}
        self._stream_syms: set[str] = set()
        self._stream_wanted = asyncio.Event()
        self._ws = None

    async def resolve(self, ticker: str) -> dict | None:
        """Identify an app ticker on Yahoo: {yf_symbol, currency, exchange,
        description}, or None. Canadian listings are probed .TO → .NE → .V
        (CDRs trade on Cboe Canada, Yahoo .NE)."""
        t = ticker.upper()
        if t.endswith(".TO"):
            base = t.removesuffix(".TO")
            probes = [t, base + ".NE", base + ".V"]
        else:
            probes = [t.replace(".", "-")]

        def fetch():
            import yfinance as yf

            for sym in probes:
                try:
                    tk = yf.Ticker(sym)
                    fi = tk.fast_info
                    if not fi.last_price:
                        continue
                    try:
                        name = tk.info.get("longName") or tk.info.get("shortName")
                    except Exception:
                        name = None
                    return {"yf_symbol": sym, "currency": fi.currency,
                            "exchange": _YF_EXCHANGE.get(fi.exchange, fi.exchange), "description": name or t}
                except Exception:
                    continue
            return None

        try:
            return await asyncio.to_thread(fetch)
        except Exception as exc:
            log.warning("yfinance resolve failed for %s: %s", t, exc)
            return None

    async def refresh_bars(self, ticker: str, yf_symbol: str, interval: str, start: date) -> int:
        """Fetch OHLCV into `candles` under the APP ticker and legacy interval
        name (idempotent upsert, TTL-throttled). Daily bars are split-adjusted
        only (auto_adjust=False) and re-fetched over the whole range each time,
        so a new split re-adjusts history instead of leaving a seam. Intraday is
        regular-session only (Yahoo's prepost=False) and bounded by Yahoo's
        lookback (5m/15m ≈ 60 days, 60m ≈ 730 days)."""
        key = (ticker, interval)
        now_ts = utcnow().timestamp()
        if now_ts - self._bars_at.get(key, 0) < BAR_TTL[interval]:
            return 0
        yf_interval = YF_BAR_INTERVAL[interval]
        if interval == "OneDay":
            # One TTL per (ticker, interval) — fetch a range wide enough for every
            # caller (stock page wants ~2y for EMA200/52w; valuation wants inception).
            start = min(start, date.today() - timedelta(days=800))
        else:
            start = max(start, date.today() - timedelta(days=59 if yf_interval in ("5m", "15m") else 729))

        def fetch():
            import yfinance as yf

            df = yf.Ticker(yf_symbol).history(
                start=start.isoformat(), interval=yf_interval, auto_adjust=False, actions=False
            )
            out = []
            for ts, row in df.iterrows():
                if interval == "OneDay":
                    key_ts = datetime.combine(ts.date(), time.min)
                else:
                    key_ts = ts.tz_convert("UTC").to_pydatetime().replace(tzinfo=None)
                out.append((key_ts, float(row["Open"]), float(row["High"]), float(row["Low"]),
                            float(row["Close"]), int(row.get("Volume") or 0)))
            return out

        try:
            bars = await asyncio.to_thread(_retry_sync, fetch)
        except Exception as exc:
            log.warning("yfinance %s bars unavailable for %s (%s); serving cache", interval, yf_symbol, exc)
            return 0
        self._bars_at[key] = now_ts
        if not bars:
            return 0
        async with self._sessions() as session:
            if interval != "OneDay":
                # Replace the fetched span outright: Questrade-era hourly bars sit
                # on the hour, Yahoo's on the half hour — upserting alone would
                # interleave two grids on one chart.
                await session.execute(delete(Candle).where(
                    Candle.ticker == ticker, Candle.interval == interval,
                    Candle.ts >= bars[0][0], Candle.ts <= bars[-1][0],
                ))
            for ts, o, h, l, c, v in bars:
                stmt = sqlite_insert(Candle).values(
                    ticker=ticker, interval=interval, ts=ts, o=o, h=h, l=l, c=c, v=v,
                ).on_conflict_do_update(
                    index_elements=["ticker", "interval", "ts"],
                    set_={"o": o, "h": h, "l": l, "c": c, "v": v},
                )
                await session.execute(stmt)
            await session.commit()
        return len(bars)

    async def quotes(self, yf_symbols: list[str]) -> dict[str, dict]:
        """{yf_symbol: {"last", "prev_close", "session", "time"}} — cached QUOTE_TTL
        while US trading can move prices, QUOTE_TTL_CLOSED otherwise; failures
        serve the cached quote (or omit the symbol) so the dashboard never 500s.
        `session` is "post"/"pre" when `last` is an extended-hours trade, else None;
        `time` is that trade's epoch seconds."""
        now_ts = utcnow().timestamp()
        ttl = QUOTE_TTL if quotes_live() else QUOTE_TTL_CLOSED
        stale = [s for s in yf_symbols if now_ts - self._quotes.get(s, ({}, 0))[1] > ttl]

        def fetch(sym: str) -> dict | None:
            import yfinance as yf

            tk = yf.Ticker(sym)
            try:
                info = tk.info
            except Exception as exc:  # quoteSummary is flakier than the chart API fast_info uses
                log.info("quote info unavailable for %s (%s); regular session only", sym, exc)
                info = {}
            reg, reg_t = info.get("regularMarketPrice"), info.get("regularMarketTime") or 0
            prev = info.get("regularMarketPreviousClose")
            if reg:
                self._bases[sym] = (float(reg), float(prev) if prev else None)
            if not reg:
                fi = tk.fast_info
                if not fi.last_price:
                    return None
                return {"last": float(fi.last_price),
                        "prev_close": float(fi.regular_market_previous_close or 0) or None,
                        "session": None, "time": None}
            # DECISION: the newest extended-hours trade after the regular close wins.
            # Post-market extends today's move (base = previous close); pre-market
            # starts the next day's (base = the last regular close). TSX listings
            # have no extended session on Yahoo, so they stay regular.
            ext = [
                (info.get(f"{s}MarketTime") or 0, info.get(f"{s}MarketPrice"), base, s)
                for s, base in (("post", prev), ("pre", reg))
            ]
            ext = [e for e in ext if e[1] and e[0] > reg_t]
            if ext:
                t, price, base, session = max(ext, key=lambda e: e[0])
                return {"last": float(price), "prev_close": float(base or 0) or None, "session": session, "time": t}
            return {"last": float(reg), "prev_close": float(prev or 0) or None, "session": None, "time": reg_t or None}

        await self._subscribe(yf_symbols)
        if stale:
            results = await asyncio.gather(*(asyncio.to_thread(fetch, s) for s in stale), return_exceptions=True)
            for sym, res in zip(stale, results):
                if isinstance(res, Exception) or res is None:
                    log.warning("quote unavailable for %s: %s", sym, res)
                    continue
                self._quotes[sym] = (res, now_ts)
        out = {}
        for s in yf_symbols:
            q = self._quotes[s][0] if s in self._quotes else None
            q = self._with_tick(s, q)
            if q is not None:
                out[s] = q
        return out

    # -- live stream -----------------------------------------------------

    def _with_tick(self, sym: str, q: dict | None) -> dict | None:
        """Overlay the streamed trade when it is newer than the polled quote.
        Bases follow the polled rules: regular/post-market ticks move from the
        previous close, pre-market ticks from the last regular close."""
        tick = self._ticks.get(sym)
        session = {0: "pre", 1: None, 2: "post"}.get(tick["hours"], "?") if tick else "?"
        if tick is None or session == "?" or (q is not None and tick["time"] <= (q.get("time") or 0)):
            return q
        reg, prev = self._bases.get(sym, (None, (q or {}).get("prev_close")))
        if session is None and prev is None and tick["change"] is not None:
            prev = tick["price"] - tick["change"]
        base = reg if session == "pre" else prev
        return {"last": tick["price"], "prev_close": base, "session": session, "time": tick["time"]}

    def _on_tick(self, msg: dict) -> None:
        sym, price = msg.get("id"), msg.get("price")
        if not sym or not price:
            return
        change = msg.get("change")
        self._ticks[sym] = {"price": float(price), "time": int(msg.get("time") or 0) // 1000,
                            "hours": msg.get("market_hours"), "change": float(change) if change is not None else None}

    async def _subscribe(self, yf_symbols: list[str]) -> None:
        new = set(yf_symbols) - self._stream_syms
        if not new:
            return
        self._stream_syms |= new
        self._stream_wanted.set()
        if self._ws is not None:
            try:
                await self._ws.subscribe(sorted(new))
            except Exception as exc:  # the reconnect loop resubscribes everything
                log.info("stream subscribe failed for %s: %s", sorted(new), exc)

    async def stream_forever(self) -> None:
        """Hold one Yahoo streamer socket for every symbol quotes() has been asked
        about; reconnects (resubscribing all) whenever it drops."""
        import yfinance as yf

        await self._stream_wanted.wait()
        while True:
            ws = None
            try:
                ws = yf.AsyncWebSocket(verbose=False)
                await ws.subscribe(sorted(self._stream_syms))
                self._ws = ws
                await ws.listen(self._on_tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("quote stream dropped (%s); reconnecting", exc)
            finally:
                self._ws = None
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
            await asyncio.sleep(5)

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
