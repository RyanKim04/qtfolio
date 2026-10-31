"""Stock detail data — SPEC.md §8.4 (minus S/R, owner-deferred §17).

Owned tickers get full treatment; any ticker works read-only (resolved on
Yahoo on first visit). Since the move to Wealthsimple every bar on this page is
yfinance (split-adjusted OHLC, regular session) — one source per chart (§16).
Intraday history is whatever Yahoo keeps: fetch what's available, don't fight it.
"""

import logging
from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..fx import FxService
from ..models import Activity, Candle, Symbol
from ..yf import YfService, to_yf_symbol
from . import indicators as ind
from .portfolio import PortfolioService
from .reconstruction import classify

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

QT_INTERVAL = {"5m": "FiveMinutes", "15m": "FifteenMinutes", "1h": "OneHour", "1d": "OneDay", "1w": "OneDay"}
INTRADAY_WINDOW_DAYS = {"FiveMinutes": 10, "FifteenMinutes": 30, "OneHour": 90}
DAILY_LOOKBACK_DAYS = 780  # ~2 years of trading days: 52w extremes + EMA200 warmup

SEMI_TICKERS = {"NVDA", "AMD", "SOXL", "SOXS", "SMH", "SOXX", "AVGO", "TSM", "MU", "INTC", "ASML", "AMAT"}


def default_benchmark(ticker: str) -> str:
    return "SMH" if ticker.upper() in SEMI_TICKERS else "QQQ"


class StockService:
    def __init__(self, sessions: async_sessionmaker, yf: YfService, fx: FxService, portfolio: PortfolioService):
        self._sessions = sessions
        self._yf = yf
        self._fx = fx
        self._portfolio = portfolio

    # -- symbols ----------------------------------------------------------

    async def resolve(self, ticker: str) -> Symbol | None:
        t = ticker.upper()
        async with self._sessions() as session:
            row = await session.get(Symbol, t)
        if row and row.currency:
            return row
        info = await self._yf.resolve(t)
        if not info:
            return row
        async with self._sessions() as session:
            row = await session.get(Symbol, t)
            if row is None:
                row = Symbol(ticker=t)
                session.add(row)
            row.currency = info["currency"]
            row.exchange = info["exchange"]
            row.description = info["description"]
            await session.commit()
            return row

    # -- candles ----------------------------------------------------------

    async def _ensure_candles(self, sym: Symbol, qt_interval: str) -> None:
        if qt_interval == "OneDay":
            start = date.today() - timedelta(days=DAILY_LOOKBACK_DAYS)
        else:
            start = date.today() - timedelta(days=INTRADAY_WINDOW_DAYS[qt_interval])
        await self._yf.refresh_bars(sym.ticker, to_yf_symbol(sym.ticker, sym.exchange), qt_interval, start)

    async def _load_bars(self, ticker: str, qt_interval: str) -> list[dict]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(Candle)
                    .where(Candle.ticker == ticker, Candle.interval == qt_interval, Candle.c.is_not(None))
                    .order_by(Candle.ts)
                )
            ).scalars().all()
        # Defense in depth: one bar per timestamp key (daily = date), last wins —
        # the chart library hard-asserts strictly ordered unique times.
        dedup: dict = {}
        for r in rows:
            key = r.ts.date() if qt_interval == "OneDay" else r.ts
            dedup[key] = {"ts": r.ts, "date": r.ts.date(), "o": r.o, "h": r.h, "l": r.l, "c": r.c, "v": r.v or 0}
        bars = [dedup[k] for k in sorted(dedup)]
        if qt_interval != "OneDay":
            # Regular session only (09:30–16:00 ET). Yahoo already omits
            # extended hours; kept as defense for Questrade-era cached bars, whose
            # hourly bars align to the top of the hour (09:00 carries the open).
            lower = 9 * 60 if qt_interval == "OneHour" else 9 * 60 + 30
            def in_session(b: dict) -> bool:
                et = b["ts"].replace(tzinfo=timezone.utc).astimezone(ET)
                minutes = et.hour * 60 + et.minute
                return lower <= minutes < 16 * 60
            bars = [b for b in bars if in_session(b)]
        return bars

    # -- payloads ---------------------------------------------------------

    async def candles_payload(self, ticker: str, interval: str) -> dict | None:
        sym = await self.resolve(ticker)
        if sym is None or not sym.currency:
            return None
        qt_interval = QT_INTERVAL[interval]
        await self._ensure_candles(sym, qt_interval)
        bars = await self._load_bars(sym.ticker, qt_interval)
        if interval == "1w":
            bars = ind.aggregate_weekly(bars)
        intraday = interval in ("5m", "15m", "1h")

        closes = [b["c"] for b in bars]
        lows = [b["l"] for b in bars]
        indicators: dict = {
            "ema20": ind.ema(closes, 20),
            "ema50": ind.ema(closes, 50),
            "ema200": ind.ema(closes, 200),
            "bb": ind.bollinger(closes),
            "rsi": ind.rsi(closes),
            "macd": ind.macd(closes),
        }
        if intraday:
            indicators["vwap"] = ind.session_vwap(
                bars, lambda b: b["ts"].replace(tzinfo=timezone.utc).astimezone(ET).date()
            )
        else:
            # Anchored VWAP preset: 52-week low (§8.4; click-to-anchor is §17).
            window = lows[-252:] if len(lows) > 252 else lows
            if window:
                anchor_idx = len(lows) - len(window) + window.index(min(window))
                indicators["avwap_52wlow"] = ind.anchored_vwap(bars, anchor_idx)
        crosses = ind.ema_crosses(indicators["ema50"], indicators["ema200"]) if not intraday else []

        def encode_t(b: dict):
            if intraday:
                # lightweight-charts renders raw epochs as UTC wall-clock; shift
                # to ET so the open bar reads 09:30, not 13:30.
                et = b["ts"].replace(tzinfo=timezone.utc).astimezone(ET)
                return int(et.replace(tzinfo=timezone.utc).timestamp())
            return b["date"].isoformat()

        times = [encode_t(b) for b in bars]
        return {
            "ticker": sym.ticker,
            "description": sym.description,
            "currency": sym.currency,
            "interval": interval,
            "intraday": intraday,
            "default_benchmark": default_benchmark(sym.ticker),
            "candles": [
                {"t": times[i], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
                for i, b in enumerate(bars)
            ],
            "indicators": indicators,
            "crosses": [{"t": times[c["index"]], "kind": c["kind"]} for c in crosses],
        }

    async def levels(self, ticker: str) -> dict | None:
        sym = await self.resolve(ticker)
        if sym is None or not sym.currency:
            return None
        await self._ensure_candles(sym, "OneDay")
        bars = await self._load_bars(sym.ticker, "OneDay")
        if not bars:
            return {"ticker": sym.ticker, "empty": True}
        closes = [b["c"] for b in bars]
        ext = ind.rolling_extremes([b["h"] for b in bars], [b["l"] for b in bars])
        atr14 = ind.atr([b["h"] for b in bars], [b["l"] for b in bars], closes)
        last = closes[-1]
        hi, lo = ext["high"][-1], ext["low"][-1]
        return {
            "ticker": sym.ticker,
            "currency": sym.currency,
            "last": last,
            "as_of": bars[-1]["date"].isoformat(),
            "week52_high": hi,
            "week52_low": lo,
            "dist_from_high_pct": (last / hi - 1) * 100 if hi else None,
            "dist_from_low_pct": (last / lo - 1) * 100 if lo else None,
            "atr14": atr14[-1],
            "atr14_pct": (atr14[-1] / last * 100) if atr14[-1] and last else None,
        }

    async def position(self, ticker: str, account_ids: list[str]) -> dict:
        t = ticker.upper()
        pos = await self._portfolio.positions()
        row = next((p for p in (pos or {}).get("rows", []) if p["ticker"] == t), None)
        qty = row["qty"] if row else 0.0
        mv = row["mv"] if row else 0.0
        cost = row["cost"] if row and row["cost"] is not None else 0.0
        open_pnl = mv - cost if row and row["cost"] is not None else None
        day_pnl = row["day_pnl"] if row else None
        total_cad = await self._portfolio.total_equity_cad() or 0.0

        async with self._sessions() as session:
            sym = await session.get(Symbol, t)
            acts = (
                await session.execute(
                    select(Activity)
                    .where(Activity.ticker == t, Activity.account_id.in_(account_ids))
                    .order_by(Activity.transaction_date)
                )
            ).scalars().all()
        markers = []
        for a in acts:
            if classify({"type": a.type, "action": a.action, "ticker": a.ticker, "qty_micro": a.qty_micro}) != "trade":
                continue
            q = (a.qty_micro or 0) / 1e6
            price = (a.price_micro or 0) / 1e6 or (abs((a.net_amount_cents or 0) / 100) / abs(q) if q else 0)
            markers.append({
                "date": (a.trade_date or a.transaction_date).isoformat(),
                "action": "buy" if (a.action or "") == "buy" else "sell",
                "qty": abs(q),
                "price": round(price, 4),
            })

        mv_cad = mv
        if sym and sym.currency == "USD":
            rate = await self._fx.latest()
            mv_cad = mv * float(rate[1]) if rate else mv
        return {
            "ticker": t,
            "owned": qty > 0,
            "qty": qty,
            "avg_cost": row["avg_cost"] if row else None,
            "market_value": mv,
            "currency": sym.currency if sym else None,
            "weight": (mv_cad / total_cad * 100) if total_cad and qty else None,
            "open_pnl": open_pnl if qty else None,
            "open_pnl_pct": (open_pnl / cost * 100) if open_pnl is not None and cost else None,
            "day_pnl": day_pnl,
            "markers": markers,
        }

    async def rs_series(self, ticker: str, benchmark: str) -> dict:
        """Relative strength: close(ticker) / close(benchmark), indexed to 100.
        Both legs from the same daily source — sources never mixed (§16)."""
        sym = await self.resolve(ticker)
        bench = await self.resolve(benchmark)
        if not sym or not bench or not sym.currency or not bench.currency:
            return {"empty": True}
        await self._ensure_candles(sym, "OneDay")
        await self._ensure_candles(bench, "OneDay")
        a = {b["date"]: b["c"] for b in await self._load_bars(sym.ticker, "OneDay")}
        b = {bb["date"]: bb["c"] for bb in await self._load_bars(bench.ticker, "OneDay")}
        common = sorted(set(a) & set(b))[-252:]
        if len(common) < 2:
            return {"empty": True}
        base = a[common[0]] / b[common[0]]
        return {
            "empty": False,
            "ticker": sym.ticker,
            "benchmark": bench.ticker,
            "dates": [d.isoformat() for d in common],
            "values": [round(a[d] / b[d] / base * 100, 3) for d in common],
        }
