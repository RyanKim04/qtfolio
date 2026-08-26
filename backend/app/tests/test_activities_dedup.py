"""§4.2/§7: multiset-aware dedup — occurrence-indexed hashes, overlapping syncs,
cross-source (API vs CSV) collapse, and the importer's header mapping."""

from datetime import date

from app.services.activities import assign_hashes, upsert_activities
from app.services.importer import parse_export


def row(d, ticker="AAPL", qty=10_000_000, net=-170000, desc="BUY AAPL", account="A", type_="trades"):
    return {
        "account_id": account, "trade_date": d, "transaction_date": d, "settlement_date": d,
        "type": type_, "action": "buy", "ticker": ticker, "qty_micro": qty, "price_micro": 17_000_000,
        "gross_amount_cents": net, "commission_cents": 0, "net_amount_cents": net,
        "currency": "CAD", "description": desc, "raw_json": "{}",
    }


def test_identical_same_day_fills_get_distinct_occurrence_hashes():
    d = date(2024, 1, 3)
    hashed = assign_hashes([row(d), row(d)])
    assert hashed[0]["hash"] != hashed[1]["hash"]
    assert sorted(h["occurrence_idx"] for h in hashed) == [0, 1]


def test_occurrence_hashes_are_stable_across_syncs():
    d = date(2024, 1, 3)
    first = assign_hashes([row(d), row(d)])
    second = assign_hashes([row(d), row(d)])
    assert {h["hash"] for h in first} == {h["hash"] for h in second}


async def test_overlapping_sync_inserts_nothing_new(sessions):
    d = date(2024, 1, 3)
    batch = assign_hashes([row(d), row(d), row(d, ticker="NVDA")])
    assert await upsert_activities(sessions, batch) == 3
    # The overlap window refetches the same day → same hashes → 0 new.
    again = assign_hashes([row(d), row(d), row(d, ticker="NVDA")])
    assert await upsert_activities(sessions, again) == 0


async def test_multiset_third_identical_fill_is_new(sessions):
    d = date(2024, 1, 3)
    assert await upsert_activities(sessions, assign_hashes([row(d), row(d)])) == 2
    # A day later the same day is fetched with THREE identical fills: exactly one is new.
    assert await upsert_activities(sessions, assign_hashes([row(d), row(d), row(d)])) == 1


async def test_csv_and_api_versions_of_same_activity_collapse(sessions):
    d = date(2024, 1, 3)
    api_version = row(d, desc="AAPL - APPLE INC BOUGHT 10 SHARES")
    csv_version = row(d, desc="Bought 10 AAPL @ 170.00")  # description differs → excluded from hash
    assert await upsert_activities(sessions, assign_hashes([api_version])) == 1
    assert await upsert_activities(sessions, assign_hashes([csv_version])) == 0


async def test_api_and_csv_dedup_across_posting_date_shift(sessions):
    """The API posts trades at T+1 (transactionDate) with tradeDate = T; the
    web export stamps T itself with no separate trade date. Same fill, one row."""
    t = date(2024, 1, 3)
    api_version = {**row(t), "trade_date": t, "transaction_date": date(2024, 1, 4)}
    csv_version = {**row(t), "trade_date": None, "transaction_date": t}
    assert await upsert_activities(sessions, assign_hashes([api_version])) == 1
    assert await upsert_activities(sessions, assign_hashes([csv_version])) == 0


def test_distinct_rows_differing_only_in_description_both_survive():
    d = date(2024, 1, 3)
    hashed = assign_hashes([row(d, desc="dividend NVDA"), row(d, desc="withholding NVDA")])
    assert hashed[0]["hash"] != hashed[1]["hash"]  # occupy occurrence 0 and 1


def test_importer_maps_questrade_export_headers():
    csv_data = (
        "Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,"
        "Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type\n"
        "2023-05-01,2023-05-03,Buy,MSFT,BOUGHT 5 MSFT,5,300.00,-1500.00,-4.95,-1504.95,USD,111,Trades,Margin\n"
        "2023-05-02,2023-05-02,CON,,CONTRIBUTION,,,2000.00,,2000.00,CAD,111,Deposits,TFSA\n"
    ).encode()
    parsed = parse_export("activities.csv", csv_data)
    assert parsed["errors"] == []
    assert len(parsed["rows"]) == 2
    trade, deposit = parsed["rows"]
    assert trade["ticker"] == "MSFT" and trade["qty_micro"] == 5_000_000
    assert trade["net_amount_cents"] == -150495 and trade["currency"] == "USD"
    assert trade["transaction_date"] == date(2023, 5, 1) and trade["account_id"] == "111"
    assert deposit["type"] == "deposits" and deposit["net_amount_cents"] == 200000
