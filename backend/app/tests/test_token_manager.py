"""SPEC.md §4.1 rules, unit-tested against a mocked login.questrade.com.

M1 acceptance: two parallel requests never double-refresh; persist-before-use;
30s timeout + exactly one same-token retry; invalid_grant semantics.
"""

import asyncio
import itertools
import json

import httpx
import pytest
import respx

from app.models import QtToken
from app.questrade.tokens import LOGIN_URL, TokenAbsent, TokenBroken, TokenError, TokenManager


def token_response(n: int, expires_in: int = 1800) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": f"A{n}",
            "refresh_token": f"R{n + 1}",
            "api_server": "https://api01.iq.questrade.com/",
            "token_type": "Bearer",
            "expires_in": expires_in,
        },
    )


@pytest.fixture
async def manager(sessions, tmp_path):
    m = TokenManager(sessions, backup_path=tmp_path / "qt_token_backup.json")
    yield m
    await m.aclose()


async def db_row(sessions) -> QtToken:
    async with sessions() as s:
        return await s.get(QtToken, 1)


@respx.mock
async def test_paste_exchanges_and_persists_before_returning(manager, sessions, tmp_path):
    route = respx.post(LOGIN_URL).mock(return_value=token_response(1))
    await manager.set_refresh_token("R1")

    assert route.call_count == 1
    assert route.calls[0].request.url.params["refresh_token"] == "R1"
    # New refresh token committed by the time the call returns (rule 1: persist before use),
    # observable from an independent session.
    row = await db_row(sessions)
    assert row.refresh_token == "R2"
    assert row.status == "ok"
    assert row.api_server == "https://api01.iq.questrade.com"
    # Belt-and-suspenders backup mirrored (rule 3).
    backup = json.loads((tmp_path / "qt_token_backup.json").read_text())
    assert backup["refresh_token"] == "R2"


@respx.mock
async def test_concurrent_ensure_access_single_exchange(manager, sessions):
    counter = itertools.count(1)
    route = respx.post(LOGIN_URL).mock(side_effect=lambda req: token_response(next(counter)))
    # Paste hands back an access token that is already inside the 60s refresh margin,
    # so every ensure_access() below wants a refresh — only one may perform it (rule 2).
    route.side_effect = None
    route.return_value = token_response(1, expires_in=30)
    await manager.set_refresh_token("R0")
    route.return_value = None
    route.side_effect = lambda req: token_response(next(counter) + 10)

    results = await asyncio.gather(*(manager.ensure_access() for _ in range(8)))

    assert route.call_count == 2  # the paste + exactly one refresh for all 8 callers
    assert len({access for access, _ in results}) == 1


@respx.mock
async def test_timeout_gets_exactly_one_same_token_retry(manager, sessions):
    route = respx.post(LOGIN_URL).mock(
        side_effect=[httpx.ConnectTimeout("timed out"), token_response(1)]
    )
    await manager.set_refresh_token("R1")

    assert route.call_count == 2
    assert route.calls[0].request.url.params["refresh_token"] == "R1"
    assert route.calls[1].request.url.params["refresh_token"] == "R1"  # same token on retry (rule 7)
    assert (await db_row(sessions)).status == "ok"


@respx.mock
async def test_two_timeouts_stop_after_one_retry_chain_intact(manager, sessions):
    route = respx.post(LOGIN_URL).mock(return_value=token_response(1))
    await manager.set_refresh_token("R1")

    route.side_effect = [httpx.ConnectTimeout("a"), httpx.ReadTimeout("b")]
    with pytest.raises(TokenError) as exc_info:
        await manager.refresh()
    assert not isinstance(exc_info.value, TokenBroken)

    assert route.call_count == 3  # paste + first attempt + exactly one retry, no more
    row = await db_row(sessions)
    assert row.status == "ok"  # transport failure must NOT break the chain
    assert row.refresh_token == "R2"


@respx.mock
async def test_invalid_grant_on_retry_after_timeout_breaks_chain(manager, sessions):
    respx.post(LOGIN_URL).mock(
        side_effect=[
            httpx.ConnectTimeout("timed out"),
            httpx.Response(400, json={"error": "invalid_grant"}),
        ]
    )
    with pytest.raises(TokenBroken):
        await manager.set_refresh_token("R1")
    assert (await db_row(sessions)).status == "broken"


@respx.mock
async def test_direct_invalid_grant_breaks_chain(manager, sessions):
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(TokenBroken):
        await manager.set_refresh_token("bad-token")
    assert (await db_row(sessions)).status == "broken"
    # And subsequent refreshes refuse to burn anything until a fresh paste.
    with pytest.raises(TokenBroken):
        await manager.refresh()


@respx.mock
async def test_bare_400_breaks_chain(manager, sessions):
    """Questrade returns a BARE 'Bad Request' (no invalid_grant JSON) for a
    consumed refresh token — observed in production 2026-08-14. Must classify
    as broken, not transient, or the UI never shows the re-paste banner."""
    respx.post(LOGIN_URL).mock(return_value=httpx.Response(400, text="Bad Request"))
    with pytest.raises(TokenBroken):
        await manager.set_refresh_token("consumed-token")
    assert (await db_row(sessions)).status == "broken"


@respx.mock
async def test_server_error_is_transient_not_broken(manager, sessions):
    route = respx.post(LOGIN_URL).mock(return_value=token_response(1))
    await manager.set_refresh_token("R1")

    route.return_value = httpx.Response(503, text="maintenance")
    route.side_effect = None
    with pytest.raises(TokenError) as exc_info:
        await manager.refresh()
    assert not isinstance(exc_info.value, TokenBroken)
    assert (await db_row(sessions)).status == "ok"


@respx.mock
async def test_refresh_without_token_raises_absent(manager):
    with pytest.raises(TokenAbsent):
        await manager.refresh()


@respx.mock
async def test_force_refresh_skips_when_already_rotated(manager, sessions):
    counter = itertools.count(1)
    route = respx.post(LOGIN_URL).mock(side_effect=lambda req: token_response(next(counter)))
    await manager.set_refresh_token("R0")
    stale_access, _ = await manager.ensure_access()

    # Two request paths both hit a 401 with the same stale access token.
    await asyncio.gather(manager.force_refresh(stale_access), manager.force_refresh(stale_access))

    assert route.call_count == 2  # paste + ONE forced refresh; the second was a no-op
