"""TFSA contribution room (services/tfsa.py) on a synthetic history. The
owner's real history (reproducing CRA's confirmed room) lives in the gitignored
test_tfsa_private.py."""

from datetime import date

from app.services.tfsa import (
    CONTRIBUTION, DEFAULT_LIMITS, TRANSFER, WITHDRAWAL, Txn, check, from_inception, summarize,
)

CRA_JAN1_2026 = 2_000_000  # synthetic CRA-confirmed Jan 1 room


def c(d, amount):
    return Txn(date.fromisoformat(d), CONTRIBUTION, round(amount * 100))


def w(d, amount):
    return Txn(date.fromisoformat(d), WITHDRAWAL, round(amount * 100))


# 2022–2026 limits sum to 33,500; net contributions of 13,500 leave the 20,000 anchor.
HISTORY = [
    c("2022-03-01", 6_000), c("2023-05-01", 5_000), c("2024-02-01", 3_000), w("2024-09-01", 2_000),
    c("2025-04-01", 2_500), w("2025-11-01", 1_000),
]
WS_2026 = c("2026-10-07", 3_000)
TODAY = date(2026, 10, 29)


def test_history_from_2022_reproduces_cra_jan1_2026():
    years = {r.year: r for r in from_inception(DEFAULT_LIMITS, HISTORY, 2026)}
    assert years[2022].jan1_cents == 600_000
    assert years[2026].jan1_cents == CRA_JAN1_2026


def test_history_never_overcontributed():
    for r in from_inception(DEFAULT_LIMITS, HISTORY, 2026):
        assert r.first_negative is None, r.year
        assert r.end_cents >= 0, r.year


def test_remaining_after_wealthsimple_contribution():
    years = {r.year: r for r in from_inception(DEFAULT_LIMITS, HISTORY + [WS_2026], 2026)}
    assert years[2026].end_cents == 1_700_000
    # The anchored walk agrees, and ignores pre-anchor rows already inside the anchor.
    for txns in ([WS_2026], HISTORY + [WS_2026]):
        s = summarize(TODAY, 2026, CRA_JAN1_2026, DEFAULT_LIMITS, txns)
        assert s["remaining_cents"] == 1_700_000
        assert s["contributed_cents"] == 300_000 and s["withdrawn_cents"] == 0
        assert s["overcontribution"] is None


def test_next_jan1_projection_and_tba_limit():
    s = summarize(TODAY, 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026])
    # 2027 limit TBA → the 2026 limit (7,000) stands in, flagged provisional
    assert s["next_limit_provisional"] and s["next_limit_cents"] == 700_000
    assert s["projected_next_jan1_cents"] == 1_700_000 + 700_000
    s = summarize(TODAY, 2026, CRA_JAN1_2026, {**DEFAULT_LIMITS, 2027: 750_000}, [WS_2026])
    assert not s["next_limit_provisional"]
    assert s["projected_next_jan1_cents"] == 1_700_000 + 750_000


def test_withdrawal_returns_next_jan1_only():
    s = summarize(TODAY, 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026, w("2026-10-20", 1_000)])
    assert s["remaining_cents"] == 1_700_000  # not back this year
    assert s["projected_next_jan1_cents"] == 1_700_000 + 700_000 + 100_000
    nxt = summarize(date(2027, 1, 1), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026, w("2026-10-20", 1_000)])
    assert nxt["jan1_cents"] == 1_700_000 + 700_000 + 100_000


def test_transfer_between_institutions_is_ignored():
    transfer = Txn(date(2026, 10, 19), TRANSFER, 2_500_000)
    s = summarize(TODAY, 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026, transfer])
    assert s["remaining_cents"] == 1_700_000 and s["withdrawn_cents"] == 0


def test_overcontribution_is_flagged_with_monthly_tax():
    s = summarize(TODAY, 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026, c("2026-10-28", 20_000)])
    assert s["remaining_cents"] == -300_000
    assert s["overcontribution"] == {"excess_cents": 300_000, "monthly_tax_cents": 3_000,
                                     "since": date(2026, 10, 28)}


def codes(result):
    return {(x["code"], x["level"]) for x in result["warnings"]}


def test_check_contribution_over_remaining_room():
    r = check(c("2026-10-30", 20_000), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026], TODAY)
    assert codes(r) == {("over_contribution", "danger")}
    assert r["room_after_cents"] == -300_000
    assert "17,000.00" in r["warnings"][0]["message"] and "3,000.00" in r["warnings"][0]["message"]
    ok = check(c("2026-10-30", 1_000), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026], TODAY)
    assert ok["warnings"] == [] and ok["room_after_cents"] == 1_600_000


def test_check_recontributing_a_same_year_withdrawal():
    txns = [WS_2026, w("2026-10-20", 5_000)]
    fits = check(c("2026-10-25", 5_000), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, txns, TODAY)
    assert codes(fits) == {("recontribution", "warning")}
    # Fits only if the withdrawal had restored room → both turn red.
    over = check(c("2026-10-25", 20_000), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, txns, TODAY)
    assert codes(over) == {("over_contribution", "danger"), ("recontribution", "danger")}
    # A withdrawal dated AFTER the contribution isn't being re-contributed.
    later = check(c("2026-10-10", 1_000), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, txns, TODAY)
    assert later["warnings"] == []
    # Last year's withdrawal is fair game this year.
    jan = check(c("2027-01-05", 5_000), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, txns, date(2027, 1, 5))
    assert jan["warnings"] == []


def test_check_overcontribution_pushed_into_a_later_year():
    # 2027 already holds a contribution that fits exactly; a new 2026 one eats into it.
    txns = [WS_2026, c("2027-01-02", 17_000 + 7_000)]
    r = check(c("2026-12-01", 100), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, txns, TODAY)
    assert codes(r) == {("over_contribution", "danger")}
    assert "leaves 2027 over-contributed by 100.00" in r["warnings"][0]["message"]


def test_check_info_cases():
    assert codes(check(Txn(date(2026, 10, 19), TRANSFER, 100), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [], TODAY)) \
        == {("transfer", "info")}
    assert codes(check(w("2026-10-29", 100), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [], TODAY)) == {("withdrawal", "info")}
    before = check(c("2025-06-01", 100), 2026, CRA_JAN1_2026, DEFAULT_LIMITS, [], TODAY)
    assert codes(before) == {("before_anchor", "info")} and before["room_after_cents"] is None


def test_march_reconcile_reminder():
    args = (2026, CRA_JAN1_2026, DEFAULT_LIMITS, [WS_2026])
    assert summarize(date(2026, 3, 15), *args)["reconcile"] is None  # anchor already this year's
    assert summarize(date(2027, 2, 28), *args)["reconcile"] is None  # CRA not updated yet
    r = summarize(date(2027, 3, 1), *args)["reconcile"]
    assert r == {"year": 2027, "computed_jan1_cents": 1_700_000 + 700_000}


async def test_api_record_preview_commit_delete(sessions):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api import tfsa as tfsa_api

    app = FastAPI()
    app.include_router(tfsa_api.router, prefix="/api")
    app.state.sessions = sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/api/tfsa")).json()["configured"] is False
        ws = {"date": "2026-10-07", "institution": "Wealthsimple", "type": "contribution", "amount": "3000"}
        assert (await client.post("/api/tfsa/transactions", json=ws)).status_code == 409  # no anchor yet

        r = await client.put("/api/tfsa/config", json={
            "limits": {"2022": 6000, "2023": 6500, "2024": 7000, "2025": 7000, "2026": 7000, "2027": None},
            "anchor": {"year": 2026, "room": "20000.00"},
        })
        assert r.json()["anchor"] == {"year": 2026, "room": 20000.0}

        preview = (await client.post("/api/tfsa/transactions", json=ws)).json()
        assert preview["phase"] == "preview" and preview["room_after"] == 17000.0
        assert (await client.get("/api/tfsa")).json()["transactions"] == []  # preview writes nothing

        committed = (await client.post("/api/tfsa/transactions", json={**ws, "commit": True})).json()
        assert committed["phase"] == "committed"
        state = (await client.get("/api/tfsa")).json()
        assert state["remaining"] == 17000.0 and state["contributed"] == 3000.0
        assert state["projected_next_jan1"] == 24000.0 and state["next_limit_provisional"] is True

        assert (await client.delete(f"/api/tfsa/transactions/{committed['id']}")).json() == {"ok": True}
        assert (await client.get("/api/tfsa")).json()["remaining"] == 20000.0
