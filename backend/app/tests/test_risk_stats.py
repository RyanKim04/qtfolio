"""Risk statistics on synthetic returns: performance metrics against
direct numpy/scipy formulas, covariance estimators and the Euler/ENB
decompositions, VaR methods and the Kupiec/Christoffersen backtests."""

import math
from datetime import date, timedelta

import numpy as np
import pytest
from scipy import stats

from app.services.risk import covariance as cov
from app.services.risk import metrics
from app.services.risk import var as var_mod
from app.services.risk.data import Holding, align, build_panel, fill_missing


def _walk(n=600, seed=1, mu=0.0005, sd=0.012):
    rng = np.random.default_rng(seed)
    r = rng.normal(mu, sd, n)
    idx = 100 * np.concatenate([[1.0], np.cumprod(1 + r)])
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n + 1)]
    return dates, idx, r


def test_performance_matches_direct_formulas():
    dates, idx, r = _walk()
    rf = np.full(len(r), 0.03 / 252)
    bench = r * 0.8 + np.random.default_rng(2).normal(0, 0.004, len(r))
    out = metrics.performance(dates, idx, rf, bench)
    ex = r - rf
    assert out["sharpe"] == pytest.approx(ex.mean() / ex.std(ddof=1) * math.sqrt(252))
    assert out["vol"] == pytest.approx(r.std(ddof=1) * math.sqrt(252))
    assert out["total_return"] == pytest.approx(idx[-1] / idx[0] - 1)
    dd = idx / np.maximum.accumulate(idx) - 1
    assert out["max_drawdown"] == pytest.approx(dd.min())
    b = out["benchmark"]
    assert b["beta"] == pytest.approx(np.cov(r, bench)[0, 1] / np.var(bench, ddof=1))
    assert b["tracking_error"] == pytest.approx((r - bench).std(ddof=1) * math.sqrt(252))


def test_psr_and_min_trl_are_consistent():
    # Under normal returns PSR reduces to Φ(SR√(n−1)/√(1+SR²/2)).
    sr, n = 0.08, 500
    assert metrics.psr(sr, n, 0.0, 3.0) == pytest.approx(stats.norm.cdf(sr * math.sqrt(n - 1) / math.sqrt(1 + sr**2 / 2)))
    # A track record of exactly MinTRL observations gives PSR = the confidence level.
    trl = metrics.min_trl(sr, -0.5, 6.0)
    assert metrics.psr(sr, trl, -0.5, 6.0) == pytest.approx(0.95, abs=1e-9)
    # Negative skew and fat tails demand a longer record.
    assert trl > metrics.min_trl(sr, 0.0, 3.0)
    assert metrics.min_trl(-0.1, 0, 3) is None


def test_monthly_returns_chain_to_the_total():
    dates, idx, _ = _walk(400)
    months = metrics.monthly_returns(dates, idx)
    assert np.prod([1 + m["r"] for m in months]) - 1 == pytest.approx(idx[-1] / idx[0] - 1)


def _corr_returns(n=800, seed=3):
    rng = np.random.default_rng(seed)
    f = rng.normal(0, 0.01, (n, 2))
    X = np.column_stack([f[:, 0] + rng.normal(0, 0.005, n), f[:, 0] * 2 + rng.normal(0, 0.01, n),
                         f[:, 1] + rng.normal(0, 0.006, n), rng.normal(0, 0.02, n)])
    return X, f


def test_estimators_and_euler():
    X, f = _corr_returns()
    S = cov.sample_cov(X)
    assert np.allclose(S, np.cov(X, rowvar=False))
    # EWMA with λ→1 is the (population) sample covariance.
    assert np.allclose(cov.ewma_cov(X, lam=1 - 1e-12), np.cov(X, rowvar=False, ddof=0), rtol=1e-6)
    lw = cov.ledoit_wolf(X)
    assert np.allclose(np.diag(lw), np.diag(S))  # variances kept, correlations shrunk
    w = np.array([0.4, 0.3, 0.2, 0.1])
    e = cov.euler(w, S)
    assert e["ctr"].sum() == pytest.approx(e["vol"])
    assert e["pct"].sum() == pytest.approx(1.0)
    fs = cov.factor_cov(X, f)
    assert np.allclose(np.diag(fs), np.diag(S), rtol=0.05)


def test_enb_and_minimum_torsion():
    X, _ = _corr_returns()
    S = cov.sample_cov(X)
    t = cov.minimum_torsion(S)
    M = t @ S @ t.T
    assert np.allclose(M - np.diag(np.diag(M)), 0, atol=1e-12)  # bets are uncorrelated
    w = np.full(4, 0.25)
    enb, shares = cov.enb_torsion(w, S)
    assert shares.sum() == pytest.approx(1.0)
    assert 1 <= enb <= 4
    # Uncorrelated equal-risk assets: torsion is the identity, ENB = N.
    D = np.diag([1.0, 1.0, 1.0])
    assert np.allclose(cov.minimum_torsion(D), np.eye(3))
    assert cov.enb_torsion(np.full(3, 1 / 3), D)[0] == pytest.approx(3.0)
    assert cov.enb_pca(np.full(3, 1 / 3), D) == pytest.approx(3.0)
    assert cov.herfindahl_n(np.array([0.5, 0.5])) == pytest.approx(2.0)


def test_cluster_order_groups_correlated_assets():
    C = np.array([[1, 0.9, 0.1, 0.1], [0.9, 1, 0.1, 0.1], [0.1, 0.1, 1, 0.8], [0.1, 0.1, 0.8, 1]])
    order = cov.cluster_order(np.array(C, dtype=float))
    pos = {a: i for i, a in enumerate(order)}
    assert abs(pos[0] - pos[1]) == 1 and abs(pos[2] - pos[3]) == 1


def test_var_methods_on_normal_returns():
    rng = np.random.default_rng(4)
    r = rng.normal(0, 0.01, 20000)
    tab = var_mod.var_table(r, 0.01, 0.99)
    z = stats.norm.ppf(0.99)
    assert tab["normal"]["var"] == pytest.approx(z * 0.01)
    assert tab["normal"]["es"] == pytest.approx(0.01 * stats.norm.pdf(z) / 0.01)
    for m in ("cornish_fisher", "historical", "student_t"):
        assert tab[m]["var"] == pytest.approx(z * 0.01, rel=0.08), m
    # √h scaling
    assert var_mod.var_table(r, 0.01, 0.99, 10)["normal"]["var"] == pytest.approx(z * 0.01 * math.sqrt(10))


def test_component_var_and_es_sum_to_totals():
    X, _ = _corr_returns()
    w = np.array([0.4, 0.3, 0.2, 0.1])
    S = cov.sample_cov(X)
    comp = var_mod.component_var(w, S, 0.95)
    assert comp.sum() == pytest.approx(-stats.norm.ppf(0.05) * math.sqrt(w @ S @ w))
    ces = var_mod.component_es(X, w, 0.95)
    assert ces.sum() == pytest.approx(var_mod.hist_var_es(X @ w, 0.95)[1])


def test_kupiec_and_christoffersen():
    # Exactly the expected rate → LR 0, p 1.
    k = var_mod.kupiec(10, 1000, 0.01)
    assert k["lr"] == pytest.approx(0.0, abs=1e-9) and k["p_value"] == pytest.approx(1.0)
    # Hand-computed: 25 exceptions in 1000 at p = 1%.
    pi = 0.025
    lr = -2 * (975 * math.log(0.99) + 25 * math.log(0.01) - 975 * math.log(1 - pi) - 25 * math.log(pi))
    assert var_mod.kupiec(25, 1000, 0.01)["lr"] == pytest.approx(lr)
    clustered = np.zeros(500, dtype=bool)
    clustered[100:110] = True
    spread = np.zeros(500, dtype=bool)
    spread[::50] = True
    assert var_mod.christoffersen(clustered)["p_value"] < 0.01
    assert var_mod.christoffersen(spread)["p_value"] > 0.05


def test_panel_converts_usd_holdings_to_cad_and_fills_short_history():
    cal = [date(2025, 1, 1) + timedelta(days=i) for i in range(300)]
    rng = np.random.default_rng(6)
    spy = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
    fx = 1.35 * np.cumprod(1 + rng.normal(0, 0.003, 300))
    young = 50 * np.cumprod(1 + rng.normal(0, 0.02, 300))
    closes = {"SPY": dict(zip(cal, spy)), "CAD=X": dict(zip(cal, fx)),
              "YNG": dict(zip(cal[200:], young[200:]))}
    p = build_panel(closes, [Holding("SPY", "SPY", "USD", 0.5, 50), Holding("YNG", "YNG", "CAD", 0.3, 30)], 0.1, 100)
    assert p.assets == ["SPY", "YNG", "USD cash"]
    r_spy_cad = (spy[1:] / spy[:-1]) * (fx[1:] / fx[:-1]) - 1
    assert np.allclose(p.R[:, 0], r_spy_cad)
    assert np.isnan(p.R[:150, 1]).all() and np.isfinite(p.R[201:, 1]).all()
    assert np.allclose(p.R[:, 2], fx[1:] / fx[:-1] - 1)
    # Missing days fill only where every factor exists; here most factors are absent.
    filled, share = fill_missing(p.R, p.F[:, :1])
    assert np.isfinite(filled[:, 1]).all() and share[1] > 0.6


def test_align_forward_fills_short_gaps_only():
    cal = [date(2025, 1, d) for d in range(1, 31)]
    closes = {date(2025, 1, 2): 10.0, date(2025, 1, 20): 12.0}
    a = align(closes, cal)
    assert np.isnan(a[0]) and a[1] == 10 and a[8] == 10  # 7 days of carry
    assert np.isnan(a[9])  # silent too long
    assert a[19] == 12
