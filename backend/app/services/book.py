"""The current book — what is held right now, at what average cost.

Since the move to Wealthsimple there is no live positions/balances API: the
reconstructed ledger (reconstruction.py) IS the book. Quantities and cash are the newest
holdings_ledger / cash_ledger day; average cost comes from an average-cost pass
over the activity history (all tracked accounts as one book, so a position
keeps its cost basis across the Questrade → Wealthsimple transfer).
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import CashLedgerRow, HoldingsLedgerRow
from .reconstruction import classify, detect_split_journal_pairs, signed_qty_micro


async def current_book(sessions: async_sessionmaker) -> dict | None:
    """{"date", "qty": {ticker: micro}, "cash": {currency: cents}} from the newest
    ledger day, or None before the first recompute."""
    async with sessions() as session:
        d = (await session.execute(select(func.max(CashLedgerRow.date)))).scalar_one_or_none()
        if d is None:
            return None
        qty = {
            r.ticker: r.qty_micro
            for r in (await session.execute(select(HoldingsLedgerRow).where(HoldingsLedgerRow.date == d))).scalars()
            if r.qty_micro
        }
        cash = {
            r.currency: r.balance_cents
            for r in (await session.execute(select(CashLedgerRow).where(CashLedgerRow.date == d))).scalars()
        }
    return {"date": d, "qty": qty, "cash": cash}


def average_costs(activities: list[dict], splits: dict[str, dict[date, float]]) -> dict[str, tuple[int, int]]:
    """ticker → (qty_micro, cost_cents) of the open position, native currency,
    in today's split-adjusted units (rows before a split scale by its factor;
    broker split-journal pairs are skipped — same convention as the
    back-adjusted walk).

    Average-cost method: buys add their full net cost (commission included),
    sells release cost pro rata, a position that closes resets to zero.
    Journals/transfers move quantity but never cost — a Questrade → Wealthsimple
    transfer nets to zero on its (matched, same-day) date, so the basis carries
    over. DECISION: shares arriving from an untracked institution enter at zero
    cost (the export's book value is not reliable — CAD figures on USD rows)."""
    skip = {i for _, out_id, in_id, _, _ in detect_split_journal_pairs(activities, splits) for i in (out_id, in_id)}

    def factor_after(ticker: str, d: date) -> float:
        f = 1.0
        for sd, factor in splits.get(ticker, {}).items():
            if sd > d and factor:
                f *= factor
        return f

    by_day: dict[date, list[dict]] = {}
    for r in activities:
        if r.get("transaction_date") and r.get("ticker") and r.get("id") not in skip:
            by_day.setdefault(r["transaction_date"], []).append(r)

    qty: dict[str, int] = {}
    cost: dict[str, int] = {}
    for d in sorted(by_day):
        rows = by_day[d]
        # Buys before sells within a day, so a same-day round trip can't open a
        # negative position and corrupt the basis.
        trades = sorted((r for r in rows if classify(r) == "trade"), key=lambda r: (r.get("action") or "") != "buy")
        for r in trades:
            t = r["ticker"]
            sq = round(signed_qty_micro(r, "trade") * factor_after(t, d))
            if sq > 0:
                qty[t] = qty.get(t, 0) + sq
                cost[t] = cost.get(t, 0) + abs(r.get("net_amount_cents") or 0)
            elif sq < 0:
                held = qty.get(t, 0)
                if held > 0:
                    cost[t] = cost.get(t, 0) - round(cost.get(t, 0) * min(1.0, -sq / held))
                qty[t] = held + sq
        # Journals net per day: a matched transfer's legs cancel exactly.
        net: dict[str, int] = {}
        for r in rows:
            kind = classify(r)
            if kind in ("journal", "dividend"):
                sq = signed_qty_micro(r, kind)
                if sq:
                    net[r["ticker"]] = net.get(r["ticker"], 0) + round(sq * factor_after(r["ticker"], d))
        for t, sq in net.items():
            qty[t] = qty.get(t, 0) + sq
        for t in list(qty):
            if qty[t] <= 0:
                qty[t], cost[t] = 0, 0
    return {t: (q, cost.get(t, 0)) for t, q in qty.items() if q > 0}
