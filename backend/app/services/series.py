"""Valuation, seam rescaling, TWR — SPEC.md §6.5–§7. Pure math up top (unit-
tested against the §7 fixtures), DB orchestration below.

All values integer CAD cents until the final float conversion for the API.
Forward-fill of closes per listing exchange falls out naturally: candles exist
only on that exchange's trading days, and we take the latest close ≤ d.
"""

import bisect
import logging
import statistics
from datetime import date, timedelta

log = logging.getLogger(__name__)

MICRO = 1_000_000


class _FFill:
    """Latest value at or before a date, via bisect."""

    def __init__(self, points: dict[date, int | float]):
        self._dates = sorted(points)
        self._values = [points[d] for d in self._dates]

    def at(self, d: date, *, flat_backfill: bool = False):
        i = bisect.bisect_right(self._dates, d)
        if i == 0:
            return self._values[0] if (flat_backfill and self._values) else None
        return self._values[i - 1]

    @property
    def empty(self) -> bool:
        return not self._dates


def value_series(
    daily_qty: dict[date, dict[str, int]],
    daily_cash: dict[date, dict[str, int]],
    closes_cents: dict[str, dict[date, int]],
    fx_micro: dict[date, int],
    sym_currency: dict[str, str],
) -> tuple[dict[date, int], list[dict]]:
    """Σ(qty × close → CAD) + Σ cash(→ CAD), per date, in CAD cents."""
    warnings: list[dict] = []
    fx = _FFill(fx_micro)
    ffills = {t: _FFill(pts) for t, pts in closes_cents.items()}
    warned: set[str] = set()
    out: dict[date, int] = {}
    for d in sorted(daily_qty):
        fx_rate = fx.at(d, flat_backfill=True) or MICRO  # 1.0 if no FX data at all
        total = 0
        for ticker, qty in daily_qty[d].items():
            ff = ffills.get(ticker)
            close = ff.at(d, flat_backfill=True) if ff and not ff.empty else None
            if close is None:
                if ticker not in warned:
                    warned.add(ticker)
                    warnings.append({"date": d.isoformat(), "ticker": ticker,
                                     "message": f"no daily candles for {ticker}; its value is missing from early history"})
                continue
            if ff.at(d) is None and ticker not in warned:
                warned.add(ticker)
                warnings.append({"date": d.isoformat(), "ticker": ticker,
                                 "message": f"candle history for {ticker} starts after {d}; earliest close used flat"})
            native_cents = qty * close // MICRO
            if sym_currency.get(ticker, "CAD") == "USD":
                total += native_cents * fx_rate // MICRO
            else:
                total += native_cents
        for cur, cents in daily_cash.get(d, {}).items():
            total += cents * fx_rate // MICRO if cur == "USD" else cents
        out[d] = total
    return out, warnings


def flows_to_cad(flows: dict[date, dict[str, int]], fx_micro: dict[date, int]) -> dict[date, int]:
    fx = _FFill(fx_micro)
    out: dict[date, int] = {}
    for d, per_cur in flows.items():
        rate = fx.at(d, flat_backfill=True) or MICRO
        total = 0
        for cur, cents in per_cur.items():
            total += cents * rate // MICRO if cur == "USD" else cents
        if total:
            out[d] = total
    return out


def apply_ground_truth(recon: dict[date, int], gt: dict[date, int]) -> dict[date, int]:
    """§6.6: snapshots win, without seams. Before the first snapshot: one
    constant factor. Between snapshots: linear factor interpolation. After the
    last: the last factor. A step discontinuity would print a phantom TWR
    return; rescaling forbids it."""
    if not gt:
        return dict(recon)
    gt_dates = sorted(k for k in gt if k in recon)
    if not gt_dates:
        return dict(recon)

    def factor_at(gd: date) -> float:
        r = recon[gd]
        return gt[gd] / r if r else 1.0

    out: dict[date, int] = {}
    for d in sorted(recon):
        if d <= gt_dates[0]:
            f = factor_at(gt_dates[0])
        elif d >= gt_dates[-1]:
            f = factor_at(gt_dates[-1])
        else:
            j = bisect.bisect_left(gt_dates, d)
            a, b = gt_dates[j - 1], gt_dates[j]
            fa, fb = factor_at(a), factor_at(b)
            w = (d - a).days / (b - a).days
            f = fa + (fb - fa) * w
        out[d] = gt[d] if d in gt else round(recon[d] * f)
    return out


def twr_index(values: dict[date, int], flows_cad: dict[date, int]) -> tuple[dict[date, float], list[dict]]:
    """§7: r_t = (V_t − V_{t−1} − F_t) / (V_{t−1} + F_t), start-of-day flows.
    Denominator ≤ 0 → r_t = 0, logged. Chained, 100 at series start."""
    warnings: list[dict] = []
    dates = sorted(values)
    idx: dict[date, float] = {}
    if not dates:
        return idx, warnings
    idx[dates[0]] = 100.0
    for prev, d in zip(dates, dates[1:]):
        flow = flows_cad.get(d, 0)
        denom = values[prev] + flow
        if denom <= 0:
            r = 0.0
            warnings.append({"date": d.isoformat(), "ticker": None,
                             "message": f"TWR denominator ≤ 0 on {d} (empty account); day return forced to 0"})
        else:
            r = (values[d] - values[prev] - flow) / denom
        idx[d] = idx[prev] * (1 + r)
    return idx, warnings


def daily_returns(idx: dict[date, float]) -> dict[date, float]:
    dates = sorted(idx)
    return {d: idx[d] / idx[prev] - 1 for prev, d in zip(dates, dates[1:]) if idx[prev]}


def range_start(all_dates: list[date], range_key: str) -> date:
    end = all_dates[-1]
    days = {"1m": 30, "3m": 91, "6m": 182, "1y": 365}.get(range_key)
    if days is None:
        return all_dates[0]
    target = end - timedelta(days=days)
    i = bisect.bisect_left(all_dates, target)
    return all_dates[min(i, len(all_dates) - 1)]


def range_stats(
    dates: list[date], values: dict[date, int], idx: dict[date, float], flows_cad: dict[date, int]
) -> dict:
    if len(dates) < 2:
        return {"twr": None, "mwr": None, "max_drawdown": None, "vol": None, "best_day": None, "worst_day": None}
    s, e = dates[0], dates[-1]
    twr = idx[e] / idx[s] - 1 if idx.get(s) else None

    # MWR (§7): −V(start) pseudo-flow, deposits negative, withdrawals positive, +V(end).
    # XIRR is annualized by definition; we convert to the PERIOD return so it sits
    # comparably next to the cumulative TWR (a 1-month window would otherwise
    # display an absurd annualized number). DECISION: mwr = (1+xirr)^(days/365) − 1.
    mwr = None
    try:
        from pyxirr import xirr

        xs = [(s, -values[s] / 100)]
        xs += [(d, -flows_cad[d] / 100) for d in dates[1:] if flows_cad.get(d)]
        xs.append((e, values[e] / 100))
        annual = xirr([d for d, _ in xs], [a for _, a in xs])
        if annual is not None and annual > -1:
            mwr = (1 + annual) ** ((e - s).days / 365) - 1
    except Exception as exc:
        log.info("XIRR did not converge for %s..%s: %s", s, e, exc)

    peak, maxdd = None, 0.0
    for d in dates:
        v = idx[d]
        peak = v if peak is None or v > peak else peak
        if peak:
            maxdd = min(maxdd, v / peak - 1)

    rets = {d: r for d, r in daily_returns({d: idx[d] for d in dates}).items()}
    vol = statistics.stdev(rets.values()) * (252**0.5) if len(rets) >= 2 else None
    best = max(rets, key=rets.get) if rets else None
    worst = min(rets, key=rets.get) if rets else None
    return {
        "twr": twr,
        "mwr": mwr,
        "mwr_since": s.isoformat(),
        "max_drawdown": maxdd,
        "vol": vol,
        "best_day": {"date": best.isoformat(), "r": rets[best]} if best else None,
        "worst_day": {"date": worst.isoformat(), "r": rets[worst]} if worst else None,
    }
