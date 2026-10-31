"""§4.2/§7: multiset-aware dedup — occurrence-indexed hashes, overlapping
imports, and cross-source collapse (the rules every stored hash was built on)."""

from datetime import date

from app.services.activities import assign_hashes, upsert_activities


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
