"""§4.2/§7: multiset-aware dedup — occurrence-indexed hashes, overlapping syncs,
cross-source (API vs CSV) collapse, and the importer's header mapping."""

from datetime import date

from sqlalchemy import select

from app.models import Activity
from app.services.activities import (
    assign_hashes,
    collapse_duplicate_dividends,
    upsert_activities,
)
from app.services.importer import parse_export


def div_row(ticker, net=371, desc=None, td=date(2026, 6, 26), txn=date(2026, 6, 26),
            account="A", action="div"):
    return {
        "account_id": account, "trade_date": td, "transaction_date": txn, "settlement_date": txn,
        "type": "dividends", "action": action, "ticker": ticker, "qty_micro": 0, "price_micro": 0,
        "gross_amount_cents": net, "commission_cents": 0, "net_amount_cents": net,
        "currency": "USD", "description": desc or "INVESCO NASDAQ 100 ETF CASH DIV ON 12 SHS REC 06/22/26 PAY 06/26/26",
        "raw_json": "{}",
    }


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


async def test_same_dividend_under_clean_and_registry_symbol_dedups(sessions):
    """Regression (2026-09-21): the SAME dividend arrived once with a registry-key
    symbol + trade_date (bootstrap) and again with a clean ticker, no trade_date
    (re-sync after a new API key). The symbol was in the dedup key, so it landed
    twice and double-counted P&L. Dividends now dedup symbol-free."""
    boot = div_row("H082968", td=date(2026, 6, 26), txn=date(2026, 6, 26))
    resync = div_row("QQQM", td=None, txn=date(2026, 6, 26))
    assert await upsert_activities(sessions, assign_hashes([boot])) == 1
    assert await upsert_activities(sessions, assign_hashes([resync])) == 0


async def test_collapse_removes_duplicate_and_fixes_mangled_symbol(sessions):
    """Same-batch dupes (registry key + clean ticker, whitespace-different desc)
    still land as two rows; the collapse pass removes the dupe, keeps the clean
    ticker, and strips a mangled leading-dot symbol (.TD -> TD)."""
    boot = div_row("H082968", desc="INVESCO NASDAQ 100 ETF  CASH DIV  ON 12 SHS REC 06/22/26 PAY 06/26/26")
    resync = div_row("QQQM", desc="INVESCO NASDAQ 100 ETF CASH DIV ON 12 SHS REC 06/22/26 PAY 06/26/26")
    td_div = div_row(".TD", net=727, td=date(2024, 7, 31), txn=date(2024, 7, 31),
                     desc="TORONTO-DOMINION BANK CASH DIV ON 10 SHS REC 07/10/24 PAY 07/31/24")
    await upsert_activities(sessions, assign_hashes([boot, resync, td_div]))

    assert await collapse_duplicate_dividends(sessions) == 2  # 1 removed + 1 dot-fixed
    async with sessions() as s:
        tickers = sorted(r.ticker for r in (await s.execute(select(Activity))).scalars())
    assert tickers == ["QQQM", "TD"]  # H082968 gone, .TD -> TD


async def test_collapse_never_merges_different_stocks_same_amount(sessions):
    """Two DIFFERENT stocks paying the same amount on the same day must survive —
    the collapse requires matching normalized descriptions, so it can't merge them."""
    d = date(2024, 10, 31)
    jpm = div_row("JPM", net=425, td=d, txn=d, desc="JPMORGAN CHASE & CO CASH DIV ON 4 SHS REC 10/04/24 PAY 10/31/24")
    qqq = div_row("QQQ", net=425, td=d, txn=d, desc="INVESCO QQQ TR UNIT SER 1 CASH DIV ON 3 SHS REC 09/23/24 PAY 10/31/24")
    await upsert_activities(sessions, assign_hashes([jpm, qqq]))
    assert await collapse_duplicate_dividends(sessions) == 0
    async with sessions() as s:
        assert (await s.execute(select(Activity))).scalars().all().__len__() == 2


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
