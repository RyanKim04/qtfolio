"""Window attribution's lines (performance._attribute) on synthetic rows: every
non-position item lands in its own line and the lines reconcile to the total."""

from datetime import date
from types import SimpleNamespace

from app.api.performance import _attribute
from app.models import Activity, CashLedgerRow, FxRate

D0, D1, D2 = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)


def _act(i, typ, action, currency, cents, ticker=None):
    return Activity(account_id="SYN1", hash=f"h{i}", occurrence_idx=0, transaction_date=D1,
                    type=typ, action=action, currency=currency, net_amount_cents=cents, ticker=ticker)


async def test_lines_split_and_reconcile(sessions):
    async with sessions() as s:
        s.add_all([FxRate(date=D0, usdcad_micro=1_380_000), FxRate(date=D2, usdcad_micro=1_400_000)])
        s.add_all([CashLedgerRow(date=d, currency="USD", balance_cents=1000_00) for d in (D0, D1, D2)])
        s.add_all([
            _act(1, "fees and rebates", "fch", "CAD", -100_00),
            _act(2, "other", "hst", "CAD", -13_00),  # QT posts sales tax as a cash-only "Other" row
            _act(3, "fees and rebates", "transfer_fee_reimbursement", "CAD", 115_00),
            _act(4, "fees and rebates", "nrt", "USD", -1_00),  # withholding tax, US$1 @ 1.38
            _act(5, "interest", "int", "CAD", 2_00),
            _act(6, "fx conversion", "fxt", "CAD", -1400_00),  # broker rate 1.40 vs market 1.38
            _act(7, "fx conversion", "fxt", "USD", 1000_00),
            _act(8, "dividends", "div", "CAD", 5_00, ticker="XYZ.TO"),
        ])
        await s.commit()
    cash_fx = 1000_00 * (1_400_000 - 1_380_000) // 1_000_000  # US$1000 held while USD/CAD rose: +$20
    net = -100_00 - 13_00 + 115_00 - 1_38 + 2_00 - 20_00 + 5_00 + cash_fx
    m = {
        "dates": [D0, D1, D2],
        "values": {D0: 10_000_00, D1: 10_000_00, D2: 10_000_00 + net},
        "flows": {}, "capital": {D0: 10_000_00, D1: 10_000_00, D2: 10_000_00},
        "fx_cum": {D0: 0, D1: -20_00, D2: -20_00 + cash_fx},  # conversion cost + cash translation
        "ledger_end": D2, "live_price": {}, "live_reason": "",
    }
    a = await _attribute(SimpleNamespace(sessions=sessions), m, D0, D2)
    assert [(r["ticker"], r["pnl"], r["dividends"]) for r in a["rows"]] == [("XYZ.TO", 0, 5.0)]  # row is price only
    assert a["dividends"] == 5.0 and a["interest"] == 2.0
    assert a["usd_cash_fx"] == 20.0
    assert a["conversion_cost"] == -20.0
    assert a["fees"] == -114.38  # fee + HST + withholding tax
    assert a["rebates"] == 115.0
    assert a["other"] == 0  # nothing left unexplained
    assert a["adjustments"] == 0.62
    lines = a["price_gains"] + a["dividends"] + a["interest"] + a["usd_cash_fx"] + a["conversion_cost"] + a["adjustments"]
    assert round(lines, 2) == a["total_pnl"] == net / 100
    assert a["fx_pnl"] == 0 and a["fx_rate_pnl"] == 20.0
    assert round(a["investment_pnl"] + a["fx_pnl"], 2) == a["total_pnl"]
