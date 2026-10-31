"""Counterfactuals for the owner's own decisions.

Trade decisions: the counterfactual of a day's trades is the same book without
them (proceeds stay in cash, nothing is bought). Their value-add at horizon h
is therefore exact accounting: Σ Δq_i · price_i · FX · R_i(close_d → close_d+h)
in CAD. Buys earn the holding's return; sells earn the return they avoided.
DECISION: measured from the trade day's close, so it isolates timing and
selection, not intraday execution; later trades are left out, so each
decision is judged on its own.
Placebo: the same CAD basket started on every other session within ~3 years. The
decision's percentile in that distribution says whether its timing beat
what the same trade would usually have earned.

Events: a Bayesian structural time series in the spirit of CausalImpact
(Brodersen et al. 2015). A local-level model with factor regressors is fitted
to the portfolio's daily returns before the event and forecast after it. The
cumulative gap between realized and forecast is the abnormal return, with
simulation intervals, plus a placebo run on a fake event before the real one.
"""

import bisect
from dataclasses import dataclass
from datetime import date

import numpy as np

HORIZONS = (20, 60, 120)
PLACEBO_SPAN = 756  # sessions either side (~3 years): placebo starts from a comparable market


@dataclass
class Trade:
    day: date
    ticker: str
    qty: float  # signed: + bought, − sold
    price: float  # native fill
    currency: str


def _levels_on(calendar: list[date], closes: dict[date, float]) -> np.ndarray:
    from .data import align

    return align(closes, calendar)


def decisions(trades: list[Trade], calendar: list[date], closes: dict[str, dict[date, float]],
              fx: dict[date, float], horizons: tuple[int, ...] = HORIZONS) -> list[dict]:
    """`closes` maps ticker → adjusted closes; `fx` is USD/CAD on the same source."""
    fxl = _levels_on(calendar, fx)
    lv_native = {t: _levels_on(calendar, c) for t, c in closes.items()}
    by_day: dict[date, list[Trade]] = {}
    for t in trades:
        by_day.setdefault(t.day, []).append(t)
    out = []
    for day in sorted(by_day):
        i0 = bisect.bisect_left(calendar, day)
        if i0 >= len(calendar):
            continue
        legs: dict[str, dict] = {}
        for t in by_day[day]:
            if t.ticker not in lv_native:
                continue
            fxd = fxl[i0] if t.currency == "USD" else 1.0
            leg = legs.setdefault(t.ticker, {"ticker": t.ticker, "qty": 0.0, "cad": 0.0, "currency": t.currency})
            leg["qty"] += t.qty
            leg["cad"] += t.qty * t.price * fxd
        legs = {k: v for k, v in legs.items() if abs(v["qty"]) > 1e-9 and np.isfinite(v["cad"])}
        if not legs:
            continue
        # CAD level of each leg's holding, so R(a→b) = lv[b]/lv[a] − 1.
        cad_lv = {k: lv_native[k] * (fxl if v["currency"] == "USD" else 1.0) for k, v in legs.items()}
        result = {"date": day.isoformat(), "legs": [
            {"ticker": v["ticker"], "side": "buy" if v["qty"] > 0 else "sell", "qty": abs(v["qty"]), "cad": abs(v["cad"])}
            for v in sorted(legs.values(), key=lambda v: -abs(v["cad"]))
        ], "gross_cad": float(sum(abs(v["cad"]) for v in legs.values())), "horizons": {}}
        for h in horizons:
            if i0 + h >= len(calendar):
                result["horizons"][h] = None
                continue
            # Value-add of the basket started at every session s, over h sessions.
            path = sum(legs[k]["cad"] * (cad_lv[k][h:] / cad_lv[k][:-h] - 1) for k in legs)
            va = path[i0]
            if not np.isfinite(va):
                result["horizons"][h] = None
                continue
            near = np.abs(np.arange(len(path)) - i0) <= PLACEBO_SPAN
            near[i0] = False
            placebo = path[near & np.isfinite(path)]
            result["horizons"][h] = {
                "value_add": float(va),
                "percentile": float(np.mean(placebo < va)) if len(placebo) else None,
                "placebo_n": int(len(placebo)),
                "placebo_median": float(np.median(placebo)) if len(placebo) else None,
            }
        out.append(result)
    return out


def summarize(rows: list[dict], horizons: tuple[int, ...] = HORIZONS) -> dict:
    out = {}
    for h in horizons:
        done = [r["horizons"][h] for r in rows if r["horizons"].get(h)]
        if not done:
            out[h] = None
            continue
        pcts = [d["percentile"] for d in done if d["percentile"] is not None]
        out[h] = {"n": len(done), "value_add": float(sum(d["value_add"] for d in done)),
                  "mean_percentile": float(np.mean(pcts)) if pcts else None,
                  "beat_median": float(np.mean([p > 0.5 for p in pcts])) if pcts else None}
    return out


def bsts_event(y: np.ndarray, X: np.ndarray, event: int, pre: int = 120, post: int = 60,
               reps: int = 400, seed: int = 3) -> dict | None:
    """Abnormal cumulative return after index `event` (first post-event session)."""
    import statsmodels.api as sm

    start = max(0, event - pre)
    end = min(len(y), event + post)
    if event - start < 40 or end - event < 1:
        return None
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    if not ok[start:end].all():
        return None
    mod = sm.tsa.UnobservedComponents(y[start:event], level="llevel", exog=X[start:event])
    res = mod.fit(disp=False, maxiter=200)
    steps = end - event
    fc = res.get_forecast(steps, exog=X[event:end]).predicted_mean
    sims = res.simulate(steps, repetitions=reps, anchor="end", exog=X[event:end],
                        rng=np.random.default_rng(seed))
    sims = np.asarray(sims).reshape(steps, -1)
    actual = y[event:end]
    car = np.cumsum(actual - fc)
    sim_car = np.cumsum(actual[:, None] - sims, axis=0)
    lo, hi = np.quantile(sim_car, [0.05, 0.95], axis=1)
    p_two = float(min(1.0, 2 * min((sim_car[-1] <= 0).mean(), (sim_car[-1] >= 0).mean())))
    return {"car": car.tolist(), "lo": lo.tolist(), "hi": hi.tolist(), "actual": actual.tolist(),
            "predicted": np.asarray(fc).tolist(), "p_value": p_two, "pre": event - start, "post": steps}
