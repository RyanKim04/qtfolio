"""Indicators verified against a reference implementation (pandas, which
ships with yfinance) on a fixture series — M3 acceptance requirement.

Seeding conventions differ (we SMA-seed EMAs per TA-Lib; pandas ewm seeds on
the first value), so EMA-family comparisons check the tail after warmup, where
both converge.
"""

import math
import random
from datetime import date, datetime, timedelta

import pytest

from app.services import indicators as ind

random.seed(42)
N = 300
CLOSES = [100.0]
for _ in range(N - 1):
    CLOSES.append(max(1.0, CLOSES[-1] * (1 + random.gauss(0.0005, 0.02))))
HIGHS = [c * (1 + abs(random.gauss(0, 0.008))) for c in CLOSES]
LOWS = [c * (1 - abs(random.gauss(0, 0.008))) for c in CLOSES]

pd = pytest.importorskip("pandas")
S = pd.Series(CLOSES)


def tail_close(a, b, n=30, tol=1e-6):
    for x, y in zip(a[-n:], b[-n:]):
        assert x is not None and math.isfinite(y)
        assert abs(x - y) < tol * max(1, abs(y)), f"{x} vs {y}"


def test_sma_matches_pandas():
    ref = S.rolling(20).mean()
    ours = ind.sma(CLOSES, 20)
    assert ours[18] is None
    tail_close(ours, ref.tolist(), n=100)


def test_ema_converges_to_pandas():
    ref = S.ewm(span=20, adjust=False).mean()
    tail_close(ind.ema(CLOSES, 20), ref.tolist(), n=30, tol=1e-4)


def test_rsi_wilder_matches_reference():
    delta = S.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    ref = 100 - 100 / (1 + gain / loss)
    tail_close(ind.rsi(CLOSES, 14), ref.tolist(), n=30, tol=1e-3)
    flat_up = ind.rsi([float(i) for i in range(1, 30)], 14)
    assert flat_up[-1] == 100.0  # monotonic gains → RSI 100


def test_macd_converges_to_pandas():
    ref_line = S.ewm(span=12, adjust=False).mean() - S.ewm(span=26, adjust=False).mean()
    ref_signal = ref_line.ewm(span=9, adjust=False).mean()
    ours = ind.macd(CLOSES)
    tail_close(ours["macd"], ref_line.tolist(), n=30, tol=1e-3)
    tail_close(ours["signal"], ref_signal.tolist(), n=30, tol=1e-2)
    i = len(CLOSES) - 1
    assert abs(ours["hist"][i] - (ours["macd"][i] - ours["signal"][i])) < 1e-12


def test_bollinger_matches_pandas_population_std():
    mid = S.rolling(20).mean()
    std = S.rolling(20).std(ddof=0)
    ours = ind.bollinger(CLOSES)
    tail_close(ours["upper"], (mid + 2 * std).tolist(), n=50)
    tail_close(ours["lower"], (mid - 2 * std).tolist(), n=50)


def test_atr_wilder_matches_reference():
    h, l, c = pd.Series(HIGHS), pd.Series(LOWS), S
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    ref = tr.ewm(alpha=1 / 14, adjust=False).mean()
    tail_close(ind.atr(HIGHS, LOWS, CLOSES, 14), ref.tolist(), n=30, tol=1e-2)


def test_session_vwap_resets_per_day():
    bars = []
    t0 = datetime(2026, 1, 5, 14, 30)
    for day in range(2):
        for i in range(3):
            px = 100 + day * 10 + i
            bars.append({"ts": t0 + timedelta(days=day, minutes=5 * i), "h": px + 1, "l": px - 1, "c": px, "v": 100})
    out = ind.session_vwap(bars, lambda b: b["ts"].date())
    assert out[2] == pytest.approx((100 + 101 + 102) / 3)
    assert out[3] == pytest.approx(110.0)  # reset at the new session


def test_anchored_vwap_starts_at_anchor():
    bars = [{"ts": None, "h": p + 1, "l": p - 1, "c": p, "v": 10} for p in (10.0, 20.0, 30.0)]
    out = ind.anchored_vwap(bars, 1)
    assert out[0] is None
    assert out[1] == pytest.approx(20.0)
    assert out[2] == pytest.approx(25.0)


def test_rolling_extremes_and_crosses():
    ext = ind.rolling_extremes(HIGHS, LOWS, 252)
    assert ext["high"][-1] == max(HIGHS[-252:])
    assert ext["low"][-1] == min(LOWS[-252:])
    fast = [None, 1.0, 2.0, 3.0, 2.0, 1.0]
    slow = [None, 2.0, 2.0, 2.0, 2.0, 2.0]
    crosses = ind.ema_crosses(fast, slow)
    assert [c["kind"] for c in crosses] == ["golden", "death"]


def test_weekly_aggregation():
    bars = [
        {"date": date(2026, 1, 5), "ts": None, "o": 1, "h": 3, "l": 0.5, "c": 2, "v": 10},   # Mon
        {"date": date(2026, 1, 7), "ts": None, "o": 2, "h": 5, "l": 1.5, "c": 4, "v": 20},   # Wed
        {"date": date(2026, 1, 12), "ts": None, "o": 4, "h": 6, "l": 3.0, "c": 5, "v": 30},  # next Mon
    ]
    weeks = ind.aggregate_weekly(bars)
    assert len(weeks) == 2
    assert weeks[0]["o"] == 1 and weeks[0]["h"] == 5 and weeks[0]["l"] == 0.5
    assert weeks[0]["c"] == 4 and weeks[0]["v"] == 30
    assert weeks[1]["c"] == 5
