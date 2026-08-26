"""Broker posting lag (§4.2): the newest snapshot can contradict recorded
activities (trades executed but not yet posted). recompute() must fall back to
the newest CONSISTENT anchor, extend the ledger forward, and keep capital-in on
pure flows — instead of fabricating negative quantities through all of history."""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.models import Account, BalanceSnapshot, Candle, PositionSnapshot, SeriesDaily
from app.services import history
from app.services.reconstruction import ReconResult, extend_forward
from app.services.activities import assign_hashes, upsert_activities

M = 1_000_000
TODAY = date.today()


class StubYf:
    async def splits(self, sym):
        return {}


def act(d, type_, action=None, ticker=None, qty=None, net=None):
    return {
        "account_id": "A", "trade_date": d, "transaction_date": d, "settlement_date": d,
        "type": type_, "action": action or "", "ticker": ticker,
        "qty_micro": round(qty * M) if qty is not None else None, "price_micro": None,
        "gross_amount_cents": None, "commission_cents": None,
        "net_amount_cents": round(net * 100) if net is not None else 0,
        "currency": "CAD", "description": None, "raw_json": "{}",
    }


async def seed(sessions):
    d_dep, d_buy = TODAY - timedelta(days=20), TODAY - timedelta(days=19)
    clean_day = TODAY - timedelta(days=5)
    async with sessions() as s:
        s.add(Account(qt_account_id="A", tracked=True))
        # snapshots: a clean one 5 days ago (10 XYZ), and today's showing 6 XYZ
        # (4 shares sold this week — the sell has NOT posted to activities)
        for d, qty, cash in ((clean_day, 10, 9_000_00), (TODAY, 6, 9_400_00)):
            s.add(PositionSnapshot(date=d, account_id="A", ticker="XYZ", qty_micro=qty * M,
                                   price_micro=100 * M, market_value_cents=qty * 100_00, currency="CAD"))
            s.add(BalanceSnapshot(date=d, account_id="A", currency="CAD", cash_cents=cash,
                                  market_value_cents=qty * 100_00, total_equity_cents=cash + qty * 100_00))
        for back in range(25, -1, -1):
            d = TODAY - timedelta(days=back)
            s.add(Candle(ticker="XYZ", interval="OneDay", ts=datetime.combine(d, datetime.min.time()),
                         o=100, h=100, l=100, c=100.0, v=1000))
        await s.commit()
    rows = assign_hashes([
        act(d_dep, "deposits", action="con", net=10_000),
        act(d_buy, "trades", action="buy", ticker="XYZ", qty=10, net=-1_000),
    ])
    await upsert_activities(sessions, rows)


async def test_recompute_falls_back_to_consistent_anchor(sessions):
    await seed(sessions)
    result = await history.recompute(sessions, StubYf())
    assert result["ok"] and result["end"] == TODAY.isoformat()

    warnings = await history.get_setting(sessions, "reconciliation_warnings", [])
    assert not any("negative quantity" in w["message"] for w in warnings)
    assert any("not yet posted" in w["message"] for w in warnings)

    async with sessions() as s:
        rows = list((await s.execute(select(SeriesDaily).order_by(SeriesDaily.date))).scalars())
    assert rows[-1].date == TODAY
    # capital-in stays pure flows ($10k deposited) — not polluted by the phantom gap
    assert rows[-1].capital_in_cad_cents == 10_000_00
    # totals pinned to today's real snapshot despite approximated tail holdings
    assert rows[-1].portfolio_value_cad_cents == 9_400_00 + 6 * 100_00


async def test_recompute_uses_newest_anchor_when_consistent(sessions):
    """Once the missing rows post, the newest snapshot walks clean and is used."""
    await seed(sessions)
    sell_day = TODAY - timedelta(days=2)
    await upsert_activities(sessions, assign_hashes([
        act(sell_day, "trades", action="sell", ticker="XYZ", qty=-4, net=400),
    ]))
    result = await history.recompute(sessions, StubYf())
    assert result["ok"] and result["end"] == TODAY.isoformat()
    warnings = await history.get_setting(sessions, "reconciliation_warnings", [])
    assert not any("not yet posted" in w["message"] for w in warnings)
    assert not any("negative quantity" in w["message"] for w in warnings)


def test_extend_forward_applies_recorded_rows():
    start, end = date(2026, 8, 7), date(2026, 8, 10)
    res = ReconResult()
    res.daily_qty[start] = {"XYZ": 10 * M}
    res.daily_cash[start] = {"CAD": 1_000_00}
    rows = [
        act(date(2026, 8, 9), "trades", action="sell", ticker="XYZ", qty=-4, net=400),
        act(date(2026, 8, 10), "deposits", action="con", net=500),
    ]
    extend_forward(res, rows, start, end)
    assert res.daily_qty[date(2026, 8, 8)] == {"XYZ": 10 * M}
    assert res.daily_qty[date(2026, 8, 9)] == {"XYZ": 6 * M}
    assert res.daily_cash[date(2026, 8, 9)]["CAD"] == 1_400_00
    assert res.daily_cash[end]["CAD"] == 1_900_00
    assert res.flows[end]["CAD"] == 500_00  # forward flows still count as capital
