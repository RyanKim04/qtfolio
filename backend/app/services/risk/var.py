"""Value at Risk and Expected Shortfall, losses reported as positive fractions.

Methods (one-day, scaled to longer horizons by √h):
- normal: zero-mean Gaussian on the covariance estimate.
- cornish_fisher: Gaussian quantile corrected for sample skew and kurtosis.
- student_t: Student-t with the tail index fitted to the portfolio returns.
- historical: empirical quantile of the current book's returns over the window.
- fhs: filtered historical simulation (Barone-Adesi et al. 1999): returns
  devolatilized by their EWMA vol and rescaled to today's vol, the headline.

Backtests: Kupiec's proportion-of-failures and Christoffersen's independence
likelihood-ratio tests on the realized portfolio returns.
"""

import math

import numpy as np
from scipy import stats

from .covariance import EWMA_LAMBDA


def _cf_quantile(z: float, s: float, k: float) -> float:
    """Cornish-Fisher quantile; k is excess kurtosis."""
    return z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36


def ewma_vol(r: np.ndarray, lam: float = EWMA_LAMBDA, seed_n: int = 30) -> np.ndarray:
    """σ_t used for day t (from returns before t); the last entry is tomorrow's."""
    var = np.empty(len(r) + 1)
    var[0] = np.var(r[:seed_n]) if len(r) >= 2 else 0.0
    for t, x in enumerate(r):
        var[t + 1] = lam * var[t] + (1 - lam) * x * x
    return np.sqrt(var)


def hist_var_es(r: np.ndarray, conf: float) -> tuple[float, float]:
    q = float(np.quantile(r, 1 - conf))
    tail = r[r <= q]
    return -q, float(-tail.mean()) if len(tail) else -q


def fhs_sample(r: np.ndarray) -> np.ndarray:
    sig = ewma_vol(r)
    z = r / np.where(sig[:-1] > 0, sig[:-1], np.nan)
    z = z[np.isfinite(z)]
    return z * sig[-1]


def var_table(r: np.ndarray, vol: float, conf: float, horizon: int = 1) -> dict:
    """VaR/ES by method for daily portfolio returns `r` (current book) and its
    model vol (daily σ from the chosen covariance)."""
    h = math.sqrt(horizon)
    z = stats.norm.ppf(1 - conf)  # negative
    skew = float(stats.skew(r, bias=False))
    exk = float(stats.kurtosis(r, fisher=True, bias=False))
    out = {}
    out["normal"] = {"var": -z * vol * h, "es": vol * stats.norm.pdf(z) / (1 - conf) * h}
    # ES under Cornish-Fisher: mean of the corrected quantiles over the tail.
    us = np.linspace(1 - conf, 0, 200, endpoint=False)[1:] if conf < 1 else np.array([])
    cf_q = [_cf_quantile(stats.norm.ppf(u), skew, exk) for u in us]
    out["cornish_fisher"] = {"var": -_cf_quantile(z, skew, exk) * vol * h,
                             "es": -float(np.mean(cf_q)) * vol * h if cf_q else None}
    nu, _, _ = stats.t.fit(r, floc=0)
    nu = float(max(nu, 2.1))
    scale = vol * math.sqrt((nu - 2) / nu)
    tq = stats.t.ppf(1 - conf, nu)
    out["student_t"] = {"var": -tq * scale * h,
                        "es": scale * stats.t.pdf(tq, nu) / (1 - conf) * (nu + tq**2) / (nu - 1) * h, "nu": nu}
    v, e = hist_var_es(r, conf)
    out["historical"] = {"var": v * h, "es": e * h}
    v, e = hist_var_es(fhs_sample(r), conf)
    out["fhs"] = {"var": v * h, "es": e * h}
    return out


def component_var(w: np.ndarray, S: np.ndarray, conf: float) -> np.ndarray:
    """Gaussian Euler components; they sum to the normal VaR."""
    vol = math.sqrt(max(float(w @ S @ w), 0.0))
    if not vol:
        return np.zeros_like(w)
    return -stats.norm.ppf(1 - conf) * w * (S @ w) / vol


def component_es(R: np.ndarray, w: np.ndarray, conf: float) -> np.ndarray:
    """Historical Euler components: each asset's average loss on the tail days
    of the portfolio; they sum to the historical ES."""
    rp = R @ w
    q = np.quantile(rp, 1 - conf)
    tail = rp <= q
    return -(R[tail] * w).mean(axis=0) if tail.any() else np.zeros_like(w)


def kupiec(exceptions: int, n: int, p: float) -> dict:
    """Proportion of failures: H0 exception rate = p."""
    x = exceptions
    if n == 0:
        return {"lr": None, "p_value": None}
    pi = x / n

    def ll(q):
        a = (n - x) * math.log(1 - q) if q < 1 else (0 if x == n else -math.inf)
        b = x * math.log(q) if q > 0 else (0 if x == 0 else -math.inf)
        return a + b

    lr = -2 * (ll(p) - ll(pi))
    return {"lr": lr, "p_value": float(1 - stats.chi2.cdf(lr, 1))}


def christoffersen(hits: np.ndarray) -> dict:
    """Independence of exceptions (first-order Markov)."""
    h = hits.astype(int)
    prev, cur = h[:-1], h[1:]
    n00 = int(((prev == 0) & (cur == 0)).sum())
    n01 = int(((prev == 0) & (cur == 1)).sum())
    n10 = int(((prev == 1) & (cur == 0)).sum())
    n11 = int(((prev == 1) & (cur == 1)).sum())

    def xlogy(x, y):
        return x * math.log(y) if x > 0 else 0.0

    p01 = n01 / (n00 + n01) if n00 + n01 else 0.0
    p11 = n11 / (n10 + n11) if n10 + n11 else 0.0
    p = (n01 + n11) / max(1, n00 + n01 + n10 + n11)
    l0 = xlogy(n00 + n10, 1 - p) + xlogy(n01 + n11, p)
    l1 = xlogy(n00, 1 - p01) + xlogy(n01, p01) + xlogy(n10, 1 - p11) + xlogy(n11, p11)
    lr = -2 * (l0 - l1)
    return {"lr": lr, "p_value": float(1 - stats.chi2.cdf(lr, 1)), "clustered": n11}


def backtest(r: np.ndarray, conf: float, window: int = 250, method: str = "fhs") -> dict:
    """One-day-ahead VaR from the trailing `window` returns, compared with the
    next day's realized return."""
    preds, hits = [], []
    for t in range(window, len(r)):
        hist = r[t - window:t]
        v, _ = hist_var_es(fhs_sample(hist) if method == "fhs" else hist, conf)
        preds.append(v)
        hits.append(r[t] < -v)
    hits_a = np.array(hits, dtype=bool)
    n, x = len(hits), int(hits_a.sum())
    return {
        "method": method, "n": n, "exceptions": x, "expected": n * (1 - conf),
        "kupiec": kupiec(x, n, 1 - conf), "christoffersen": christoffersen(hits_a) if n > 1 else None,
        "var": preds,
    }
