"""Mandatory fixtures: backward-ledger construction over a synthetic
activities set: buys/sells, deposit/withdrawal, FX conversion, journal pair
posting 2 days apart, identical same-day fills, and a split under both regimes."""

from datetime import date

from app.services.reconstruction import BACK_ADJUSTED, UNADJUSTED, walk_backward

M = 1_000_000  # micro-shares per share
ANCHOR = date(2024, 1, 15)


def act(id, d, type_, action=None, ticker=None, qty=None, net=None, cur="CAD"):
    return {
        "id": id, "account_id": "A", "transaction_date": d, "type": type_, "action": action,
        "ticker": ticker, "qty_micro": round(qty * M) if qty is not None else None,
        "net_amount_cents": round(net * 100) if net is not None else 0, "currency": cur,
    }


def test_buys_sells_deposit_reverse_correctly():
    acts = [
        act(1, date(2024, 1, 2), "deposits", net=5000),
        act(2, date(2024, 1, 3), "trades", action="buy", ticker="NVDA", qty=150, net=-13500),
        act(3, date(2024, 1, 8), "trades", action="sell", ticker="NVDA", qty=50, net=4600),
    ]
    res = walk_backward({"NVDA": 100 * M}, {"CAD": 10_000_00}, ANCHOR, acts, BACK_ADJUSTED)

    assert res.daily_qty[date(2024, 1, 8)] == {"NVDA": 100 * M}   # EOD before reversing the sell
    assert res.daily_qty[date(2024, 1, 5)] == {"NVDA": 150 * M}   # between buy and sell
    assert res.daily_cash[date(2024, 1, 5)]["CAD"] == 5_400_00
    assert res.daily_qty[date(2024, 1, 2)] == {}                  # before the buy
    assert res.opening_qty == {}
    assert res.opening_cash["CAD"] == 13_900_00                   # 18 900 − 5 000 deposit
    assert res.flows == {date(2024, 1, 2): {"CAD": 5_000_00}}     # un-reversed sign, own date
    assert not [w for w in res.warnings if "negative" in w["message"]]


def test_withdrawal_flow_sign_and_date():
    acts = [act(1, date(2024, 1, 5), "withdrawals", net=-2000)]
    res = walk_backward({}, {"CAD": 1_000_00}, ANCHOR, acts, BACK_ADJUSTED)
    assert res.flows == {date(2024, 1, 5): {"CAD": -2_000_00}}
    assert res.opening_cash["CAD"] == 3_000_00  # had $3k before withdrawing $2k


def test_fx_conversion_moves_cash_zero_flow():
    acts = [
        act(1, date(2024, 1, 4), "fx conversion", net=-13500, cur="CAD"),
        act(2, date(2024, 1, 4), "fx conversion", net=10000, cur="USD"),
    ]
    res = walk_backward({}, {"CAD": 0, "USD": 10_000_00}, ANCHOR, acts, BACK_ADJUSTED)
    assert res.opening_cash == {"CAD": 13_500_00, "USD": 0}
    assert res.flows == {}  # Norbert legs are not contributions


def test_journal_pair_two_days_apart_zero_contribution():
    acts = [
        act(1, date(2024, 1, 2), "trades", action="buy", ticker="DLR", qty=100, net=-1000),
        act(2, date(2024, 1, 4), "transfers", ticker="DLR", qty=-100),
        act(3, date(2024, 1, 8), "transfers", ticker="DLR.U", qty=100),
    ]
    res = walk_backward({"DLR.U": 100 * M}, {"CAD": 0}, ANCHOR, acts, BACK_ADJUSTED)
    assert res.daily_qty[date(2024, 1, 8)] == {"DLR.U": 100 * M}
    assert res.daily_qty[date(2024, 1, 6)] == {}                   # in-flight between the legs
    assert res.daily_qty[date(2024, 1, 3)] == {"DLR": 100 * M}
    assert res.flows == {}
    assert not [w for w in res.warnings if "negative" in w["message"]]


def test_identical_same_day_fills_both_reverse():
    acts = [
        act(1, date(2024, 1, 3), "trades", action="buy", ticker="AAPL", qty=10, net=-1700),
        act(2, date(2024, 1, 3), "trades", action="buy", ticker="AAPL", qty=10, net=-1700),
    ]
    res = walk_backward({"AAPL": 20 * M}, {"CAD": 0}, ANCHOR, acts, BACK_ADJUSTED)
    assert res.opening_qty == {}
    assert res.opening_cash["CAD"] == 3_400_00


SPLIT = {"TQQQ": {date(2024, 1, 10): 2.0}}


def test_split_back_adjusted_without_broker_journals():
    # Bought 100 pre-split shares (= 200 today-units); anchor holds 200 today-units.
    acts = [act(1, date(2024, 1, 5), "trades", action="buy", ticker="TQQQ", qty=100, net=-5000)]
    res = walk_backward({"TQQQ": 200 * M}, {"CAD": 0}, ANCHOR, acts, BACK_ADJUSTED, SPLIT)
    assert res.daily_qty[date(2024, 1, 12)] == {"TQQQ": 200 * M}
    assert res.daily_qty[date(2024, 1, 8)] == {"TQQQ": 200 * M}   # today-units throughout
    assert res.opening_qty == {}                                   # pre-split buy reversed ×2


def test_split_back_adjusted_skips_broker_journals():
    acts = [
        act(1, date(2024, 1, 5), "trades", action="buy", ticker="TQQQ", qty=100, net=-5000),
        act(2, date(2024, 1, 10), "corporate actions", ticker="TQQQ", qty=-100),
        act(3, date(2024, 1, 10), "corporate actions", ticker="TQQQ", qty=200),
    ]
    res = walk_backward({"TQQQ": 200 * M}, {"CAD": 0}, ANCHOR, acts, BACK_ADJUSTED, SPLIT)
    assert sorted(res.skipped_split_journal_ids) == [2, 3]         # not double-applied
    assert res.daily_qty[date(2024, 1, 8)] == {"TQQQ": 200 * M}
    assert res.opening_qty == {}


def test_split_unadjusted_without_broker_journals():
    acts = [act(1, date(2024, 1, 5), "trades", action="buy", ticker="TQQQ", qty=100, net=-5000)]
    res = walk_backward({"TQQQ": 200 * M}, {"CAD": 0}, ANCHOR, acts, UNADJUSTED, SPLIT)
    assert res.daily_qty[date(2024, 1, 10)] == {"TQQQ": 200 * M}  # split day: post-split units
    assert res.daily_qty[date(2024, 1, 9)] == {"TQQQ": 100 * M}   # date-native before the split
    assert res.opening_qty == {}


def test_split_unadjusted_with_broker_journals_not_double_converted():
    acts = [
        act(1, date(2024, 1, 5), "trades", action="buy", ticker="TQQQ", qty=100, net=-5000),
        act(2, date(2024, 1, 10), "corporate actions", ticker="TQQQ", qty=-100),
        act(3, date(2024, 1, 10), "corporate actions", ticker="TQQQ", qty=200),
    ]
    res = walk_backward({"TQQQ": 200 * M}, {"CAD": 0}, ANCHOR, acts, UNADJUSTED, SPLIT)
    assert res.daily_qty[date(2024, 1, 9)] == {"TQQQ": 100 * M}   # journals converted; factor NOT reapplied
    assert res.opening_qty == {}


def test_negative_quantity_surfaces_warning_never_fudged():
    # A buy whose shares are absent from the anchor (missing sell/transfer-out
    # later in history) → reversing the buy drives the quantity negative.
    acts = [act(1, date(2024, 1, 5), "trades", action="buy", ticker="AMD", qty=50, net=-6000)]
    res = walk_backward({}, {"CAD": 0}, ANCHOR, acts, BACK_ADJUSTED)
    assert res.opening_qty == {"AMD": -50 * M}                     # reported as-is, never fudged
    assert any("negative" in w["message"] and w["ticker"] == "AMD" for w in res.warnings)


def test_unknown_type_logged_and_skipped():
    acts = [act(1, date(2024, 1, 5), "escheatment adjustment", net=123)]
    res = walk_backward({}, {"CAD": 1_000_00}, ANCHOR, acts, BACK_ADJUSTED)
    assert res.opening_cash["CAD"] == 1_000_00                     # cash effect skipped, not guessed
    assert any("unknown activity type" in w["message"] for w in res.warnings)


def test_drip_reverses_holdings_and_cash():
    acts = [act(1, date(2024, 1, 5), "dividends", action="drip", ticker="AAPL", qty=2, net=0)]
    res = walk_backward({"AAPL": 102 * M}, {"CAD": 0}, ANCHOR, acts, BACK_ADJUSTED)
    assert res.opening_qty == {"AAPL": 100 * M}
