"""Bank of Canada Valet — daily USDCAD (FXUSDCAD).

Rates persist as INTEGER micro-units (USDCAD × 1e6). M1 needs only the latest
rate for the header currency toggle; M2's bootstrap backfills history.
"""

import logging
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import utcnow
from .models import FxRate

log = logging.getLogger(__name__)

VALET_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
MICRO = Decimal(1_000_000)


class FxService:
    def __init__(self, sessions: async_sessionmaker):
        self._sessions = sessions
        self._http = httpx.AsyncClient(timeout=15.0)
        self._last_attempt: float = 0.0

    async def aclose(self):
        await self._http.aclose()

    async def latest(self) -> tuple[date, Decimal] | None:
        """Newest stored rate, refreshing from BoC at most hourly. Weekends and
        holidays simply reuse the last business-day observation (forward-fill)."""
        now_ts = utcnow().timestamp()
        if now_ts - self._last_attempt > 3600:
            self._last_attempt = now_ts
            try:
                await self._fetch_recent()
            except Exception as exc:  # degrade to the stored rate, never crash
                log.warning("BoC Valet fetch failed: %s", exc)
        async with self._sessions() as session:
            row = (
                await session.execute(select(FxRate).order_by(FxRate.date.desc()).limit(1))
            ).scalar_one_or_none()
        if row is None:
            return None
        return row.date, Decimal(row.usdcad_micro) / MICRO

    async def _fetch_recent(self) -> None:
        async with self._sessions() as session:
            newest = (await session.execute(select(FxRate.date).order_by(FxRate.date.desc()).limit(1))).scalar_one_or_none()
        if newest is not None and (date.today() - newest).days > 10:
            # A long absence outruns the 10-observation window — fill the gap.
            await self.backfill(newest)
            return
        resp = await self._http.get(VALET_URL, params={"recent": 10})
        resp.raise_for_status()
        await self._store(resp.json().get("observations", []))

    async def backfill(self, start: date) -> int:
        """Full history from `start` — needed to value USD positions/flows historically."""
        resp = await self._http.get(VALET_URL, params={"start_date": start.isoformat()})
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
        await self._store(observations)
        return len(observations)

    async def _store(self, observations: list[dict]) -> None:
        if not observations:
            return
        async with self._sessions() as session:
            for obs in observations:
                d = date.fromisoformat(obs["d"])
                micro = int(Decimal(obs["FXUSDCAD"]["v"]) * MICRO)
                existing = await session.get(FxRate, d)
                if existing is None:
                    session.add(FxRate(date=d, usdcad_micro=micro))
                else:
                    existing.usdcad_micro = micro
            await session.commit()
