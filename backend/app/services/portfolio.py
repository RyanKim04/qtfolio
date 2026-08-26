"""Live dashboard data — SPEC.md §8.1. Everything here reads Questrade live
(positions/balances/quotes) plus the local candle cache for sparklines; the
reconstructed history (§6) is M2 and never involved on this path.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import utcnow
from ..fx import FxService
from ..models import Account, Candle, Symbol
from ..questrade.client import QuestradeClient
from .quotes import QuoteCache

log = logging.getLogger(__name__)

SPARKLINE_DAYS = 30
CANDLE_FETCH_TTL = 30 * 60  # refetch a ticker's daily candles at most every 30 min


class PortfolioService:
    def __init__(self, sessions: async_sessionmaker, qt: QuestradeClient, quotes: QuoteCache, fx: FxService):
        self._sessions = sessions
        self._qt = qt
        self._quotes = quotes
        self._fx = fx
        self._candle_fetch_at: dict[str, float] = {}
        self._stats_cache: tuple[float, dict] | None = None

    # -- public ----------------------------------------------------------

    async def summary(self, currency: str) -> dict:
        accounts = await self._tracked_account_ids()
        if not accounts:
            return {"empty": True, "currency": currency, "as_of": utcnow().isoformat()}
        totals_now, totals_sod, cash_combined, cash_native = await self._combined_totals(accounts)
        positions = await self._all_positions(accounts)
        sym_currency = await self._ensure_symbols(positions)

        # Questrade's combinedBalances are flagged isRealTime: False (delayed
        # marks) — on fast days they lag the real-time positions by a lot. Build
        # the header from the same real-time components as the holdings table so
        # the two always agree; combined totals remain only as the fallback.
        open_pnl_native = {"CAD": 0.0, "USD": 0.0}
        mv_native = {"CAD": 0.0, "USD": 0.0}
        day_native = {"CAD": 0.0, "USD": 0.0}
        any_day = False
        for p in positions:
            cur = sym_currency.get(p.get("symbol"), "CAD")
            open_pnl_native[cur] = open_pnl_native.get(cur, 0.0) + (p.get("openPnl") or 0.0)
            mv_native[cur] = mv_native.get(cur, 0.0) + (p.get("currentMarketValue") or 0.0)
            if p.get("dayPnl") is not None:
                any_day = True
                day_native[cur] = day_native.get(cur, 0.0) + p["dayPnl"]

        rate = await self._rate(totals_now)
        if positions:
            total = self._to_display(
                {c: mv_native.get(c, 0.0) + cash_native.get(c, 0.0) for c in ("CAD", "USD")}, currency, rate
            )
        else:
            total = totals_now[currency]
        day_pnl = self._to_display(day_native, currency, rate) if any_day else total - totals_sod[currency]
        base = total - day_pnl
        stats = await self._header_stats()
        return {
            "empty": False,
            "currency": currency,
            "as_of": utcnow().isoformat(),
            "total_value": total,
            "day_pnl": day_pnl,
            "day_pnl_pct": (day_pnl / base * 100) if base else None,
            "open_pnl": self._to_display(open_pnl_native, currency, rate),
            "cash": cash_combined[currency],
            "mwr": stats["mwr"],
            "mwr_since": stats["mwr_since"],
            "twr_ytd": stats["twr_ytd"],
        }

    async def _header_stats(self) -> dict:
        """series_daily-backed stats, cached 5 min (recomputes are event-driven)."""
        from . import history

        now_ts = utcnow().timestamp()
        if self._stats_cache and now_ts - self._stats_cache[0] < 300:
            return self._stats_cache[1]
        try:
            stats = await history.header_stats(self._sessions)
        except Exception as exc:
            log.warning("header stats unavailable: %s", exc)
            stats = {"mwr": None, "mwr_since": None, "twr_ytd": None}
        self._stats_cache = (now_ts, stats)
        return stats

    async def holdings(self, currency: str) -> dict:
        accounts = await self._tracked_account_ids()
        if not accounts:
            return {"empty": True, "currency": currency, "rows": [], "as_of": utcnow().isoformat()}
        totals_now, _, _, cash_native = await self._combined_totals(accounts)
        positions = await self._all_positions(accounts)
        sym_currency = await self._ensure_symbols(positions)
        rate = await self._rate(totals_now)

        # Aggregate across accounts by ticker.
        agg: dict[str, dict] = {}
        for p in positions:
            t = p.get("symbol")
            a = agg.setdefault(
                t,
                {"ticker": t, "symbol_id": p.get("symbolId"), "qty": 0.0, "cost": 0.0,
                 "mv": 0.0, "open_pnl": 0.0, "day_pnl": None, "last": p.get("currentPrice")},
            )
            qty = p.get("openQuantity") or 0.0
            a["qty"] += qty
            a["cost"] += (p.get("averageEntryPrice") or 0.0) * qty
            a["mv"] += p.get("currentMarketValue") or 0.0
            a["open_pnl"] += p.get("openPnl") or 0.0
            if p.get("dayPnl") is not None:
                a["day_pnl"] = (a["day_pnl"] or 0.0) + p["dayPnl"]

        quotes = await self._quotes.get_many([a["symbol_id"] for a in agg.values() if a["symbol_id"]])
        sparklines = await self._sparklines({a["ticker"]: a["symbol_id"] for a in agg.values() if a["symbol_id"]})

        # Real-time total (positions + cash) — combined balances are delayed (see summary()).
        mv_native = {"CAD": 0.0, "USD": 0.0}
        for a in agg.values():
            cur = sym_currency.get(a["ticker"], "CAD")
            mv_native[cur] = mv_native.get(cur, 0.0) + a["mv"]
        total_display = (
            self._to_display({c: mv_native.get(c, 0.0) + cash_native.get(c, 0.0) for c in ("CAD", "USD")}, currency, rate)
            if agg
            else totals_now[currency]
        )
        rows = []
        for a in agg.values():
            cur = sym_currency.get(a["ticker"], "CAD")
            q = quotes.get(a["symbol_id"]) or {}
            last = q.get("lastTradePrice") or a["last"]
            day_pnl = a["day_pnl"]
            if day_pnl is None and last is not None and q.get("prevDayClosePrice"):
                day_pnl = (last - q["prevDayClosePrice"]) * a["qty"]
            mv_native = {"CAD": 0.0, "USD": 0.0, cur: a["mv"]}
            mv_disp = self._to_display(mv_native, currency, rate)
            rows.append({
                "ticker": a["ticker"],
                "currency": cur,  # per-share figures (avg cost, last) stay native
                "qty": a["qty"],
                "avg_cost": (a["cost"] / a["qty"]) if a["qty"] else None,
                "last": last,
                "market_value": mv_disp,
                # DECISION: weight = mv / total equity (incl. cash), so weights + cash sum to 100%.
                "weight": (mv_disp / total_display * 100) if total_display else None,
                "day_pnl": self._to_display({"CAD": 0.0, "USD": 0.0, cur: day_pnl}, currency, rate) if day_pnl is not None else None,
                "open_pnl": self._to_display({"CAD": 0.0, "USD": 0.0, cur: a["open_pnl"]}, currency, rate),
                "open_pnl_pct": (a["open_pnl"] / a["cost"] * 100) if a["cost"] else None,
                "sparkline": sparklines.get(a["ticker"], []),
            })
        rows.sort(key=lambda r: -(r["market_value"] or 0))
        return {
            "empty": False,
            "currency": currency,
            "as_of": utcnow().isoformat(),
            "total_equity": total_display,
            "cash_native": cash_native,
            "rows": rows,
        }

    async def allocation(self, by: str, currency: str) -> dict:
        if by == "sector":
            # DECISION: sector allocation needs yfinance sector caching (§8.1) — arrives with M5's analyst sync.
            return {"by": by, "available": False, "slices": []}
        h = await self.holdings(currency)
        if h.get("empty"):
            return {"by": by, "available": True, "slices": []}
        slices: dict[str, float] = {}
        for r in h["rows"]:
            # Values are in display currency; the "currency" grouping is by each
            # holding's native listing currency (exposure, not denomination).
            label = r["ticker"] if by == "position" else r["currency"]
            slices[label] = slices.get(label, 0.0) + (r["market_value"] or 0.0)
        # Cash slice keeps the donut summing to total equity.
        cash_value = h["total_equity"] - sum(slices.values())
        if cash_value > 0.005 * (h["total_equity"] or 1):
            slices["Cash"] = cash_value
        total = sum(slices.values()) or 1.0
        return {
            "by": by,
            "available": True,
            "currency": currency,
            "slices": [
                {"label": k, "value": v, "pct": v / total * 100}
                for k, v in sorted(slices.items(), key=lambda kv: -kv[1])
            ],
        }

    # -- internals -------------------------------------------------------

    async def _tracked_account_ids(self) -> list[str]:
        async with self._sessions() as session:
            rows = (await session.execute(select(Account).where(Account.tracked))).scalars().all()
        return [r.qt_account_id for r in rows]

    async def _combined_totals(self, account_ids: list[str]):
        balances = await asyncio.gather(*(self._qt.balances(a) for a in account_ids))
        totals_now = {"CAD": 0.0, "USD": 0.0}   # whole portfolio expressed in CAD / in USD
        totals_sod = {"CAD": 0.0, "USD": 0.0}
        cash_combined = {"CAD": 0.0, "USD": 0.0}  # total cash expressed in CAD / in USD
        cash_native = {"CAD": 0.0, "USD": 0.0}    # actual CAD cash and USD cash held
        for b in balances:
            for cb in b.get("combinedBalances", []):
                if cb.get("currency") in totals_now:
                    totals_now[cb["currency"]] += cb.get("totalEquity") or 0.0
                    cash_combined[cb["currency"]] += cb.get("cash") or 0.0
            for cb in b.get("sodCombinedBalances", []):
                if cb.get("currency") in totals_sod:
                    totals_sod[cb["currency"]] += cb.get("totalEquity") or 0.0
            for pcb in b.get("perCurrencyBalances", []):
                if pcb.get("currency") in cash_native:
                    cash_native[pcb["currency"]] += pcb.get("cash") or 0.0
        return totals_now, totals_sod, cash_combined, cash_native

    async def _all_positions(self, account_ids: list[str]) -> list[dict]:
        results = await asyncio.gather(*(self._qt.positions(a) for a in account_ids))
        return [p for chunk in results for p in chunk if (p.get("openQuantity") or 0) != 0]

    async def _ensure_symbols(self, positions: list[dict]) -> dict[str, str]:
        """Cache ticker → currency/exchange/description in `symbols`; return ticker → currency."""
        tickers = {p["symbol"]: p.get("symbolId") for p in positions if p.get("symbol")}
        async with self._sessions() as session:
            known = (
                (await session.execute(select(Symbol).where(Symbol.ticker.in_(tickers)))).scalars().all()
            )
            known_map = {s.ticker: s for s in known}
            missing_ids = [sid for t, sid in tickers.items() if sid and (t not in known_map or known_map[t].currency is None)]
            if missing_ids:
                try:
                    infos = await self._qt.symbols_by_ids(missing_ids)
                except Exception as exc:
                    log.warning("symbol resolution failed: %s", exc)
                    infos = []
                for info in infos:
                    t = info.get("symbol")
                    row = known_map.get(t) or await session.get(Symbol, t)
                    if row is None:
                        row = Symbol(ticker=t)
                        session.add(row)
                    row.qt_symbol_id = info.get("symbolId")
                    row.currency = info.get("currency")
                    row.exchange = info.get("listingExchange")
                    row.description = info.get("description")
                    known_map[t] = row
                await session.commit()
        # USD default for unresolved US-looking tickers would be a guess; default CAD-safe? Neither is
        # safe — but unresolved symbols are rare (resolution failed entirely). Default to CAD.
        return {t: (known_map[t].currency if t in known_map and known_map[t].currency else "CAD") for t in tickers}

    async def _rate(self, totals_now: dict | None) -> Decimal:
        """USDCAD for display conversion. DECISION: BoC latest first; fallback =
        implied rate from Questrade's own combined balances; 1.0 if no USD anywhere."""
        r = await self._fx.latest()
        if r is not None:
            return r[1]
        if totals_now and totals_now.get("USD"):
            return Decimal(str(totals_now["CAD"])) / Decimal(str(totals_now["USD"]))
        return Decimal(1)

    @staticmethod
    def _to_display(native: dict[str, float], currency: str, rate: Decimal) -> float:
        cad = native.get("CAD", 0.0)
        usd = native.get("USD", 0.0)
        if currency == "CAD":
            return cad + usd * float(rate)
        return usd + (cad / float(rate) if rate else 0.0)

    async def _sparklines(self, ticker_ids: dict[str, int]) -> dict[str, list[float]]:
        now_ts = utcnow().timestamp()
        to_fetch = {t: sid for t, sid in ticker_ids.items() if now_ts - self._candle_fetch_at.get(t, 0) > CANDLE_FETCH_TTL}
        if to_fetch:
            results = await asyncio.gather(
                *(self._fetch_daily_candles(t, sid) for t, sid in to_fetch.items()),
                return_exceptions=True,
            )
            for (t, _), res in zip(to_fetch.items(), results):
                if isinstance(res, Exception):
                    log.warning("candle fetch failed for %s: %s", t, res)
                self._candle_fetch_at[t] = now_ts

        out: dict[str, list[float]] = {}
        async with self._sessions() as session:
            for t in ticker_ids:
                closes = (
                    await session.execute(
                        select(Candle.c)
                        .where(Candle.ticker == t, Candle.interval == "OneDay", Candle.c.is_not(None))
                        .order_by(Candle.ts.desc())
                        .limit(SPARKLINE_DAYS)
                    )
                ).scalars().all()
                out[t] = list(reversed(closes))
        return out

    async def _fetch_daily_candles(self, ticker: str, symbol_id: int) -> None:
        from . import candles as candle_svc

        end = utcnow()
        start = end - timedelta(days=SPARKLINE_DAYS + 15)  # margin for weekends/holidays
        bars = await self._qt.candles(symbol_id, start, end, "OneDay")
        await candle_svc.upsert_bars(self._sessions, ticker, "OneDay", bars)
