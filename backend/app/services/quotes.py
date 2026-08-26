"""60-second snapshot-quote cache (SPEC.md §4.2) — refreshed while a page is open."""

import logging

from ..config import utcnow
from ..questrade.client import QuestradeClient

log = logging.getLogger(__name__)

TTL_SECONDS = 60


class QuoteCache:
    def __init__(self, qt: QuestradeClient):
        self._qt = qt
        self._cache: dict[int, tuple[dict, float]] = {}

    async def get_many(self, symbol_ids: list[int]) -> dict[int, dict]:
        now = utcnow().timestamp()
        stale = [i for i in symbol_ids if i not in self._cache or now - self._cache[i][1] > TTL_SECONDS]
        if stale:
            try:
                for q in await self._qt.quotes(stale):
                    self._cache[q["symbolId"]] = (q, now)
            except Exception as exc:  # serve whatever is cached; dashboard must not 500 on a quote hiccup
                log.warning("quote refresh failed: %s", exc)
        return {i: self._cache[i][0] for i in symbol_ids if i in self._cache}
