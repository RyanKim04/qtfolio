"""Covariance estimators and the risk decomposition built on them.

- sample: plain sample covariance.
- lw: Ledoit-Wolf linear shrinkage (2004) of the correlations toward the identity.
- ewma: RiskMetrics exponentially weighted, λ = 0.94 (≈ 11-day half-life), the
  current-regime view.
- factor: B Σ_f Bᵀ + D from the ETF factor model, the structured estimate
  (Barra-style) that tolerates short histories.

Decomposition is Euler's: each asset's contribution w_i (Σw)_i / σ_p sums to σ_p.
Diversification uses Choueifaty's diversification ratio, the Herfindahl
effective N, and Meucci's Effective Number of Bets on principal components and
on minimum-torsion bets (Meucci, Santangelo & Deguest 2015).
"""

import numpy as np
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

EWMA_LAMBDA = 0.94
ANN = 252


def sample_cov(X: np.ndarray) -> np.ndarray:
    return np.cov(X, rowvar=False, ddof=1).reshape(X.shape[1], X.shape[1])


def ledoit_wolf(X: np.ndarray) -> np.ndarray:
    """Shrinks the correlation matrix toward the identity, then restores each
    asset's own variance. DECISION: the scaled-identity target applied to raw
    returns pulls every variance toward the average, which for a book mixing
    3× ETFs with large caps cut GOOG/GOOG.TO's correlation from 0.97 to 0.87."""
    from sklearn.covariance import LedoitWolf

    sd = X.std(axis=0, ddof=1)
    sd = np.where(sd > 0, sd, 1.0)
    C = LedoitWolf().fit(X / sd).covariance_
    d = np.sqrt(np.diag(C))
    C = C / np.outer(d, d)
    return C * np.outer(sd, sd)


def ewma_cov(X: np.ndarray, lam: float = EWMA_LAMBDA) -> np.ndarray:
    """Weights λ^(T−1−t), normalized; demeaned (zero-mean RiskMetrics differs
    by the squared daily mean, negligible)."""
    T = X.shape[0]
    w = lam ** np.arange(T - 1, -1, -1)
    w /= w.sum()
    Xc = X - (w @ X)
    return (Xc * w[:, None]).T @ Xc


def factor_model(R: np.ndarray, F: np.ndarray) -> dict:
    """Per-asset OLS on the factors over each asset's own available days.
    Returns betas B (N×K), alphas, residual variances d (N) and R²."""
    N, K = R.shape[1], F.shape[1]
    B, alpha, d, r2, nobs = np.zeros((N, K)), np.zeros(N), np.zeros(N), np.zeros(N), np.zeros(N, dtype=int)
    fok = np.isfinite(F).all(axis=1)
    for j in range(N):
        ok = fok & np.isfinite(R[:, j])
        nobs[j] = ok.sum()
        if ok.sum() < K + 10:
            d[j] = np.nanvar(R[:, j]) if np.isfinite(R[:, j]).sum() > 1 else 0.0
            continue
        X = np.column_stack([np.ones(ok.sum()), F[ok]])
        coef, *_ = np.linalg.lstsq(X, R[ok, j], rcond=None)
        resid = R[ok, j] - X @ coef
        alpha[j], B[j] = coef[0], coef[1:]
        d[j] = resid.var(ddof=K + 1)
        tot = R[ok, j].var()
        r2[j] = 1 - resid.var() / tot if tot else 0.0
    return {"B": B, "alpha": alpha, "resid_var": d, "r2": r2, "nobs": nobs}


def factor_cov(R: np.ndarray, F: np.ndarray, lam: float | None = None) -> np.ndarray:
    fm = factor_model(R, F)
    Fk = F[np.isfinite(F).all(axis=1)]
    Sf = ewma_cov(Fk, lam) if lam else sample_cov(Fk)
    return fm["B"] @ Sf @ fm["B"].T + np.diag(fm["resid_var"])


def estimate(X: np.ndarray, method: str, F: np.ndarray | None = None) -> np.ndarray:
    if method == "sample":
        return sample_cov(X)
    if method == "lw":
        return ledoit_wolf(X)
    if method == "ewma":
        return ewma_cov(X)
    if method == "factor":
        return factor_cov(X, F)
    raise ValueError(f"unknown covariance method {method}")


def corr_from_cov(S: np.ndarray) -> np.ndarray:
    sd = np.sqrt(np.diag(S))
    with np.errstate(divide="ignore", invalid="ignore"):
        C = S / np.outer(sd, sd)
    C[~np.isfinite(C)] = 0.0
    np.fill_diagonal(C, 1.0)
    return C


def cluster_order(C: np.ndarray) -> list[int]:
    """Leaf order of single-linkage clustering on the correlation distance
    √(½(1−ρ)) (López de Prado's HRP quasi-diagonalization)."""
    n = C.shape[0]
    if n < 3:
        return list(range(n))
    dist = np.sqrt(np.clip(0.5 * (1 - C), 0, None))
    np.fill_diagonal(dist, 0.0)
    return [int(i) for i in leaves_list(linkage(squareform(dist, checks=False), method="single"))]


def euler(w: np.ndarray, S: np.ndarray) -> dict:
    """Portfolio vol and per-asset marginal/component contributions (daily)."""
    Sw = S @ w
    var = float(w @ Sw)
    vol = float(np.sqrt(max(var, 0.0)))
    mctr = Sw / vol if vol else np.zeros_like(w)
    ctr = w * mctr
    return {
        "vol": vol,
        "mctr": mctr,
        "ctr": ctr,
        "pct": ctr / vol if vol else np.zeros_like(w),
        "beta": Sw / var if var else np.zeros_like(w),
    }


def diversification_ratio(w: np.ndarray, S: np.ndarray) -> float | None:
    vol = float(np.sqrt(w @ S @ w))
    return float(w @ np.sqrt(np.diag(S)) / vol) if vol else None


def herfindahl_n(w: np.ndarray) -> float | None:
    s = np.abs(w).sum()
    if not s:
        return None
    p = np.abs(w) / s
    return float(1 / (p**2).sum())


def _entropy_n(p: np.ndarray) -> float:
    p = p[p > 1e-15]
    p = p / p.sum()
    return float(np.exp(-(p * np.log(p)).sum()))


def enb_pca(w: np.ndarray, S: np.ndarray) -> float | None:
    """Meucci (2009): exposure to each principal component, its share of variance, exp(entropy)."""
    lam, E = np.linalg.eigh(S)
    b = E.T @ w
    contrib = b**2 * np.clip(lam, 0, None)
    tot = contrib.sum()
    return _entropy_n(contrib / tot) if tot > 0 else None


def _sqrtm_sym(A: np.ndarray) -> np.ndarray:
    lam, E = np.linalg.eigh(A)
    return E @ np.diag(np.sqrt(np.clip(lam, 0, None))) @ E.T


def minimum_torsion(S: np.ndarray, tol: float = 1e-12, max_iter: int = 10_000) -> np.ndarray:
    """Meucci-Santangelo-Deguest minimum-torsion transformation t: the
    uncorrelated bets t·r closest (in tracking error) to the original assets."""
    sigma = np.sqrt(np.diag(S))
    C = S / np.outer(sigma, sigma)
    c = _sqrtm_sym(C)
    n = len(sigma)
    d = np.ones(n)
    pi_prev = np.zeros((n, n))
    pi = pi_prev
    for _ in range(max_iter):
        U = np.diag(d) @ c @ c @ np.diag(d)
        q = np.linalg.solve(_sqrtm_sym(U), np.diag(d) @ c)
        d = np.diag(q @ c).copy()
        pi = np.diag(d) @ q
        if np.linalg.norm(pi - pi_prev) <= tol:
            break
        pi_prev = pi
    x = pi @ np.linalg.inv(c)
    return np.diag(sigma) @ x @ np.diag(1 / sigma)


def enb_torsion(w: np.ndarray, S: np.ndarray) -> tuple[float | None, np.ndarray]:
    """ENB on minimum-torsion bets, plus each bet's share of variance (one
    per asset, so it reads next to the asset's weight)."""
    if S.shape[0] == 0:
        return None, np.zeros(0)
    sd = np.sqrt(np.diag(S))
    keep = sd > 1e-12  # a zero-variance column makes the torsion singular
    shares = np.zeros(len(w))
    if keep.sum() == 0:
        return None, shares
    Sk, wk = S[np.ix_(keep, keep)], w[keep]
    t = minimum_torsion(Sk)
    b = np.linalg.solve(t.T, wk)  # exposures to the bets
    v = np.diag(t @ Sk @ t.T)
    contrib = b**2 * v
    tot = contrib.sum()
    if tot <= 0:
        return None, shares
    shares[keep] = contrib / tot
    return _entropy_n(contrib / tot), shares
