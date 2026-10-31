"""FxService.ensure_history: a fresh install holds only recent rates, so USD
amounts before them need a backfill back to the first activity."""

from datetime import date

from sqlalchemy import select

from app.fx import FxService
from app.models import FxRate


class FakeHttp:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def get(self, url, params):
        self.calls.append(params)
        if self.fail:
            raise OSError("network down")
        return FakeResp([{"d": "2026-06-01", "FXUSDCAD": {"v": "1.3837"}},
                         {"d": "2026-06-02", "FXUSDCAD": {"v": "1.3834"}}])


class FakeResp:
    def __init__(self, obs):
        self._obs = obs

    def raise_for_status(self):
        pass

    def json(self):
        return {"observations": self._obs}


async def _svc(sessions, http):
    async with sessions() as s:
        s.add(FxRate(date=date(2026, 10, 19), usdcad_micro=1_409_700))  # what a fresh install has
        await s.commit()
    fx = FxService(sessions)
    await fx.aclose()
    fx._http = http
    return fx


async def test_backfills_once_when_rates_start_after_first_activity(sessions):
    http = FakeHttp()
    fx = await _svc(sessions, http)
    await fx.ensure_history(date(2026, 6, 1))
    assert http.calls == [{"start_date": "2026-06-01"}]
    async with sessions() as s:
        assert (await s.execute(select(FxRate.date).order_by(FxRate.date))).scalars().first() == date(2026, 6, 1)
    await fx.ensure_history(date(2026, 6, 1))
    await fx.ensure_history(date(2026, 7, 1))
    assert len(http.calls) == 1  # covered: no refetch


async def test_no_fetch_when_rates_already_cover_start(sessions):
    http = FakeHttp()
    fx = await _svc(sessions, http)
    await fx.ensure_history(date(2026, 10, 20))
    await fx.ensure_history(None)  # no activities yet
    assert http.calls == []


async def test_network_failure_is_logged_and_retried_later(sessions):
    http = FakeHttp(fail=True)
    fx = await _svc(sessions, http)
    await fx.ensure_history(date(2026, 6, 1))  # must not raise
    await fx.ensure_history(date(2026, 6, 1))
    assert len(http.calls) == 2  # not marked covered after a failure
