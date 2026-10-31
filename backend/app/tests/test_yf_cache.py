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
