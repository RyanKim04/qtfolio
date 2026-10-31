"""Stress tests of the current book.

- Historical replay: each holding's CAD return over a past crisis window.
  Holdings that traded then replay their own prices; younger ones are
  factor-mapped: today's factor betas applied to the factors' daily moves in
  the window, compounded (so a 3× ETF's daily reset decays as it would have).
- Conditional (correlational) shocks: the Gaussian conditional expectation of
  every other driver and holding given the shocked drivers. This is what most
  stress tools report; services/risk/causal.py gives the interventional answer.
- Reverse stress: the most plausible move (minimum Mahalanobis distance) that
  produces a given loss, in driver space and in holding space.
- Monte Carlo: one-month filtered historical simulation of the book.
"""

import math
from datetime import date

import numpy as np
from scipy import stats

from .data import Panel, complete_rows
from .var import ewma_vol

# (key, label, start = last close before the selloff, end = trough close).
SCENARIOS: list[tuple[str, str, date, date]] = [
    ("gfc", "2008 financial crisis", date(2008, 9, 12), date(2009, 3, 9)),
    ("downgrade", "2011 US downgrade", date(2011, 7, 22), date(2011, 10, 3)),
    ("china", "2015 China devaluation", date(2015, 8, 17), date(2015, 8, 25)),
    ("q4_2018", "2018 Q4 selloff", date(2018, 9, 20), date(2018, 12, 24)),
    ("covid", "2020 COVID crash", date(2020, 2, 19), date(2020, 3, 23)),
    ("rates_2022", "2022 rate shock", date(2022, 1, 3), date(2022, 10, 12)),
    ("svb", "2023 regional banks", date(2023, 3, 8), date(2023, 3, 13)),
    ("carry_2024", "2024 yen carry unwind", date(2024, 7, 10), date(2024, 8, 5)),
    ("deepseek", "2025 DeepSeek shock", date(2025, 1, 24), date(2025, 1, 27)),
    ("tariff_2025", "2025 tariff shock", date(2025, 2, 19), date(2025, 4, 8)),
]
COVERAGE = 0.95  # share of window sessions a holding needs to replay its own prices
MC_PATHS = 10_000
MC_DAYS = 21


def _betas(y: np.ndarray, X: np.ndarray) -> np.ndarray | None:
    ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
    if ok.sum() < X.shape[1] + 20:
        return None
    A = np.column_stack([np.ones(ok.sum()), X[ok]])
    coef, *_ = np.linalg.lstsq(A, y[ok], rcond=None)
    return coef[1:]  # DECISION: no alpha carried into a stress window


def historical(full: Panel, recent: Panel) -> list[dict]:
    """`full` holds the long history (from 2008); `recent` is the estimation
    window for factor betas."""
    out = []
    mkt = full.factor_keys.index("mkt")
    fx_i = full.factor_keys.index("usdcad")
    for key, label, start, end in SCENARIOS:
        win = full.between(start, end)
        if len(win.dates) == 0:
            continue
        fav = np.isfinite(win.F).all(axis=0)  # factors that existed throughout the window
        rows = []
        port = 0.0
        for j, a in enumerate(full.assets):
            r = win.R[:, j]
            if np.isfinite(r).mean() >= COVERAGE:
                ret, method = float(np.prod(1 + np.nan_to_num(r)) - 1), "replay"
            else:
                b = _betas(recent.R[:, j], recent.F[:, fav])
                if b is None:
                    ret, method = None, "unavailable"
                else:
                    ret, method = float(np.prod(1 + win.F[:, fav] @ b) - 1), "factor"
            rows.append({"asset": a, "return": ret, "method": method, "weight": float(full.weights[j])})
            if ret is not None:
                port += full.weights[j] * ret
        valid = [x for x in rows if x["return"] is not None]
        worst = min(valid, key=lambda x: x["return"]) if valid else None
        out.append({
            "key": key, "label": label, "start": start.isoformat(), "end": end.isoformat(),
            "sessions": len(win.dates), "portfolio": port, "pnl_cad": port * full.total_cad,
            "market": float(np.prod(1 + np.nan_to_num(win.F[:, mkt])) - 1),
            "usdcad": float(np.prod(1 + np.nan_to_num(win.F[:, fx_i])) - 1),
            "worst": worst["asset"] if worst else None,
            "factor_mapped": [x["asset"] for x in rows if x["method"] == "factor"],
            "holdings": rows,
        })
    return out


def conditional(D: np.ndarray, R: np.ndarray, w: np.ndarray, shocks: dict[int, float]) -> dict:
    """E[everything | shocked drivers = x] under joint normality (zero mean)."""
    ok = complete_rows(D, R)
    Z = np.column_stack([D[ok], R[ok]])
    S = np.cov(Z, rowvar=False)
    idx = sorted(shocks)
    x = np.array([shocks[i] for i in idx])
    Sss = S[np.ix_(idx, idx)]
    cond = S[:, idx] @ np.linalg.solve(Sss, x)
    m = D.shape[1]
    drivers, assets = cond[:m], cond[m:]
    return {"drivers": drivers, "assets": assets, "portfolio": float(w @ assets)}


def reverse(D: np.ndarray, R: np.ndarray, w: np.ndarray, loss: float, horizon: int) -> dict:
    """Most plausible scenarios with portfolio return = −loss over `horizon`
    sessions: x* = −L Σβ / (βᵀΣβ), distance d = L / √(βᵀΣβ)."""
    ok = complete_rows(D, R)
    Dk, Rk = D[ok], R[ok]
    rp = Rk @ w
    A = np.column_stack([np.ones(len(rp)), Dk])
    coef, *_ = np.linalg.lstsq(A, rp, rcond=None)
    beta = coef[1:]
    fitted = A @ coef
    r2 = 1 - np.var(rp - fitted) / np.var(rp) if np.var(rp) else 0.0
    Sd = np.cov(Dk, rowvar=False) * horizon
    q = float(beta @ Sd @ beta)
    xd = -loss * (Sd @ beta) / q if q > 0 else np.zeros(D.shape[1])
    dd = loss / math.sqrt(q) if q > 0 else math.inf
    Sr = np.cov(Rk, rowvar=False).reshape(R.shape[1], R.shape[1]) * horizon
    qa = float(w @ Sr @ w)
    xa = -loss * (Sr @ w) / qa if qa > 0 else np.zeros(R.shape[1])
    da = loss / math.sqrt(qa) if qa > 0 else math.inf

    def every_years(d):
        p = stats.norm.sf(d)
        return float(horizon / 252 / p) if p > 0 else None

    return {
        "loss": loss, "horizon": horizon,
        "drivers": xd, "driver_distance": dd, "driver_r2": float(r2), "driver_every_years": every_years(dd),
        "assets": xa, "asset_distance": da, "asset_every_years": every_years(da),
    }


def monte_carlo(R: np.ndarray, w: np.ndarray, days: int = MC_DAYS, paths: int = MC_PATHS, seed: int = 11) -> dict:
    """Filtered historical simulation: each holding's returns devolatilized by
    its own EWMA vol, whole days resampled (keeping the cross-section's joint
    tail), rescaled to today's vols, compounded over `days`."""
    rng = np.random.default_rng(seed)
    T, N = R.shape
    Z = np.empty_like(R)
    now = np.empty(N)
    for j in range(N):
        s = ewma_vol(R[:, j])
        Z[:, j] = R[:, j] / np.where(s[:-1] > 0, s[:-1], np.nan)
        now[j] = s[-1]
    Z = Z[np.isfinite(Z).all(axis=1)]
    pick = rng.integers(0, len(Z), size=(paths, days))
    sims = Z[pick] * now  # paths × days × N
    cum = np.prod(1 + sims, axis=1) - 1  # paths × N, buy and hold
    port = cum @ w
    hist, edges = np.histogram(port, bins=60)
    out = {"days": days, "paths": paths, "median": float(np.median(port)),
           "p_loss_10": float((port <= -0.10).mean()), "p_loss_20": float((port <= -0.20).mean()),
           "hist": {"edges": edges.tolist(), "counts": hist.tolist()}}
    for conf in (0.95, 0.99):
        q = float(np.quantile(port, 1 - conf))
        out[f"var{int(conf * 100)}"] = -q
        out[f"es{int(conf * 100)}"] = float(-port[port <= q].mean())
    return out
