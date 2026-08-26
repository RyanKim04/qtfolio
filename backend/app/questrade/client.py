"""Questrade API client — SPEC.md §4.2.

Behaviors: api_server always comes from the token response (never hardcoded);
401 → one forced token refresh → one retry; honor X-RateLimit-* headers;
exponential backoff + jitter on 429/5xx; small concurrency cap.
"""

import asyncio
import logging
import random
from datetime import datetime

import httpx

from ..config import TZ, utcnow
from .tokens import TokenManager

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


class QuestradeAPIError(Exception):
    pass


def qt_datetime(dt: datetime) -> str:
    """Questrade wants ISO 8601 with an offset; naive input is taken as UTC."""
    if dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).isoformat(timespec="seconds")


class QuestradeClient:
    def __init__(self, tokens: TokenManager):
        self._tokens = tokens
        self._http = httpx.AsyncClient(timeout=20.0)
        self._sem = asyncio.Semaphore(4)
        self._rate_reset: float | None = None  # unix ts to wait until (remaining=0)

    async def aclose(self):
        await self._http.aclose()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        auth_retry_left = True
        backoff = 1.0
        for _ in range(MAX_ATTEMPTS):
            if self._rate_reset:
                delay = self._rate_reset - utcnow().timestamp()
                self._rate_reset = None
                if 0 < delay < 120:
                    log.info("rate limit exhausted; waiting %.1fs", delay)
                    await asyncio.sleep(delay)

            access, api_server = await self._tokens.ensure_access()
            async with self._sem:
                resp = await self._http.get(
                    f"{api_server}/{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {access}"},
                )

            remaining = resp.headers.get("X-RateLimit-Remaining")
            reset = resp.headers.get("X-RateLimit-Reset")
            if remaining == "0" and reset:
                self._rate_reset = float(reset)

            if resp.status_code == 401 and auth_retry_left:
                auth_retry_left = False
                await self._tokens.force_refresh(access)
                continue
            if resp.status_code == 429:
                delay = backoff
                if reset:
                    delay = max(0.5, min(float(reset) - utcnow().timestamp(), 120))
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                backoff = min(backoff * 2, 30)
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, 30)
                continue
            if resp.status_code != 200:
                raise QuestradeAPIError(f"GET {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise QuestradeAPIError(f"GET {path} failed after {MAX_ATTEMPTS} attempts")

    # -- endpoints (M1 subset) -------------------------------------------

    async def accounts(self) -> list[dict]:
        return (await self._get("v1/accounts")).get("accounts", [])

    async def positions(self, account_id: str) -> list[dict]:
        return (await self._get(f"v1/accounts/{account_id}/positions")).get("positions", [])

    async def balances(self, account_id: str) -> dict:
        return await self._get(f"v1/accounts/{account_id}/balances")

    async def activities(self, account_id: str, start: datetime, end: datetime) -> list[dict]:
        """Max 31-day window per request (§4.2) — callers own the pagination."""
        return (
            await self._get(
                f"v1/accounts/{account_id}/activities",
                {"startTime": qt_datetime(start), "endTime": qt_datetime(end)},
            )
        ).get("activities", [])

    async def quotes(self, symbol_ids: list[int]) -> list[dict]:
        if not symbol_ids:
            return []
        ids = ",".join(str(i) for i in symbol_ids)
        return (await self._get("v1/markets/quotes", {"ids": ids})).get("quotes", [])

    async def symbols_by_ids(self, symbol_ids: list[int]) -> list[dict]:
        if not symbol_ids:
            return []
        ids = ",".join(str(i) for i in symbol_ids)
        return (await self._get("v1/symbols", {"ids": ids})).get("symbols", [])

    async def symbol_search(self, prefix: str) -> list[dict]:
        return (await self._get("v1/symbols/search", {"prefix": prefix})).get("symbols", [])

    async def candles(self, symbol_id: int, start: datetime, end: datetime, interval: str) -> list[dict]:
        return (
            await self._get(
                f"v1/markets/candles/{symbol_id}",
                {"startTime": qt_datetime(start), "endTime": qt_datetime(end), "interval": interval},
            )
        ).get("candles", [])
