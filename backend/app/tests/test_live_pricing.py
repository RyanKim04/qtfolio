"""Extended-hours live pricing: quotes prefer the newest post/pre-market trade
after the regular close, and the Performance series' today point is the live
dashboard total. It overrides a recomputed today row, or is appended when the
series stops short of today (pre-market, weekends)."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api import performance
from app.config import TZ
from app.yf import YfService, quotes_live

TODAY = date.today()
CLOSE_T = 1_000_000  # epoch seconds of the regular close; only ordering matters


class FakeTicker:
    infos: dict[str, dict] = {}

    def __init__(self, sym):
        self.info = self.infos[sym]


@pytest.fixture
def fake_yf(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    return FakeTicker.infos


def info(post=None, post_t=0, pre=None, pre_t=0):
    return {"regularMarketPrice": 100.0, "regularMarketTime": CLOSE_T, "regularMarketPreviousClose": 90.0,
            "postMarketPrice": post, "postMarketTime": post_t, "preMarketPrice": pre, "preMarketTime": pre_t}


async def test_quotes_session_selection(fake_yf):
    fake_yf.update({
        "REG": info(post=95.0, post_t=CLOSE_T - 3600),  # yesterday's post-market, older than the close
        "POST": info(post=80.0, post_t=CLOSE_T + 3600),
        "PRE": info(post=80.0, post_t=CLOSE_T + 3600, pre=110.0, pre_t=CLOSE_T + 50_000),
    })
    q = await YfService(None).quotes(["REG", "POST", "PRE"])
    assert q["REG"] == {"last": 100.0, "prev_close": 90.0, "session": None, "time": CLOSE_T}
    # post-market extends today's move: base stays the previous close
    assert q["POST"] == {"last": 80.0, "prev_close": 90.0, "session": "post", "time": CLOSE_T + 3600}
    # pre-market starts the next day: base is the last regular close
    assert q["PRE"] == {"last": 110.0, "prev_close": 100.0, "session": "pre", "time": CLOSE_T + 50_000}


def test_quotes_live_window():
    et = lambda *a: datetime(*a, tzinfo=TZ)  # noqa: E731
    assert not quotes_live(et(2026, 10, 29, 3, 59))  # before pre-market
    assert quotes_live(et(2026, 10, 29, 4, 0))
    assert quotes_live(et(2026, 10, 29, 19, 59))  # after-hours
    assert not quotes_live(et(2026, 10, 29, 20, 0))  # extended trading over
    assert not quotes_live(et(2026, 10, 31, 12, 0))  # Saturday


def row(d, value, idx, flows=0, capital=1_000_000, fx=500):
    return SimpleNamespace(date=d, portfolio_value_cad_cents=value, twr_index=idx, flows_cad_cents=flows,
                           capital_in_cad_cents=capital, fx_gain_cad_cents=fx)


class FakePortfolio:
    def __init__(self, total, fail=False):
        self.total, self.fail = total, fail

    async def positions(self):
        if self.fail:
            raise RuntimeError("yahoo down")
        return {"rows": [{"ticker": "AAA", "last": 12.5}], "cash": {}}

    async def total_equity_cad(self):
        return self.total


def patch_series(monkeypatch, rows):
    async def load(state):
        return rows

    monkeypatch.setattr(performance, "_load_series", load)


async def test_live_point_overrides_today(monkeypatch):
    patch_series(monkeypatch, [row(TODAY - timedelta(days=1), 1_000_000, 100.0),
                               row(TODAY, 1_100_000, 115.0, flows=50_000)])
    m = await performance._series_maps(SimpleNamespace(portfolio=FakePortfolio(10_500.0)))
    assert m["dates"][-1] == TODAY and len(m["dates"]) == 2
    assert m["values"][TODAY] == 1_050_000
    # Start-of-day flow: r = (1.05M − 1M − 50k) / (1M + 50k) = 0
    assert m["idx"][TODAY] == pytest.approx(100.0)
    assert m["live_price"] == {"AAA": 12.5}


async def test_live_point_appended_when_series_is_behind(monkeypatch):
    last = TODAY - timedelta(days=3)
    patch_series(monkeypatch, [row(last - timedelta(days=1), 900_000, 90.0),
                               row(last, 1_000_000, 100.0, capital=1_200_000, fx=-300)])
    m = await performance._series_maps(SimpleNamespace(portfolio=FakePortfolio(9_000.0)))
    assert m["dates"][-2:] == [last, TODAY] and m["ledger_end"] == last
    assert m["values"][TODAY] == 900_000 and m["flows"][TODAY] == 0
    assert m["capital"][TODAY] == 1_200_000 and m["fx_cum"][TODAY] == -300
    assert m["idx"][TODAY] == pytest.approx(90.0)


async def test_live_failure_keeps_stored_series(monkeypatch):
    patch_series(monkeypatch, [row(TODAY - timedelta(days=2), 1_000_000, 100.0),
                               row(TODAY - timedelta(days=1), 1_100_000, 110.0)])
    m = await performance._series_maps(SimpleNamespace(portfolio=FakePortfolio(1.0, fail=True)))
    assert m["dates"][-1] == TODAY - timedelta(days=1)
    assert m["live_price"] == {} and "yahoo down" in m["live_reason"]


def test_day_pnl_counts_session_buys_from_fill():
    from app.services.portfolio import fill_aware_day_pnl

    # 37 held at the previous close (130), 5 bought today at 96, last 91
    assert fill_aware_day_pnl(91.0, 130.0, 42.0, [(5.0, 96.0)]) == pytest.approx(37 * -39 + 5 * -5)
    # no session trades: plain (last − prev) × qty
    assert fill_aware_day_pnl(91.0, 130.0, 42.0, []) == pytest.approx(42 * -39)
    # sold 2 of 10 today at 120: the sold shares' move is realized at the fill
    assert fill_aware_day_pnl(110.0, 100.0, 8.0, [(-2.0, 120.0)]) == pytest.approx(8 * 10 + 2 * 20)


async def test_stream_tick_overrides_stale_polled_quote(fake_yf):
    fake_yf.update({"S": info()})  # polled: regular 100 @ CLOSE_T, prev close 90
    yfs = YfService(None)
    await yfs.quotes(["S"])
    assert "S" in yfs._stream_syms  # asked-for symbols get streamed
    # regular-session tick newer than the polled quote wins, base = previous close
    yfs._on_tick({"id": "S", "price": 101.5, "time": str((CLOSE_T + 60) * 1000), "market_hours": 1, "change": 11.5})
    assert (await yfs.quotes(["S"]))["S"] == {"last": 101.5, "prev_close": 90.0, "session": None, "time": CLOSE_T + 60}
    # pre-market tick measures from the last regular close
    yfs._on_tick({"id": "S", "price": 104.0, "time": str((CLOSE_T + 50_000) * 1000), "market_hours": 0})
    assert (await yfs.quotes(["S"]))["S"] == {"last": 104.0, "prev_close": 100.0, "session": "pre", "time": CLOSE_T + 50_000}
    # an older tick never beats a fresher polled quote; unknown sessions are ignored
    yfs._on_tick({"id": "S", "price": 1.0, "time": str((CLOSE_T - 60) * 1000), "market_hours": 1})
    assert (await yfs.quotes(["S"]))["S"]["last"] == 100.0
    yfs._on_tick({"id": "S", "price": 1.0, "time": str((CLOSE_T + 99_000) * 1000), "market_hours": 3})
    assert (await yfs.quotes(["S"]))["S"]["last"] == 100.0
