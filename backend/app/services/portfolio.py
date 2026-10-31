"""Dashboard data, rebuilt on the reconstructed ledger after the
move to Wealthsimple (2026-10-27): there is no live positions/balances API any
more, so holdings and cash are the current book (services/book.py), prices are
yfinance quotes (60 s cache) and sparklines the cached daily closes.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import TZ, utcnow
from ..fx import FxService
from ..models import Account, Activity, Candle, Symbol
from ..yf import YfService, quotes_live, to_yf_symbol
from .book import average_costs, current_book
from .reconstruction import classify, signed_qty_micro

log = logging.getLogger(__name__)

SPARKLINE_DAYS = 30
COST_TTL = 60


def _iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _session_date(epoch: int | None) -> date:
    """The trading day a quote belongs to (ET); a fallback close counts as today."""
    if epoch is None:
        return utcnow().replace(tzinfo=timezone.utc).astimezone(TZ).date()
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(TZ).date()


def fill_aware_day_pnl(last: float, prev: float, qty: float, trades: list[tuple[float, float]]) -> float:
    """Day P&L of a position whose session trades are (signed qty, fill price):
    shares held at the previous close move from `prev`, shares traded today from
    their fill — last·qty_now − prev·qty_at_open − net cash spent on today's trades."""
    qty_open = qty - sum(q for q, _ in trades)
    return last * qty - prev * qty_open - sum(q * p for q, p in trades)


class PortfolioService:
    def __init__(self, sessions: async_sessionmaker, yf: YfService, fx: FxService):
        self._sessions = sessions
        self._yf = yf
        self._fx = fx
        self._cost_cache: tuple[float, dict] | None = None

    # -- public ----------------------------------------------------------

    async def summary(self, currency: str) -> dict:
        pos = await self.positions()
        if pos is None:
            return {"empty": True, "currency": currency, "as_of": utcnow().isoformat()}
        rate = await self._rate()
        mv, day, open_native = {"CAD": 0.0, "USD": 0.0}, {"CAD": 0.0, "USD": 0.0}, {"CAD": 0.0, "USD": 0.0}
        for p in pos["rows"]:
            mv[p["currency"]] += p["mv"]
            if p["cost"] is not None:  # open P&L only over positions with a known basis
                open_native[p["currency"]] += p["mv"] - p["cost"]
            if p["day_pnl"] is not None:
                day[p["currency"]] += p["day_pnl"]
        cash = pos["cash"]
        total = self._to_display({c: mv[c] + cash[c] for c in mv}, currency, rate)
        day_pnl = self._to_display(day, currency, rate)
        # Opening value + the session's deposits (start-of-day flows) — the same
        # denominator as the TWR day return in series.twr_index.
        base = total - day_pnl
        stats = await self._header_stats(self._to_display({c: mv[c] + cash[c] for c in mv}, "CAD", rate))
        newest = max((p for p in pos["rows"] if p["price_time"]), key=lambda p: p["price_time"], default=None)
        return {
            "empty": False,
            "currency": currency,
            "as_of": utcnow().isoformat(),
            "total_value": total,
            "day_pnl": day_pnl,
            "day_pnl_pct": (day_pnl / base * 100) if base else None,
            "extended_hours": any(p["session"] for p in pos["rows"]),
            "prices_as_of": _iso_utc(newest["price_time"]) if newest else None,
            "price_session": newest["session"] if newest else None,
            "quotes_live": quotes_live(),  # inside the US pre-market → after-hours window
            "open_pnl": self._to_display(open_native, currency, rate),
            "cash": self._to_display(cash, currency, rate),
            "mwr": stats["mwr"],
            "mwr_since": stats["mwr_since"],
            "twr_ytd": stats["twr_ytd"],
            "freshness": await self.freshness(),
        }

    async def holdings(self, currency: str) -> dict:
        pos = await self.positions()
        if pos is None:
            return {"empty": True, "currency": currency, "rows": [], "as_of": utcnow().isoformat()}
        rate = await self._rate()
        sparklines = await self._sparklines([p["ticker"] for p in pos["rows"]])
        cash = pos["cash"]
        mv_native = {"CAD": 0.0, "USD": 0.0}
        for p in pos["rows"]:
            mv_native[p["currency"]] += p["mv"]
        total_display = self._to_display({c: mv_native[c] + cash[c] for c in mv_native}, currency, rate)

        def disp(cur: str, v: float) -> float:
            return self._to_display({"CAD": 0.0, "USD": 0.0, cur: v}, currency, rate)

        rows = []
        for p in pos["rows"]:
            mv_disp = disp(p["currency"], p["mv"])
            open_pnl = p["mv"] - p["cost"] if p["cost"] is not None else None
            rows.append({
                "ticker": p["ticker"],
                "currency": p["currency"],  # per-share figures (avg cost, last) stay native
                "qty": p["qty"],
                "avg_cost": p["avg_cost"],
                "last": p["last"],
                "session": p["session"],
                "price_time": _iso_utc(p["price_time"]) if p["price_time"] else None,
                "market_value": mv_disp,
                # DECISION: weight = mv / total equity (incl. cash), so weights + cash sum to 100%.
                "weight": (mv_disp / total_display * 100) if total_display else None,
                "day_pnl": disp(p["currency"], p["day_pnl"]) if p["day_pnl"] is not None else None,
                "open_pnl": disp(p["currency"], open_pnl) if open_pnl is not None else None,
                "open_pnl_pct": (open_pnl / p["cost"] * 100) if open_pnl is not None and p["cost"] else None,
                "sparkline": sparklines.get(p["ticker"], []),
            })
        rows.sort(key=lambda r: -(r["market_value"] or 0))
        return {
            "empty": False,
            "currency": currency,
            "as_of": utcnow().isoformat(),
            "total_equity": total_display,
            "cash_native": cash,
            "cash_weight": (self._to_display(cash, currency, rate) / total_display * 100) if total_display else None,
            "rows": rows,
        }

    async def allocation(self, by: str, currency: str) -> dict:
        if by == "sector":
            # DECISION: sector allocation needs yfinance sector caching — arrives with M5's analyst sync.
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
        # Cash slice keeps the donut summing to total equity — shown however small
        # (a 0.3% sliver still answers "how much is in cash?").
        cash_value = h["total_equity"] - sum(slices.values())
        if cash_value >= 0.01:
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

    async def positions(self) -> dict | None:
        """Current book priced live: rows of {ticker, currency, qty, avg_cost,
        last, session, price_time, prev_close, mv, cost, day_pnl} (native currency) + native cash."""
        book = await current_book(self._sessions)
        if book is None:
            return None
        tickers = sorted(book["qty"])
        syms = await self._symbols(tickers)
        yf_syms = {t: to_yf_symbol(t, syms[t].exchange if t in syms else None) for t in tickers}
        quotes, closes, costs, recent = await asyncio.gather(
            self._yf.quotes(list(yf_syms.values())), self._last_closes(tickers), self._costs(syms),
            self._recent_trades(tickers),
        )
        rows = []
        for t in tickers:
            qty = book["qty"][t] / 1_000_000
            cur = syms[t].currency if t in syms and syms[t].currency in ("CAD", "USD") else "CAD"
            quote = quotes.get(yf_syms[t]) or {}
            last_close, prev_close = closes.get(t, (None, None))
            last = quote.get("last") or last_close
            prev = quote.get("prev_close") or prev_close
            cq, cc = costs.get(t, (0, 0))
            avg = (cc / 100) / (cq / 1_000_000) if cq and cc else None
            session_day = _session_date(quote.get("time"))
            trades = [(q, px) for d, q, px in recent.get(t, []) if d == session_day]
            rows.append({
                "ticker": t,
                "currency": cur,
                "qty": qty,
                "avg_cost": avg,
                "last": last,
                "session": quote.get("session"),  # "post"/"pre" = extended-hours price
                "price_time": quote.get("time"),  # epoch s of the trade behind `last`; None = fallback close
                "prev_close": prev,
                "mv": qty * last if last is not None else 0.0,
                "cost": avg * qty if avg is not None else None,
                # Shares bought/sold this session count from their fill, not the
                # previous close. DECISION: a position fully sold today has no
                # row, so its realized day move drops out of the day total.
                "day_pnl": fill_aware_day_pnl(last, prev, qty, trades) if last is not None and prev else None,
            })
        cash = {"CAD": book["cash"].get("CAD", 0) / 100, "USD": book["cash"].get("USD", 0) / 100}
        return {"date": book["date"], "rows": rows, "cash": cash}

    async def total_equity_cad(self) -> float | None:
        pos = await self.positions()
        if pos is None:
            return None
        rate = await self._rate()
        native = {"CAD": pos["cash"]["CAD"], "USD": pos["cash"]["USD"]}
        for p in pos["rows"]:
            native[p["currency"]] += p["mv"]
        return self._to_display(native, "CAD", rate)

    async def freshness(self) -> dict:
        """How current the Wealthsimple ledger is — it only moves when a new
        activities export is imported."""
        from . import history

        async with self._sessions() as session:
            ledger_ids = list(
                (await session.execute(
                    select(Account.qt_account_id).where(Account.tracked, Account.broker != history.QUESTRADE)
                )).scalars()
            )
            through = (
                await session.execute(
                    select(func.max(Activity.transaction_date)).where(Activity.account_id.in_(ledger_ids))
                )
            ).scalar_one_or_none() if ledger_ids else None
        last_import = await history.get_setting(self._sessions, "ws_last_import", None)
        return {
            "ledger_accounts": len(ledger_ids),
            "activity_through": through.isoformat() if through else None,
            "last_import_at": (last_import or {}).get("at") if isinstance(last_import, dict) else None,
        }

    # -- internals -------------------------------------------------------

    async def _header_stats(self, live_total_cad: float) -> dict:
        """series_daily-backed stats with today priced at the live total."""
        from . import history

        try:
            return await history.header_stats(self._sessions, live_total_cad, date.today())
        except Exception as exc:
            log.warning("header stats unavailable: %s", exc)
            return {"mwr": None, "mwr_since": None, "twr_ytd": None}

    async def _symbols(self, tickers: list[str]) -> dict[str, Symbol]:
        async with self._sessions() as session:
            return {s.ticker: s for s in (await session.execute(select(Symbol).where(Symbol.ticker.in_(tickers)))).scalars()}

    async def _costs(self, syms: dict[str, Symbol]) -> dict[str, tuple[int, int]]:
        """Average-cost book over every tracked account's history (cached 60 s)."""
        from . import history
        from .activities import load_activities
        from .reconstruction import match_transfers

        now_ts = utcnow().timestamp()
        if self._cost_cache and now_ts - self._cost_cache[0] < COST_TTL:
            return self._cost_cache[1]
        ids = await history.tracked_ids(self._sessions)
        acts, _ = match_transfers(history.activity_dicts(await load_activities(self._sessions, ids + [""])))
        splits = {}
        for t in {a["ticker"] for a in acts if a["ticker"]}:
            s = await self._yf.splits(to_yf_symbol(t, syms[t].exchange if t in syms else None))
            if s:
                splits[t] = s
        costs = average_costs(acts, splits)
        self._cost_cache = (now_ts, costs)
        return costs

    async def _recent_trades(self, tickers: list[str]) -> dict[str, list[tuple[date, float, float]]]:
        """ticker → [(trade date, signed qty, fill price)] over the last week of
        tracked-account trades — enough to cover any quote's session day."""
        from . import history

        ids = await history.tracked_ids(self._sessions)
        since = date.today() - timedelta(days=7)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(Activity).where(
                    Activity.account_id.in_(ids), Activity.ticker.in_(tickers), Activity.trade_date >= since,
                )
            )).scalars().all()
        out: dict[str, list[tuple[date, float, float]]] = {}
        for r in rows:
            row = {"type": r.type, "action": r.action, "qty_micro": r.qty_micro}
            if classify(row) != "trade" or not r.price_micro:
                continue
            sq = signed_qty_micro(row, "trade")
            if sq:
                out.setdefault(r.ticker, []).append((r.trade_date, sq / 1_000_000, r.price_micro / 1_000_000))
        return out

    async def _last_closes(self, tickers: list[str]) -> dict[str, tuple[float | None, float | None]]:
        """(latest, previous) cached daily close — the fallback when a quote is unavailable."""
        out = {}
        async with self._sessions() as session:
            for t in tickers:
                closes = (
                    await session.execute(
                        select(Candle.c)
                        .where(Candle.ticker == t, Candle.interval == "OneDay", Candle.c.is_not(None))
                        .order_by(Candle.ts.desc())
                        .limit(2)
                    )
                ).scalars().all()
                out[t] = (closes[0] if closes else None, closes[1] if len(closes) > 1 else None)
        return out

    async def _rate(self) -> Decimal:
        """USDCAD for display conversion: BoC latest; 1.0 if unavailable."""
        r = await self._fx.latest()
        return r[1] if r is not None else Decimal(1)

    @staticmethod
    def _to_display(native: dict[str, float], currency: str, rate: Decimal) -> float:
        cad = native.get("CAD", 0.0)
        usd = native.get("USD", 0.0)
        if currency == "CAD":
            return cad + usd * float(rate)
        return usd + (cad / float(rate) if rate else 0.0)

    async def _sparklines(self, tickers: list[str]) -> dict[str, list[float]]:
        syms = await self._symbols(tickers)
        start = date.today() - timedelta(days=SPARKLINE_DAYS + 15)
        await asyncio.gather(
            *(self._yf.refresh_bars(t, to_yf_symbol(t, syms[t].exchange if t in syms else None), "OneDay", start)
              for t in tickers),
            return_exceptions=True,
        )
        out: dict[str, list[float]] = {}
        async with self._sessions() as session:
            for t in tickers:
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
