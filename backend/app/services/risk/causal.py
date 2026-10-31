"""Causal view of macro risk: how much of a shock reaches the book, and by
which channel.

1. Structural causal model. A prior DAG over the macro drivers (data.DRIVERS)
   plus an edge from every driver into every holding, fitted as a linear SEM:
   each node on its contemporaneous parents, with its own and its parents'
   previous-day values as controls. A stress is an intervention do(X = x): the
   shocked driver is set and its incoming edges cut, and the effect flows only
   down the graph. The portfolio effect splits exactly into path-specific
   channels (products of edge coefficients), direct (driver → holdings) vs
   indirect (through other drivers). Bootstrap CIs come from a stationary
   bootstrap (Politis & Romano 1994) of the sessions.
   The correlational answer E[r | X = x] also absorbs the shocked driver's
   common causes (shocking the dollar drags in whatever moved rates);
   showing both makes that confounding visible.
2. Local projections (Jordà 2005): the portfolio's cumulative response over
   0–20 sessions to the driver's structural shock (its SEM residual, i.e.
   recursive identification), with Newey-West errors.
3. PCMCI-lite (after Runge et al. 2019): lagged links by PC1 condition
   selection + momentary conditional independence tests, and the
   contemporaneous skeleton by PC-style partial-correlation tests, as a
   check of the prior DAG against the data. Contemporaneous links found this
   way are undirected; the data alone cannot orient them.

DECISION: the prior DAG orders shocks macro → risk appetite → equities:
rates and oil are exogenous on the day; the dollar follows rates; credit
follows rates and oil; VIX follows rates, oil and credit; US equities follow
all of those; USD/CAD follows oil, the dollar and equities (CAD is a
commodity, risk-on currency). Holdings depend on every driver.
"""

import itertools
import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

from .data import complete_rows

PRIOR_PARENTS: dict[str, list[str]] = {
    "rates": [],
    "oil": [],
    "usd": ["rates"],
    "credit": ["rates", "oil"],
    "vix": ["rates", "oil", "credit"],
    "equity": ["rates", "oil", "usd", "credit", "vix"],
    "usdcad": ["oil", "usd", "equity"],
}
BOOT = 200
MEAN_BLOCK = 10


@dataclass
class SCM:
    nodes: list[str]
    m: int  # first m nodes are drivers
    A: np.ndarray  # A[v, p]: contemporaneous effect of p on v
    resid: np.ndarray  # structural residuals, rows aligned to `rows`
    rows: np.ndarray  # indices (into the input arrays) of the fitted sessions
    sigma: np.ndarray

    def parents(self, v: int) -> list[int]:
        return [int(p) for p in np.nonzero(self.A[v])[0]]


def _parents_idx(driver_keys: list[str], m: int, n_assets: int) -> list[list[int]]:
    pos = {k: i for i, k in enumerate(driver_keys)}
    out = [[pos[p] for p in PRIOR_PARENTS[k]] for k in driver_keys]
    out += [list(range(m))] * n_assets
    return out


def fit_scm(D: np.ndarray, R: np.ndarray, driver_keys: list[str], assets: list[str],
            rows: np.ndarray | None = None) -> SCM:
    X = np.column_stack([D, R])
    m, V = D.shape[1], X.shape[1]
    if rows is None:
        ok = complete_rows(X)
        rows = np.array([t for t in range(1, len(X)) if ok[t] and ok[t - 1]])
    pa = _parents_idx(driver_keys, m, R.shape[1])
    A = np.zeros((V, V))
    resid = np.zeros((len(rows), V))
    for v in range(V):
        p = pa[v]
        cols = [np.ones(len(rows))] + [X[rows, q] for q in p] + [X[rows - 1, v]] + [X[rows - 1, q] for q in p]
        Z = np.column_stack(cols)
        coef, *_ = np.linalg.lstsq(Z, X[rows, v], rcond=None)
        A[v, p] = coef[1:1 + len(p)]
        resid[:, v] = X[rows, v] - Z @ coef
    return SCM(driver_keys + assets, m, A, resid, rows, resid.std(axis=0, ddof=1))


def do(scm: SCM, shocks: dict[int, float]) -> np.ndarray:
    """One-session response of every node to do(X_k = x_k), deviations from baseline."""
    vals = np.zeros(len(scm.nodes))
    for v in range(len(scm.nodes)):  # driver order is topological, holdings last
        vals[v] = shocks[v] if v in shocks else scm.A[v] @ vals
    return vals


def channels(scm: SCM, w: np.ndarray, shocks: dict[int, float]) -> list[dict]:
    """Every driver path k → … → j that ends in the holdings, with its portfolio
    contribution x_k · Π(edges) · Σ_i w_i A[i, j]. Paths stop at other
    intervened drivers (their incoming edges are cut)."""
    m = scm.m
    into_book = w @ scm.A[m:, :m]  # portfolio loading on each driver, direct edge
    children = {p: [c for c in range(m) if scm.A[c, p] != 0] for p in range(m)}
    out = []
    for k, x in shocks.items():
        stack = [([k], x)]
        while stack:
            path, val = stack.pop()
            j = path[-1]
            out.append({"path": [scm.nodes[i] for i in path], "contribution": float(val * into_book[j])})
            for c in children[j]:
                if c in shocks or c in path:
                    continue
                stack.append((path + [c], val * scm.A[c, j]))
    out.sort(key=lambda c: -abs(c["contribution"]))
    return out


def _stationary_bootstrap(n: int, rng: np.random.Generator, mean_block: int = MEAN_BLOCK) -> np.ndarray:
    idx = np.empty(n, dtype=int)
    i = rng.integers(n)
    for t in range(n):
        if t == 0 or rng.random() < 1 / mean_block:
            i = rng.integers(n)
        else:
            i = (i + 1) % n
        idx[t] = i
    return idx


def shock_effects(D: np.ndarray, R: np.ndarray, w: np.ndarray, driver_keys: list[str], assets: list[str],
                  shocks: dict[int, float], boot: int = BOOT, seed: int = 5) -> dict:
    scm = fit_scm(D, R, driver_keys, assets)
    vals = do(scm, shocks)
    m = scm.m
    port = float(w @ vals[m:])
    ch = channels(scm, w, shocks)
    direct = sum(c["contribution"] for c in ch if len(c["path"]) == 1)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(boot):
        rows = scm.rows[_stationary_bootstrap(len(scm.rows), rng)]
        b = fit_scm(D, R, driver_keys, assets, rows=rows)
        draws.append(float(w @ do(b, shocks)[m:]))
    lo, hi = (np.quantile(draws, [0.05, 0.95]) if draws else (None, None))
    return {
        "portfolio": port, "direct": direct, "indirect": port - direct,
        "ci90": [float(lo), float(hi)] if draws else None,
        "drivers": vals[:m], "assets": vals[m:], "channels": ch, "scm": scm,
    }


def local_projection(shock: np.ndarray, y: np.ndarray, controls: np.ndarray, horizon: int = 20) -> dict:
    """β_h of Σ_{s=0..h} y_{t+s} on shock_t, controls dated t−1 or earlier."""
    import statsmodels.api as sm

    T = len(y)
    betas, lo, hi, ns = [], [], [], []
    csum = np.concatenate([[0.0], np.cumsum(y)])
    for h in range(horizon + 1):
        t = np.arange(0, T - h)
        resp = csum[t + h + 1] - csum[t]
        X = np.column_stack([shock[t], controls[t]])
        ok = np.isfinite(resp) & np.isfinite(X).all(axis=1)
        res = sm.OLS(resp[ok], sm.add_constant(X[ok])).fit(cov_type="HAC", cov_kwds={"maxlags": h + 1})
        b, se = float(res.params[1]), float(res.bse[1])
        betas.append(b)
        lo.append(b - 1.645 * se)
        hi.append(b + 1.645 * se)
        ns.append(int(ok.sum()))
    return {"beta": betas, "lo": lo, "hi": hi, "n": ns}


def lp_inputs(D: np.ndarray, R: np.ndarray, w: np.ndarray, driver_keys: list[str], assets: list[str]):
    """Structural shocks (SEM residuals) and portfolio returns on the SCM's rows,
    plus controls: the portfolio's two previous returns and every driver's
    previous value."""
    scm = fit_scm(D, R, driver_keys, assets)
    rows = scm.rows
    rp = np.nan_to_num(R) @ w
    lag2 = np.where(rows >= 2, rp[np.maximum(rows - 2, 0)], np.nan)
    controls = np.column_stack([rp[rows - 1], lag2, D[rows - 1]])
    return scm, rp[rows], controls


# -- PCMCI-lite ---------------------------------------------------------------

def parcorr(x: np.ndarray, y: np.ndarray, Z: np.ndarray | None) -> tuple[float, float]:
    """Partial correlation of x and y given Z, with its two-sided p-value."""
    n = len(x)
    if Z is not None and Z.shape[1]:
        A = np.column_stack([np.ones(n), Z])
        x = x - A @ np.linalg.lstsq(A, x, rcond=None)[0]
        y = y - A @ np.linalg.lstsq(A, y, rcond=None)[0]
        k = Z.shape[1]
    else:
        k = 0
    sx, sy = x.std(), y.std()
    if not sx or not sy:
        return 0.0, 1.0
    r = float(np.clip(np.corrcoef(x, y)[0, 1], -0.999999, 0.999999))
    df = n - 2 - k
    t = r * math.sqrt(df / (1 - r * r))
    return r, float(2 * stats.t.sf(abs(t), df))


def pcmci(X: np.ndarray, names: list[str], tau_max: int = 2, alpha_pc: float = 0.2, alpha: float = 0.01,
          max_conds: int = 3) -> dict:
    X = X[complete_rows(X)]
    T, V = X.shape
    L = 2 * tau_max  # room for parents of a lagged variable

    def col(i: int, tau: int) -> np.ndarray:
        return X[L - tau: T - tau, i]

    def mat(cols: list[tuple[int, int]]) -> np.ndarray | None:
        return np.column_stack([col(i, t) for i, t in cols]) if cols else None

    # PC1: lagged parent candidates per target.
    parents: dict[int, list[tuple[int, int]]] = {}
    for j in range(V):
        y = col(j, 0)
        cand = [(i, tau) for i in range(V) for tau in range(1, tau_max + 1)]
        strength = {c: math.inf for c in cand}
        p = 0
        while cand and p <= max_conds and len(cand) - 1 >= p:
            keep = []
            for c in cand:
                others = [o for o in sorted(cand, key=lambda o: -strength[o]) if o != c][:p]
                r, pv = parcorr(col(*c), y, mat(others))
                strength[c] = min(strength[c], abs(r))
                if pv <= alpha_pc:
                    keep.append(c)
            cand = sorted(keep, key=lambda o: -strength[o])
            p += 1
        parents[j] = cand

    # MCI on every lagged pair.
    lagged = []
    for j in range(V):
        for i in range(V):
            for tau in range(1, tau_max + 1):
                zx = [(k, t + tau) for k, t in parents[i] if t + tau <= L]
                z = [c for c in parents[j] if c != (i, tau)] + zx
                r, pv = parcorr(col(i, tau), col(j, 0), mat(z))
                if pv <= alpha:
                    lagged.append({"from": names[i], "to": names[j], "lag": tau, "r": r, "p": pv})

    # Contemporaneous skeleton, PC-style, every test conditioned on lagged parents.
    adj = {(a, b) for a in range(V) for b in range(V) if a < b}
    best: dict[tuple[int, int], tuple[float, float]] = {}
    for p in range(0, max_conds):
        for a, b in sorted(adj):
            if (a, b) not in adj:
                continue
            nbrs = sorted({c for e in adj for c in e if a in e or b in e} - {a, b})
            removed = False
            for S in itertools.combinations(nbrs, p):
                z = list(set(parents[a]) | set(parents[b])) + [(s, 0) for s in S]
                r, pv = parcorr(col(a, 0), col(b, 0), mat(z))
                prev = best.get((a, b))
                if prev is None or abs(r) < abs(prev[0]):
                    best[(a, b)] = (r, pv)
                if pv > alpha:
                    adj.discard((a, b))
                    removed = True
                    break
            if removed:
                continue
    contemporaneous = [{"a": names[a], "b": names[b], "r": best[(a, b)][0], "p": best[(a, b)][1]}
                       for a, b in sorted(adj)]
    return {"n": T - L, "tau_max": tau_max, "alpha": alpha, "contemporaneous": contemporaneous, "lagged": lagged}
