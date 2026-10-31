"""Realized performance statistics on the portfolio's TWR series.

Daily returns are measured on market sessions only (series.trading_sample), and
annualized with 252 sessions. The Sharpe ratio's estimation error is reported
with the Probabilistic Sharpe Ratio and Minimum Track Record Length (Bailey &
López de Prado 2012), which correct for skew and fat tails.
"""

import math
from datetime import date

import numpy as np
from scipy import stats

ANN = 252


def drawdowns(idx: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(idx)
    return idx / peak - 1


def drawdown_stats(dates: list[date], idx: np.ndarray) -> dict:
    """Max drawdown with its peak/trough/recovery dates, the longest time under
    water (calendar days) and the current drawdown."""
    dd = drawdowns(idx)
    t = int(np.argmin(dd))
    peak_i = int(np.argmax(idx[: t + 1])) if t else 0
    rec = next((i for i in range(t, len(idx)) if idx[i] >= idx[peak_i]), None)
    longest, start = 0, None
    for i, v in enumerate(dd):
        if v < 0 and start is None:
            start = i - 1 if i else 0
        if v >= 0 and start is not None:
            longest = max(longest, (dates[i] - dates[start]).days)
            start = None
    if start is not None:
        longest = max(longest, (dates[-1] - dates[start]).days)
    return {
        "max_drawdown": float(dd[t]),
        "peak": dates[peak_i].isoformat(),
        "trough": dates[t].isoformat(),
        "recovered": dates[rec].isoformat() if rec is not None and dd[t] < 0 else None,
        "longest_underwater_days": longest,
        "current_drawdown": float(dd[-1]),
        "ulcer_index": float(np.sqrt(np.mean(dd**2))),
    }


def psr(sr: float, n: int, skew: float, kurt: float, sr_star: float = 0.0) -> float | None:
    """P(true per-period Sharpe > sr_star). `kurt` is raw (normal = 3)."""
    den = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if n < 3 or den <= 0:
        return None
    return float(stats.norm.cdf((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(den)))


def min_trl(sr: float, skew: float, kurt: float, sr_star: float = 0.0, conf: float = 0.95) -> float | None:
    """Observations needed for PSR(sr_star) ≥ conf; None when sr ≤ sr_star."""
    if sr <= sr_star:
        return None
    z = stats.norm.ppf(conf)
    return float(1 + (1 - skew * sr + (kurt - 1) / 4 * sr**2) * (z / (sr - sr_star)) ** 2)


def performance(dates: list[date], idx: np.ndarray, rf_daily: np.ndarray, bench: np.ndarray | None = None) -> dict:
    """`idx` is the TWR index on `dates` (market sessions, first = window base);
    `rf_daily` and `bench` are aligned to the returns (len(dates) − 1)."""
    r = idx[1:] / idx[:-1] - 1
    n = len(r)
    out: dict = {"n": n}
    if n < 5:
        return out
    days = (dates[-1] - dates[0]).days or 1
    total = float(idx[-1] / idx[0] - 1)
    ex = r - rf_daily
    sd = float(np.std(r, ddof=1))
    sd_ex = float(np.std(ex, ddof=1))
    downside = float(np.sqrt(np.mean(np.minimum(ex, 0) ** 2)))
    sr = float(np.mean(ex) / sd_ex) if sd_ex else 0.0
    skew = float(stats.skew(r, bias=False))
    kurt = float(stats.kurtosis(r, fisher=False, bias=False))
    gains, losses = np.maximum(r, 0).sum(), np.maximum(-r, 0).sum()
    cagr = float((1 + total) ** (365.25 / days) - 1)
    dd = drawdown_stats(dates, idx)
    mtrl = min_trl(sr, skew, kurt)
    q05 = float(np.quantile(r, 0.05))
    out.update({
        "total_return": total,
        "cagr": cagr,
        "annualized": days >= 365,
        "vol": sd * math.sqrt(ANN),
        "downside_dev": downside * math.sqrt(ANN),
        "sharpe": sr * math.sqrt(ANN),
        "sortino": float(np.mean(ex) / downside * math.sqrt(ANN)) if downside else None,
        "calmar": cagr / -dd["max_drawdown"] if dd["max_drawdown"] < 0 else None,
        "omega": float(gains / losses) if losses else None,
        "skew": skew,
        "excess_kurtosis": kurt - 3,
        "psr": psr(sr, n, skew, kurt),
        "min_trl_years": mtrl / ANN if mtrl else None,
        "track_years": n / ANN,
        "hit_rate": float((r > 0).mean()),
        "var95_hist": -q05,
        "es95_hist": float(-r[r <= q05].mean()),
        "rf_annual": float(np.mean(rf_daily) * ANN),
        **dd,
    })
    if bench is not None and np.isfinite(bench).sum() > 5:
        ok = np.isfinite(bench)
        rb, bb, rfb = r[ok], bench[ok], rf_daily[ok]
        var_b = float(np.var(bb, ddof=1))
        beta = float(np.cov(rb, bb, ddof=1)[0, 1] / var_b) if var_b else None
        active = rb - bb
        te = float(np.std(active, ddof=1) * math.sqrt(ANN))
        up, down = bb > 0, bb < 0
        out["benchmark"] = {
            "n": int(ok.sum()),
            "total_return": float(np.prod(1 + bb) - 1),
            "beta": beta,
            "alpha": float((np.mean(rb - rfb) - beta * np.mean(bb - rfb)) * ANN) if beta is not None else None,
            "correlation": float(np.corrcoef(rb, bb)[0, 1]),
            "tracking_error": te,
            "information_ratio": float(np.mean(active) * ANN / te) if te else None,
            # DECISION: arithmetic daily capture ratios (mean on up/down benchmark days).
            "up_capture": float(rb[up].mean() / bb[up].mean()) if up.any() else None,
            "down_capture": float(rb[down].mean() / bb[down].mean()) if down.any() else None,
        }
    return out


def rolling(dates: list[date], idx: np.ndarray, rf_daily: np.ndarray, bench: np.ndarray | None,
            window: int = 63, max_points: int = 400) -> dict:
    """Rolling annualized vol, Sharpe and beta over `window` sessions."""
    import pandas as pd

    r = pd.Series(idx[1:] / idx[:-1] - 1)
    ex = r - rf_daily
    vol = r.rolling(window).std() * math.sqrt(ANN)
    sharpe = ex.rolling(window).mean() / ex.rolling(window).std() * math.sqrt(ANN)
    beta = None
    if bench is not None:
        b = pd.Series(bench)
        beta = r.rolling(window).cov(b) / b.rolling(window).var()
    rd = dates[1:]
    step = max(1, len(rd) // max_points)
    keep = list(range(window - 1, len(rd), step))
    if keep and keep[-1] != len(rd) - 1:
        keep.append(len(rd) - 1)

    def pick(s):
        return [None if s is None or not np.isfinite(s.iloc[i]) else round(float(s.iloc[i]), 5) for i in keep]

    return {"window": window, "dates": [rd[i].isoformat() for i in keep], "vol": pick(vol),
            "sharpe": pick(sharpe), "beta": pick(beta) if beta is not None else None}


def underwater(dates: list[date], idx: np.ndarray, max_points: int = 500) -> dict:
    dd = drawdowns(idx)
    step = max(1, len(dates) // max_points)
    keep = list(range(0, len(dates), step))
    if keep[-1] != len(dates) - 1:
        keep.append(len(dates) - 1)
    return {"dates": [dates[i].isoformat() for i in keep], "drawdown": [round(float(dd[i]), 5) for i in keep]}


def monthly_returns(dates: list[date], idx: np.ndarray) -> list[dict]:
    """Calendar-month TWR, each measured from the previous month's last value
    (the first month from the window base)."""
    out, base, cur = [], idx[0], (dates[0].year, dates[0].month)
    last = idx[0]
    for d, v in zip(dates[1:], idx[1:]):
        key = (d.year, d.month)
        if key != cur:
            out.append({"year": cur[0], "month": cur[1], "r": float(last / base - 1)})
            base, cur = last, key
        last = v
    out.append({"year": cur[0], "month": cur[1], "r": float(last / base - 1)})
    # A base month holding only the window's opening value has no return.
    first = (dates[0].year, dates[0].month)
    if not any((d.year, d.month) == first for d in dates[1:]):
        out = out[1:]
    return out
