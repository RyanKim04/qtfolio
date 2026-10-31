"""Time-series factor exposures on the ETF factor set (data.FACTORS).

Each holding's CAD returns are regressed on the factor returns by OLS, with
Newey-West (HAC, 5 lags) standard errors because daily returns are
heteroskedastic. Portfolio exposures are the weight-sum of holding exposures.
Portfolio variance splits into factor risk bᵀΣ_f b (Euler-decomposed per
factor) and specific risk Σ w_i² d_i.
"""

import math

import numpy as np

from .covariance import ANN, factor_model, sample_cov

HAC_LAGS = 5


def exposures(R: np.ndarray, F: np.ndarray, assets: list[str], keys: list[str]) -> list[dict]:
    import statsmodels.api as sm

    out = []
    fok = np.isfinite(F).all(axis=1)
    for j, a in enumerate(assets):
        ok = fok & np.isfinite(R[:, j])
        if ok.sum() < len(keys) + 20:
            out.append({"asset": a, "n": int(ok.sum()), "betas": None})
            continue
        X = sm.add_constant(F[ok])
        res = sm.OLS(R[ok, j], X).fit(cov_type="HAC", cov_kwds={"maxlags": HAC_LAGS})
        out.append({
            "asset": a,
            "n": int(ok.sum()),
            "alpha_annual": float(res.params[0] * ANN),
            "r2": float(res.rsquared),
            "specific_vol": float(math.sqrt(res.mse_resid * ANN)),
            "betas": {k: {"beta": float(res.params[i + 1]), "t": float(res.tvalues[i + 1])} for i, k in enumerate(keys)},
        })
    return out


def risk_split(R: np.ndarray, F: np.ndarray, w: np.ndarray, keys: list[str]) -> dict:
    fm = factor_model(R, F)
    Fk = F[np.isfinite(F).all(axis=1)]
    Sf = sample_cov(Fk)
    b = fm["B"].T @ w
    factor_var = float(b @ Sf @ b)
    specific_var = float((w**2 * fm["resid_var"]).sum())
    total = factor_var + specific_var
    contrib = b * (Sf @ b)
    return {
        "vol": math.sqrt(total * ANN) if total > 0 else 0.0,
        "factor_share": factor_var / total if total else None,
        "specific_share": specific_var / total if total else None,
        "factors": [
            {"key": k, "exposure": float(b[i]), "risk_share": float(contrib[i] / total) if total else None,
             "factor_vol": float(math.sqrt(Sf[i, i] * ANN))}
            for i, k in enumerate(keys)
        ],
    }
