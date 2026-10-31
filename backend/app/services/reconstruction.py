"""Backward portfolio reconstruction.

Anchors at a known EOD state (live snapshot) and walks activities newest →
oldest, reversing each. Correct for exactly the window the data covers; the
residual state at the window edge IS the opening balance. Pure functions — the
test_reconstruction fixtures drive this directly.

Sign conventions (forward semantics; reversal subtracts):
- qty: buys/DRIP/journal-in positive, sells/journal-out negative (micro-shares)
- net_amount_cents: Questrade's sign (buy negative, sell/dividend positive)
- flows: deposits positive, withdrawals negative, recorded at their own dates
  with un-reversed sign — the only rows that count as contributions.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

log = logging.getLogger(__name__)

BACK_ADJUSTED = "back_adjusted"
UNADJUSTED = "unadjusted"

JOURNAL_PAIR_TOLERANCE_DAYS = 4  # ±2 business days ≈ ±4 calendar days
SPLIT_RATIO_TOLERANCE = 0.015


@dataclass
class ReconResult:
    daily_qty: dict[date, dict[str, int]] = field(default_factory=dict)   # EOD micro-shares
    daily_cash: dict[date, dict[str, int]] = field(default_factory=dict)  # EOD cents by currency
    flows: dict[date, dict[str, int]] = field(default_factory=dict)       # external flows, cents by currency
    flow_events: list[dict] = field(default_factory=list)                 # chart markers
    opening_qty: dict[str, int] = field(default_factory=dict)
    opening_cash: dict[str, int] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    skipped_split_journal_ids: list[int] = field(default_factory=list)


def classify(row: dict) -> str:
    t = (row.get("type") or "").lower()
    a = (row.get("action") or "").lower()
    if "trade" in t or a in ("buy", "sell"):
        return "trade"
    if "dividend" in t or t == "div" or "drip" in t:
        return "dividend"
    if "deposit" in t or "contribution" in t or a in ("con", "dep"):
        return "deposit"
    if "withdraw" in t:
        return "withdrawal"
    if "fx" in t or "foreign exchange" in t:
        return "fx"
    if "interest" in t:
        return "interest"
    if "fee" in t or "rebate" in t or "tax" in t:
        return "fee"
    if "transfer" in t or "journal" in t or "corporate" in t:
        return "journal"
    if t == "other" and row.get("ticker") and row.get("qty_micro"):
        return "journal"
    # DECISION: a symbol-less, quantity-less "Other" row is a cash-only charge
    # (Questrade posts HST on the transfer-out fee this way) — fee semantics, no flow.
    if t == "other" and not row.get("qty_micro") and row.get("net_amount_cents"):
        return "fee"
    return "unknown"


def signed_qty_micro(row: dict, kind: str) -> int:
    q = row.get("qty_micro") or 0
    a = (row.get("action") or "").lower()
    if kind == "trade":
        if a == "buy":
            return abs(q)
        if a == "sell":
            return -abs(q)
    return q  # trust the posted sign elsewhere (journals, DRIP)


def detect_split_journal_pairs(
    activities: list[dict], splits: dict[str, dict[date, float]]
) -> list[tuple[str, int, int, date, float]]:
    """Broker-posted split journals: same ticker, out-row + in-row near a known
    split date, in ≈ -out × factor. Must not be double-applied on top of the
    factor. Returns (ticker, out_id, in_id, split_date, factor)."""
    pairs = []
    by_ticker: dict[str, list[dict]] = {}
    for r in activities:
        k = classify(r)
        if k == "journal" and r.get("ticker") and (r.get("qty_micro") or 0) != 0:
            by_ticker.setdefault(r["ticker"], []).append(r)
    for ticker, tsplits in splits.items():
        rows = by_ticker.get(ticker, [])
        for split_date, factor in tsplits.items():
            if not factor or factor == 1:
                continue
            window = [
                r for r in rows
                if abs((r["transaction_date"] - split_date).days) <= JOURNAL_PAIR_TOLERANCE_DAYS
            ]
            outs = [r for r in window if r["qty_micro"] < 0]
            ins = [r for r in window if r["qty_micro"] > 0]
            for out in outs:
                for inn in ins:
                    expected = -out["qty_micro"] * factor
                    if expected and abs(inn["qty_micro"] - expected) / expected < SPLIT_RATIO_TOLERANCE:
                        pairs.append((ticker, out["id"], inn["id"], split_date, factor))
                        break
    return pairs


def walk_backward(
    anchor_qty: dict[str, int],
    anchor_cash: dict[str, int],
    anchor_date: date,
    activities: list[dict],
    regime: str,
    splits: dict[str, dict[date, float]] | None = None,
) -> ReconResult:
    splits = splits or {}
    res = ReconResult()
    qty = {t: int(q) for t, q in anchor_qty.items() if q}
    cash = {c: int(v) for c, v in anchor_cash.items()}

    rows = [r for r in activities if r.get("transaction_date")]
    dropped = [r for r in activities if not r.get("transaction_date")]
    for r in dropped:
        res.warnings.append({"date": None, "ticker": r.get("ticker"), "message": f"activity id={r.get('id')} has no transaction date; skipped"})
    future = [r for r in rows if r["transaction_date"] > anchor_date]
    if future:
        res.warnings.append({"date": anchor_date.isoformat(), "ticker": None,
                             "message": f"{len(future)} activities post-date the anchor snapshot and were ignored"})
        rows = [r for r in rows if r["transaction_date"] <= anchor_date]
    if not rows:
        res.opening_qty, res.opening_cash = dict(qty), dict(cash)
        res.daily_qty[anchor_date] = dict(qty)
        res.daily_cash[anchor_date] = dict(cash)
        return res

    pair_info = detect_split_journal_pairs(rows, splits)
    paired_ids = {i for _, out_id, in_id, _, _ in pair_info for i in (out_id, in_id)}
    # (ticker, split_date) combos already converted by broker journal rows.
    journal_handled = {(t, sd) for t, _, _, sd, _ in pair_info}
    if regime == BACK_ADJUSTED:
        res.skipped_split_journal_ids = sorted(paired_ids)

    # Back-adjusted regime: the whole walk stays in today's units, but activity
    # rows are posted in date-native units — a pre-split buy of 100 is 200
    # today-units after a 2:1. running_factor[t] = Π factors of splits AFTER the
    # current walk date; rows dated ON the split date are already post-split.
    running_factor: dict[str, float] = {t: 1.0 for t in splits}

    by_day: dict[date, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["transaction_date"], []).append(r)
    earliest = min(by_day)

    d = anchor_date
    while d >= earliest:
        # Record EOD state for day d.
        res.daily_qty[d] = {t: q for t, q in qty.items() if q}
        res.daily_cash[d] = dict(cash)
        for t, q in qty.items():
            if q < 0:
                res.warnings.append({"date": d.isoformat(), "ticker": t,
                                     "message": f"negative quantity {q / 1e6:g} at {d} — missing older history or unhandled corporate action"})

        # Reverse day d's activities.
        for r in by_day.get(d, []):
            kind = classify(r)
            net = r.get("net_amount_cents") or 0
            cur = r.get("currency") or "CAD"
            ticker = r.get("ticker")

            if kind == "unknown":
                res.warnings.append({"date": d.isoformat(), "ticker": ticker,
                                     "message": f"unknown activity type {r.get('type')!r} (id={r.get('id')}); cash/qty effects skipped"})
                continue

            if kind in ("deposit", "withdrawal"):
                res.flows.setdefault(d, {})
                res.flows[d][cur] = res.flows[d].get(cur, 0) + net
                res.flow_events.append({"date": d.isoformat(), "amount_cents": net, "currency": cur, "kind": kind})

            if regime == BACK_ADJUSTED and r.get("id") in paired_ids:
                # Broker split journal: quantities stay in today's units; reversing
                # the pair would double-convert. Cash effect (none) still honored.
                cash[cur] = cash.get(cur, 0) - net
                continue

            sq = signed_qty_micro(r, kind)
            if kind == "trade" and sq and not ticker:
                res.warnings.append({"date": d.isoformat(), "ticker": None,
                                     "message": f"trade row without a symbol (id={r.get('id')}) — its share "
                                                "quantity cannot be tracked and was ignored"})
            if regime == BACK_ADJUSTED and ticker in running_factor and sq:
                sq = round(sq * running_factor[ticker])
            if kind in ("trade", "journal", "dividend") and ticker and sq:
                qty[ticker] = qty.get(ticker, 0) - sq  # dividend w/ qty = DRIP delivery
            cash[cur] = cash.get(cur, 0) - net

        # Crossing a split date going back (i.e. moving from the split date to
        # the day before it): unadjusted quantities become date-native — unless
        # broker journals already did the conversion; back-adjusted walks bump
        # the running unit-conversion factor instead.
        for ticker, tsplits in splits.items():
            factor = tsplits.get(d)
            if not factor or factor == 1:
                continue
            if regime == UNADJUSTED:
                if (ticker, d) not in journal_handled and qty.get(ticker):
                    qty[ticker] = round(qty[ticker] / factor)
            else:
                running_factor[ticker] *= factor

        d -= timedelta(days=1)

    res.opening_qty = {t: q for t, q in qty.items() if q}
    res.opening_cash = dict(cash)
    for t, q in res.opening_qty.items():
        if q < 0:
            res.warnings.append({"date": earliest.isoformat(), "ticker": t,
                                 "message": f"opening quantity {q / 1e6:g} is negative — history incomplete before {earliest}"})
    return res


TRANSFER_MATCH_DAYS = 7


def match_transfers(activities: list[dict]) -> tuple[list[dict], list[dict]]:
    """Broker-to-broker transfers (Questrade TFO → Wealthsimple SecurityTransfer,
    and the cash legs that settle the fee/residual balances) between TRACKED
    accounts are internal: zero external flow, and the two legs must land on the
    SAME day or the aggregate ledger double-counts (or drops) the assets while
    they are in transit. Matched pairs are re-dated to the later leg — the
    assets stay where they were until they arrive.

    Unmatched legs came from / went to an untracked institution: a cash leg is
    retyped as a deposit/withdrawal (a real external flow); an in-kind leg is
    kept as a journal with a warning (its value enters without a capital-in
    flow). Returns (rows, warnings); changed rows are copies."""
    rows = [dict(r) for r in activities]
    legs = [
        r for r in rows
        if "transfer" in (r.get("type") or "").lower() and r.get("transaction_date")
        and ((r.get("ticker") and r.get("qty_micro")) or (not r.get("qty_micro") and r.get("net_amount_cents")))
    ]
    legs.sort(key=lambda r: (r["transaction_date"], r.get("id") or 0))
    warnings: list[dict] = []
    matched: set[int] = set()

    def key(r: dict) -> tuple:
        if r.get("ticker") and r.get("qty_micro"):
            return ("sec", r["ticker"], abs(r["qty_micro"]))
        return ("cash", r.get("currency") or "CAD", abs(r["net_amount_cents"]))

    def sign(r: dict) -> int:
        v = r["qty_micro"] if key(r)[0] == "sec" else r["net_amount_cents"]
        return 1 if v > 0 else -1

    for i, a in enumerate(legs):
        if i in matched:
            continue
        best = None
        for j in range(i + 1, len(legs)):
            b = legs[j]
            gap = (b["transaction_date"] - a["transaction_date"]).days
            if gap > TRANSFER_MATCH_DAYS:
                break
            if j in matched or b["account_id"] == a["account_id"]:
                continue
            if key(b) == key(a) and sign(b) == -sign(a):
                best = j
                break
        if best is None:
            continue
        matched.update((i, best))
        b = legs[best]
        a["transaction_date"] = b["transaction_date"] = max(a["transaction_date"], b["transaction_date"])

    for i, r in enumerate(legs):
        if i in matched:
            continue
        d = r["transaction_date"].isoformat()
        if key(r)[0] == "cash":
            r["type"] = "deposits" if r["net_amount_cents"] > 0 else "withdrawals"
            warnings.append({"date": d, "ticker": None,
                             "message": f"cash transfer {r['net_amount_cents'] / 100:+.2f} {r.get('currency')} has no "
                                        "counterpart in a tracked account — counted as an external deposit/withdrawal"})
        else:
            warnings.append({"date": d, "ticker": r["ticker"],
                             "message": f"in-kind transfer of {r['qty_micro'] / 1e6:g} sh has no counterpart in a "
                                        "tracked account — its value enters/leaves without a capital-in flow"})
    return rows, warnings


class LedgerState:
    """Exact state of ledger-only accounts (Wealthsimple: no balances API, so
    the CSV activity history from account opening IS the ground truth) at any
    date, in the same back-adjusted units walk_backward uses.

    Built from one zero-anchored backward walk: with anchor 0, the walk's
    opening state is −(all effects) and its EOD state at d is −(effects after d),
    so state(d) = daily(d) − opening. Exact because the back-adjusted walk is
    linear in its anchor; using the walk itself keeps split handling identical."""

    def __init__(self, activities: list[dict], end: date, splits: dict[str, dict[date, float]] | None = None):
        dated = [r for r in activities if r.get("transaction_date")]
        self._first = min((r["transaction_date"] for r in dated), default=None)
        self._end = end
        if self._first is None:
            return
        z = walk_backward({}, {}, end, dated, BACK_ADJUSTED, splits)
        self._daily_qty, self._daily_cash = z.daily_qty, z.daily_cash
        self._total_qty = {t: -q for t, q in z.opening_qty.items()}
        self._total_cash = {c: -v for c, v in z.opening_cash.items()}

    def at(self, d: date) -> tuple[dict[str, int], dict[str, int]]:
        if self._first is None or d < self._first:
            return {}, {}
        if d >= self._end:
            return dict(self._total_qty), dict(self._total_cash)
        qty = dict(self._total_qty)
        for t, q in self._daily_qty.get(d, {}).items():
            qty[t] = qty.get(t, 0) + q
        cash = dict(self._total_cash)
        for c, v in self._daily_cash.get(d, {}).items():
            cash[c] = cash.get(c, 0) + v
        return {t: q for t, q in qty.items() if q}, cash


def add_states(*states: tuple[dict[str, int], dict[str, int]]) -> tuple[dict[str, int], dict[str, int]]:
    qty: dict[str, int] = {}
    cash: dict[str, int] = {}
    for q, c in states:
        for t, v in q.items():
            qty[t] = qty.get(t, 0) + v
        for cur, v in c.items():
            cash[cur] = cash.get(cur, 0) + v
    return {t: v for t, v in qty.items() if v}, cash


def has_negative_qty(res: ReconResult) -> bool:
    return any("negative quantity" in w["message"] or "is negative" in w["message"] for w in res.warnings)


def extend_forward(res: ReconResult, activities: list[dict], start: date, end: date) -> None:
    """Carry the ledger FORWARD from the EOD state at `start` through `end`,
    applying whatever activities ARE recorded in that span. Used when the
    newest snapshot contradicts recorded history (broker posting lag):
    the walk anchors at the last consistent snapshot and this extension keeps
    the series current; snapshot ground-truth pinning (series.py) corrects the totals at every
    real snapshot in the span."""
    qty = dict(res.daily_qty.get(start, {}))
    cash = dict(res.daily_cash.get(start, {}))
    by_day: dict[date, list[dict]] = {}
    for r in activities:
        d = r.get("transaction_date")
        if d and start < d <= end:
            by_day.setdefault(d, []).append(r)
    d = start + timedelta(days=1)
    while d <= end:
        for r in by_day.get(d, []):
            kind = classify(r)
            if kind == "unknown":
                continue
            net = r.get("net_amount_cents") or 0
            cur = r.get("currency") or "CAD"
            ticker = r.get("ticker")
            sq = signed_qty_micro(r, kind)
            if kind in ("deposit", "withdrawal"):
                res.flows.setdefault(d, {})
                res.flows[d][cur] = res.flows[d].get(cur, 0) + net
                res.flow_events.append({"date": d.isoformat(), "amount_cents": net, "currency": cur, "kind": kind})
            if kind in ("trade", "journal", "dividend") and ticker and sq:
                qty[ticker] = qty.get(ticker, 0) + sq
            cash[cur] = cash.get(cur, 0) + net
        res.daily_qty[d] = {t: q for t, q in qty.items() if q}
        res.daily_cash[d] = dict(cash)
        d += timedelta(days=1)
