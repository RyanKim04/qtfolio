"""Questrade → Wealthsimple move (2026-10-27): WS CSV parsing, broker-to-broker
transfer matching, ledger-account state, cross-broker average cost, and an
end-to-end recompute where a retired Questrade history hands off to a
Wealthsimple ledger account without a seam in value, capital-in or TWR."""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.models import Account, BalanceSnapshot, Candle, SeriesDaily
from app.services import history
from app.services.activities import assign_hashes, upsert_activities
from app.services.book import average_costs, current_book
from app.services.reconstruction import LedgerState, classify, match_transfers
from app.services.wealthsimple import app_ticker, is_ws_export, parse_ws_export

M = 1_000_000
TODAY = date.today()

WS_CSV = b"""effective_date,effective_time,settlement_date,account_id,account_type,activity_type,activity_sub_type,description,direction,symbol,name,currency,quantity,unit_price,commission,net_cash_amount
2026-10-07,12:04:23,,WS1,TFSA,MoneyMovement,EFT,Deposit,,,,CAD,1000,,,1000
2026-10-07,12:05:39,,WS1,TFSA,FxExchange,-,Convert USD,,,,USD,500.00,,,500.00
2026-10-07,12:05:39,,WS1,TFSA,FxExchange,-,Convert CAD,,,,CAD,-690.00,,,-690.00
2026-10-07,12:05:40,2026-10-08,WS1,TFSA,Trade,BUY,"META - Meta: Bought 1.0000 shares",LONG,META,Meta Platforms Inc.,USD,1,480.00,0,-480.00
2026-10-07,12:37:01,2026-10-08,WS1,TFSA,Trade,BUY,GOOG - Alphabet CDR (CAD Hedged) : Bought 10.0000 shares,LONG,GOOG,Alphabet CDR (CAD Hedged) ,CAD,10,50.00,0,-500.00
2026-10-19,01:00:00,,WS1,TFSA,SecurityTransfer,-,"RDDT - Reddit Inc.: Tax-free transfer of 20.0000 shares into the account",,RDDT,Reddit Inc.,USD,20,,,2000.00
2026-10-20,01:00:00,,WS1,TFSA,MoneyMovement,TRANSFER_TF,Money transfer into the account,,,,USD,10.00,,,10.00
2026-10-20,01:00:00,,WS1,TFSA,MoneyMovement,TRANSFER,Money transfer out of the account,,,,CAD,-50.00,,,-50.00
2026-10-21,00:00:00,,WS1,TFSA,AdministrativePayment,TRANSFER_FEE_REIMBURSEMENT,CAD credited,,,,CAD,100.00,,,100.00
2026-10-22,15:55:02,2026-10-23,WS1,TFSA,Trade,SELL,"META - Meta: Sold 1.0000 shares",LONG,META,Meta Platforms Inc.,USD,-1,490.00,0,490.00
2026-10-23,00:00:00,,WS1,TFSA,Tax,NRT,Non-resident tax,,,,USD,-0.15,,,-0.15
2026-10-23,00:00:00,,WS1,TFSA,Dividend,-,"ORCL - Oracle Corp.: Cash dividend",,ORCL,Oracle Corp.,USD,1.0,,,1.0
"""


def by_desc(rows, needle):
    return next(r for r in rows if needle in (r["description"] or ""))


def test_ws_export_detected_and_mapped():
    assert is_ws_export(WS_CSV)
    assert not is_ws_export(b"Transaction Date,Action,Symbol,Quantity\n")
    parsed = parse_ws_export(WS_CSV)
    rows = parsed["rows"]
    assert parsed["errors"] == [] and parsed["accounts"] == {"WS1": "TFSA"}
    assert len(rows) == 12

    dep = by_desc(rows, "Deposit")
    assert (dep["type"], dep["net_amount_cents"], classify(dep)) == ("deposits", 100_000, "deposit")

    goog = by_desc(rows, "Alphabet CDR")
    assert goog["ticker"] == "GOOG.TO"  # the CAD CDR never collides with US GOOG
    assert classify(goog) == "trade" and goog["qty_micro"] == 10 * M

    xfer = by_desc(rows, "Tax-free transfer")
    # book value in net_cash_amount is NOT cash
    assert (xfer["type"], xfer["qty_micro"], xfer["net_amount_cents"]) == ("transfers", 20 * M, 0)
    assert classify(xfer) == "journal"

    cash_in = by_desc(rows, "Money transfer into")
    assert cash_in["type"] == "transfers" and classify(cash_in) == "journal"
    assert classify(by_desc(rows, "CAD credited")) == "fee"
    assert classify(by_desc(rows, "Non-resident tax")) == "fee"
    div = by_desc(rows, "Cash dividend")
    # WS repeats the cash amount in `quantity` — must not become 1.0 phantom share
    assert classify(div) == "dividend" and div["qty_micro"] is None and div["net_amount_cents"] == 100


def test_app_ticker_convention():
    assert app_ticker("GOOG", "CAD") == "GOOG.TO"
    assert app_ticker("GOOG", "USD") == "GOOG"
    assert app_ticker("XEQT.TO", "CAD") == "XEQT.TO"
    assert app_ticker("", "CAD") is None


def leg(acc, d, type_, ticker=None, qty=None, net=0, currency="USD", id_=None):
    return {"id": id_, "account_id": acc, "transaction_date": d, "type": type_, "action": "",
            "ticker": ticker, "qty_micro": round(qty * M) if qty is not None else None,
            "net_amount_cents": round(net * 100), "currency": currency}


def test_transfer_legs_match_and_share_a_day():
    d = date(2026, 10, 19)
    acts = [
        leg("Q", d, "transfers", "SOXL", -35, id_=1),
        leg("W", d + timedelta(days=2), "transfers", "SOXL", 35, id_=2),  # in transit two days
        leg("Q", d + timedelta(days=1), "transfers", net=-26.21, id_=3),
        leg("W", d + timedelta(days=1), "transfers", net=26.21, id_=4),
        leg("W", d, "transfers", net=500, currency="CAD", id_=5),  # from an untracked bank
    ]
    rows, warnings = match_transfers(acts)
    by_id = {r["id"]: r for r in rows}
    assert by_id[1]["transaction_date"] == by_id[2]["transaction_date"] == d + timedelta(days=2)
    assert by_id[3]["type"] == by_id[4]["type"] == "transfers"
    assert by_id[5]["type"] == "deposits" and classify(by_id[5]) == "deposit"
    assert len(warnings) == 1 and "no counterpart" in warnings[0]["message"]
    assert acts[0]["transaction_date"] == d  # input untouched


def test_ledger_state_is_the_forward_sum():
    d0 = date(2026, 10, 7)
    acts = [
        leg("W", d0, "deposits", net=1000, currency="CAD", id_=1),
        {**leg("W", d0 + timedelta(days=1), "trades", "XYZ", 4, net=-400, currency="CAD", id_=2), "action": "buy"},
        {**leg("W", d0 + timedelta(days=3), "trades", "XYZ", -1, net=120, currency="CAD", id_=3), "action": "sell"},
    ]
    ledger = LedgerState(acts, d0 + timedelta(days=10))
    assert ledger.at(d0 - timedelta(days=1)) == ({}, {})
    assert ledger.at(d0) == ({}, {"CAD": 100_000})
    assert ledger.at(d0 + timedelta(days=2)) == ({"XYZ": 4 * M}, {"CAD": 60_000})
    assert ledger.at(d0 + timedelta(days=30)) == ({"XYZ": 3 * M}, {"CAD": 72_000})


def test_average_cost_carries_across_the_transfer():
    d = date(2026, 10, 1)
    acts = [
        {**leg("Q", d, "trades", "SOXL", 10, net=-1000, id_=1), "action": "buy"},
        {**leg("Q", d + timedelta(days=1), "trades", "SOXL", 10, net=-1200, id_=2), "action": "buy"},
        {**leg("Q", d + timedelta(days=2), "trades", "SOXL", -5, net=600, id_=3), "action": "sell"},
        leg("Q", d + timedelta(days=5), "transfers", "SOXL", -15, id_=4),
        leg("W", d + timedelta(days=5), "transfers", "SOXL", 15, id_=5),
        {**leg("W", d + timedelta(days=6), "trades", "SOXL", 5, net=-500, id_=6), "action": "buy"},
    ]
    rows, _ = match_transfers(acts)
    q, cost = average_costs(rows, {})["SOXL"]
    # 20 sh @ 110 → sell 5 releases 550 → 15 sh @ 1650; transfer keeps it; +5 @ 100
    assert q == 20 * M and cost == 1650_00 + 500_00


class StubYf:
    async def splits(self, sym):
        return {}


def row(acc, d, type_, action="", ticker=None, qty=None, net=0, currency="CAD"):
    return {
        "account_id": acc, "trade_date": d, "transaction_date": d, "settlement_date": d,
        "type": type_, "action": action, "ticker": ticker,
        "qty_micro": round(qty * M) if qty is not None else None, "price_micro": None,
        "gross_amount_cents": None, "commission_cents": None, "net_amount_cents": round(net * 100),
        "currency": currency, "description": None, "raw_json": "{}",
    }


async def test_recompute_hands_off_from_questrade_to_wealthsimple(sessions):
    d_dep, d_buy = TODAY - timedelta(days=20), TODAY - timedelta(days=19)
    d_snap, d_move, d_ws_dep = TODAY - timedelta(days=15), TODAY - timedelta(days=10), TODAY - timedelta(days=5)
    async with sessions() as s:
        s.add(Account(qt_account_id="Q", tracked=True, broker="questrade", status="Closed"))
        s.add(Account(qt_account_id="W", tracked=True, broker="wealthsimple", status="Active"))
        # a mid-history Questrade snapshot (10 XYZ @ 50 + 500 cash) and the
        # closing snapshot taken at retirement (empty)
        s.add(BalanceSnapshot(date=d_snap, account_id="Q", currency="CAD", cash_cents=500_00,
                              market_value_cents=500_00, total_equity_cents=1000_00))
        s.add(BalanceSnapshot(date=TODAY, account_id="Q", currency="CAD", cash_cents=0,
                              market_value_cents=0, total_equity_cents=0))
        for back in range(25, -1, -1):
            d = TODAY - timedelta(days=back)
            c = 50.0 if d < TODAY - timedelta(days=12) else 60.0
            s.add(Candle(ticker="XYZ", interval="OneDay", ts=datetime.combine(d, datetime.min.time()),
                         o=c, h=c, l=c, c=c, v=1000))
        await s.commit()
    from app.models import PositionSnapshot
    async with sessions() as s:
        s.add(PositionSnapshot(date=d_snap, account_id="Q", ticker="XYZ", qty_micro=10 * M,
                               price_micro=50 * M, market_value_cents=500_00, currency="CAD"))
        await s.commit()
    await upsert_activities(sessions, assign_hashes([
        row("Q", d_dep, "deposits", "dep", net=1000),
        row("Q", d_buy, "trades", "buy", "XYZ", 10, net=-500),
        row("Q", d_move, "transfers", "tfo", "XYZ", -10),
        row("Q", d_move, "transfers", "tfo", net=-500),
        row("W", d_move, "transfers", "tfi", "XYZ", 10),
        row("W", d_move, "transfers", "tfi", net=500),
        row("W", d_ws_dep, "deposits", "dep", net=200),
    ]))

    result = await history.recompute(sessions, StubYf())
    assert result["ok"]
    async with sessions() as s:
        series = {r.date: r for r in (await s.execute(select(SeriesDaily))).scalars()}
    last = series[max(series)]
    assert max(series) == TODAY
    assert last.portfolio_value_cad_cents == 10 * 60_00 + 700_00
    assert last.capital_in_cad_cents == 1200_00  # the transfer is not a deposit
    assert series[d_move].flows_cad_cents == 0
    # TWR only sees the 50 → 60 move: +100 on 1000
    assert abs(last.twr_index - 110.0) < 0.01
    warnings = await history.get_setting(sessions, "reconciliation_warnings", [])
    assert not [w for w in warnings if "negative" in w["message"]]

    book = await current_book(sessions)
    assert book["qty"] == {"XYZ": 10 * M} and book["cash"] == {"CAD": 700_00}
