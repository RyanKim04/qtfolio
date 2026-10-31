"""YfService caches: splits expire (a split while the app runs must reach the
walk) and survive a failed refetch; a recent closes fetch only short-circuits a
request it actually covers."""

from datetime import date, timedelta

import pandas as pd
import pytest
import yfinance

from app import yf as yfmod
from app.yf import YfService


class FakeTicker:
    splits = pd.Series(dtype=float)
    history_starts: list[str] = []
    fail = False

    def __init__(self, symbol):
        if FakeTicker.fail:
            raise ConnectionError("offline")

    def history(self, start, **kw):
        FakeTicker.history_starts.append(start)
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


@pytest.fixture
def fake(monkeypatch):
    FakeTicker.splits = pd.Series(dtype=float)
    FakeTicker.history_starts = []
    FakeTicker.fail = False
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    return FakeTicker


def _clock(monkeypatch, ts):
    monkeypatch.setattr(yfmod, "utcnow", lambda: pd.Timestamp(ts, unit="s").to_pydatetime())


async def test_splits_expire_and_survive_failures(fake, monkeypatch):
    y = YfService(None)
    _clock(monkeypatch, 1_000_000)
    assert await y.splits("AAA") == {}

    fake.splits = pd.Series([2.0], index=[pd.Timestamp("2026-10-29")])
    assert await y.splits("AAA") == {}  # still cached
    _clock(monkeypatch, 1_000_000 + yfmod.SPLITS_TTL + 1)
    assert await y.splits("AAA") == {date(2026, 10, 29): 2.0}  # expired → the new split is seen

    fake.fail = True
    _clock(monkeypatch, 1_000_000 + 2 * yfmod.SPLITS_TTL + 2)
    assert await y.splits("AAA") == {date(2026, 10, 29): 2.0}  # failed refetch keeps the last known


async def test_daily_closes_refetch_when_start_is_earlier(fake, monkeypatch):
    y = YfService(None)
    today = date.today()
    await y.refresh_daily_closes("SPY", today - timedelta(days=10))  # the EOD job's short window
    await y.refresh_daily_closes("SPY", today - timedelta(days=5))  # covered → no fetch
    await y.refresh_daily_closes("SPY", today - timedelta(days=400))  # a 1Y chart → must fetch
    assert fake.history_starts == [(today - timedelta(days=10)).isoformat(), (today - timedelta(days=400)).isoformat()]


async def test_ensure_daily_closes_refetches_only_on_adjustment_drift(fake, monkeypatch, sessions):
    """Incremental refresh of a long cached history; a dividend re-adjusting past
    closes (overlap drift) forces a full refetch so two bases never mix."""
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(40)]
    base = {d: 100.0 + i for i, d in enumerate(days)}
    FakeTicker.closes = base

    def history(self, start, **kw):
        FakeTicker.history_starts.append(start)
        s = date.fromisoformat(start)
        rows = {d: c for d, c in FakeTicker.closes.items() if d >= s}
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in rows])
        return pd.DataFrame({"Open": list(rows.values()), "High": list(rows.values()), "Low": list(rows.values()),
                             "Close": list(rows.values()), "Volume": [0] * len(rows)}, index=idx)

    monkeypatch.setattr(FakeTicker, "history", history)
    y = YfService(sessions)
    clock = [1_000_000]
    monkeypatch.setattr(yfmod, "utcnow", lambda: pd.Timestamp(clock[0], unit="s").to_pydatetime())
    got = await y.ensure_daily_closes("SPY", days[0])
    assert got == base and fake.history_starts == [days[0].isoformat()]

    clock[0] += 4000  # past the hourly throttle, nothing re-adjusted: a short recent fetch only
    await y.ensure_daily_closes("SPY", days[0])
    assert fake.history_starts[-1] == (days[-1] - timedelta(days=14)).isoformat()

    clock[0] += 4000  # a dividend rescales every earlier close → full refetch from the oldest cached day
    FakeTicker.closes = {d: c * 0.99 for d, c in base.items()}
    got = await y.ensure_daily_closes("SPY", days[0])
    assert fake.history_starts[-1] == days[0].isoformat()
    assert got[days[0]] == pytest.approx(99.0)
