"""Causal and stress machinery on simulated data with known structure: the SEM
recovers its coefficients, do() differs from conditioning when a confounder is
present, channels sum to the total effect, local projections recover a known
impulse response, PCMCI-lite finds planted links, and the stress tests and
trade counterfactuals do their accounting exactly."""

from datetime import date, timedelta

import numpy as np
import pytest

from app.services.risk import causal, counterfactual, stress
from app.services.risk.data import DRIVERS, Holding, build_panel

KEYS = [k for k, *_ in DRIVERS]


def _simulate(n=3000, seed=0):
    """Drivers follow the prior DAG with known coefficients; two holdings load
    on equity and (one of them) directly on rates."""
    rng = np.random.default_rng(seed)
    e = rng.normal(size=(n, 7))
    rates = 5 * e[:, 0]
    oil = 0.02 * e[:, 1]
    usd = 0.001 * rates + 0.004 * e[:, 2]
    credit = 0.0005 * rates - 0.05 * oil + 0.002 * e[:, 3]
    vix = 0.1 * rates - 300 * credit + 1.0 * e[:, 4]
    equity = -0.001 * rates - 0.004 * vix + 0.5 * credit + 0.006 * e[:, 5]
    usdcad = 0.3 * usd - 0.1 * oil - 0.2 * equity + 0.003 * e[:, 6]
    D = np.column_stack([rates, oil, usd, credit, vix, equity, usdcad])
    h1 = 1.5 * equity + 0.008 * rng.normal(size=n)
    h2 = 0.8 * equity + 0.002 * rates + 0.01 * rng.normal(size=n)
    return D, np.column_stack([h1, h2])


def test_scm_recovers_coefficients_and_channels_sum():
    D, R = _simulate()
    scm = causal.fit_scm(D, R, KEYS, ["h1", "h2"])
    i = {k: n for n, k in enumerate(KEYS)}
    assert scm.A[i["vix"], i["credit"]] == pytest.approx(-300, rel=0.05)
    assert scm.A[i["equity"], i["vix"]] == pytest.approx(-0.004, rel=0.05)
    assert scm.A[7, i["equity"]] == pytest.approx(1.5, rel=0.05)
    assert scm.A[8, i["rates"]] == pytest.approx(0.002, rel=0.1)
    w = np.array([0.6, 0.4])
    shock = {i["rates"]: 10.0}
    total = float(w @ causal.do(scm, shock)[7:])
    ch = causal.channels(scm, w, shock)
    assert sum(c["contribution"] for c in ch) == pytest.approx(total)
    assert any(c["path"] == ["rates", "credit", "vix", "equity"] for c in ch)


def test_do_differs_from_conditioning_under_confounding():
    D, R = _simulate()
    w = np.array([0.6, 0.4])
    i = KEYS.index("usd")
    # The dollar's only route to the book here is through USD/CAD, which the
    # holdings don't load on; conditioning on it also picks up rates, its cause.
    eff = causal.shock_effects(D, R, w, KEYS, ["h1", "h2"], {i: 0.01}, boot=20)
    cond = stress.conditional(D, R, w, {i: 0.01})
    assert abs(eff["portfolio"]) < abs(cond["portfolio"]) / 3
    lo, hi = eff["ci90"]
    assert lo <= eff["portfolio"] <= hi


def test_local_projection_recovers_known_irf():
    rng = np.random.default_rng(1)
    n = 4000
    s = rng.normal(size=n)
    y = np.zeros(n)
    # y_t = 0.5 s_t + 0.3 s_{t−1} + noise → cumulative IRF 0.5, 0.8, 0.8, …
    y[1:] = 0.5 * s[1:] + 0.3 * s[:-1] + 0.2 * rng.normal(size=n - 1)
    y[0] = 0.5 * s[0]
    controls = np.column_stack([np.concatenate([[0], y[:-1]])])
    lp = causal.local_projection(s, y, controls, horizon=4)
    assert lp["beta"][0] == pytest.approx(0.5, abs=0.03)
    assert lp["beta"][1] == pytest.approx(0.8, abs=0.05)
    assert lp["beta"][4] == pytest.approx(0.8, abs=0.08)
    assert all(lo <= b <= hi for b, lo, hi in zip(lp["beta"], lp["lo"], lp["hi"]))


def test_pcmci_finds_planted_links():
    rng = np.random.default_rng(2)
    n = 2000
    a = rng.normal(size=n)
    b = np.zeros(n)
    c = rng.normal(size=n)
    b[1:] = 0.6 * a[:-1] + rng.normal(size=n - 1)  # lagged a → b
    c = c + 0.7 * a  # contemporaneous a — c
    out = causal.pcmci(np.column_stack([a, b, c]), ["a", "b", "c"])
    assert any(x["from"] == "a" and x["to"] == "b" and x["lag"] == 1 for x in out["lagged"])
    pairs = {(x["a"], x["b"]) for x in out["contemporaneous"]}
    assert ("a", "c") in pairs and ("a", "b") not in pairs


def _panel(n=400, seed=4):
    rng = np.random.default_rng(seed)
    cal = [date(2008, 1, 2) + timedelta(days=i) for i in range(n)]
    syms = ["SPY", "QQQ", "SMH", "IWM", "IWD", "IWF", "MTUM", "USMV", "XIU.TO", "IEF", "HYG", "USO", "GLD", "CAD=X",
            "UUP", "^VIX", "^TNX", "AAA"]
    closes = {s: dict(zip(cal, 100 * np.cumprod(1 + rng.normal(0, 0.01, n)))) for s in syms}
    return build_panel(closes, [Holding("AAA", "AAA", "CAD", 0.8, 80)], 0.0, 100)


def test_historical_replay_compounds_the_window():
    p = _panel()
    s, e = p.dates[10], p.dates[30]
    # Replace the scenario list with one window over the synthetic dates.
    orig = stress.SCENARIOS
    stress.SCENARIOS = [("t", "test", s, e)]
    try:
        out = stress.historical(p, p)
    finally:
        stress.SCENARIOS = orig
    win = [i for i, d in enumerate(p.dates) if s < d <= e]
    expect = np.prod(1 + p.R[win, 0]) - 1
    assert out[0]["holdings"][0]["method"] == "replay"
    assert out[0]["portfolio"] == pytest.approx(0.8 * expect)


def test_reverse_stress_hits_the_target_loss():
    D, R = _simulate(1500)
    w = np.array([0.6, 0.4])
    out = stress.reverse(D, R, w, 0.1, 21)
    S = np.cov(R, rowvar=False) * 21
    assert float(w @ out["assets"]) == pytest.approx(-0.1)
    assert out["asset_distance"] == pytest.approx(0.1 / np.sqrt(w @ S @ w))


def test_trade_counterfactual_is_exact_accounting():
    cal = [date(2026, 1, 1) + timedelta(days=i) for i in range(200)]
    px = {d: 100.0 * (1.01 ** i) for i, d in enumerate(cal)}  # +1% a session
    fx = {d: 1.4 for d in cal}
    trades = [counterfactual.Trade(cal[50], "AAA", 10, 100.0, "USD")]
    rows = counterfactual.decisions(trades, cal, {"AAA": px}, fx, horizons=(20,))
    va = rows[0]["horizons"][20]["value_add"]
    assert va == pytest.approx(10 * 100 * 1.4 * (1.01**20 - 1))
    # A jump inside the trade's window: only placebo starts that also span it
    # (about 20 of ~180) do as well.
    jump = {d: v * (1.2 if i >= 60 else 1.0) for i, (d, v) in enumerate(px.items())}
    rows = counterfactual.decisions(trades, cal, {"AAA": jump}, fx, horizons=(20,))
    assert rows[0]["horizons"][20]["percentile"] > 0.8
    sell = counterfactual.decisions([counterfactual.Trade(cal[50], "AAA", -10, 100.0, "CAD")], cal, {"AAA": px}, fx,
                                    horizons=(20,))
    assert sell[0]["horizons"][20]["value_add"] == pytest.approx(-10 * 100 * (1.01**20 - 1))


def test_bsts_event_detects_a_planted_jump():
    rng = np.random.default_rng(9)
    n = 260
    X = rng.normal(0, 0.01, (n, 2))
    y = 0.8 * X[:, 0] + 0.3 * X[:, 1] + rng.normal(0, 0.002, n)
    y[200:] += 0.004  # +0.4% a day after the event
    out = counterfactual.bsts_event(y, X, 200, post=40)
    assert out["car"][-1] == pytest.approx(40 * 0.004, rel=0.35)
    assert out["lo"][-1] > 0 and out["p_value"] < 0.05
