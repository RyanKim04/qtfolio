"""Bank of Canada Valet client for daily USDCAD (FXUSDCAD) and the 3-month
T-bill yield (TB.CDN.90D.MID).

Rates persist as INTEGER micro-units (USDCAD × 1e6). The latest rate drives the
header currency toggle; ensure_history() backfills back to the first activity so
USD trades, cash and flows are valued at their own day's rate. The T-bill yield
(INTEGER basis points) is the risk-free rate of the risk-adjusted metrics.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import utcnow
from .models import FxRate, RiskFreeRate

log = logging.getLogger(__name__)

VALET_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
TBILL_SERIES = "TB.CDN.90D.MID"
TBILL_URL = f"https://www.bankofcanada.ca/valet/observations/{TBILL_SERIES}/json"
MICRO = Decimal(1_000_000)


class FxService:
    def __init__(self, sessions: async_sessionmaker):
        self._sessions = sessions
        self._http = httpx.AsyncClient(timeout=15.0)
        self._last_attempt: float = 0.0
        self._covered_from: date | None = None  # earliest start a backfill already ran for
        self._tbill_attempt: float = 0.0

    async def aclose(self):
        await self._http.aclose()

    async def latest(self) -> tuple[date, Decimal] | None:
        """Newest stored rate, refreshing from BoC at most hourly. Weekends and
        holidays reuse the last business-day observation (forward-fill)."""
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
            # A long absence outruns the 10-observation window, so fill the gap.
            await self.backfill(newest)
            return
        resp = await self._http.get(VALET_URL, params={"recent": 10})
        resp.raise_for_status()
        await self._store(resp.json().get("observations", []))

    async def ensure_history(self, start: date | None) -> None:
        """Backfill when stored rates begin after `start` (a fresh install has only
        the last ~10). Without it every earlier USD amount is valued at the oldest
        stored rate, or at 1.0 when none is stored yet (learned 2026-10-31). Never
        raises: a failed fetch keeps what is stored and retries on the next call."""
        if start is None or (self._covered_from is not None and start >= self._covered_from):
            return
        async with self._sessions() as session:
            oldest = (await session.execute(select(FxRate.date).order_by(FxRate.date).limit(1))).scalar_one_or_none()
        # A week of slack: `start` may be a weekend or holiday with no observation.
        if oldest is None or oldest > start + timedelta(days=7):
            try:
                n = await self.backfill(start)
            except Exception as exc:
                log.warning("BoC Valet backfill from %s failed: %s", start, exc)
                return
            log.info("fx history backfilled from %s: %d observations", start, n)
        self._covered_from = start

    async def backfill(self, start: date) -> int:
        """Full history from `start`, to value past USD positions and flows."""
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

    async def risk_free(self, start: date) -> dict[date, int]:
        """3-month T-bill yield in bp per business day from `start`. Fetches what is
        missing at most hourly; a failed fetch serves what is stored."""
        async with self._sessions() as session:
            span = (await session.execute(select(RiskFreeRate.date).order_by(RiskFreeRate.date))).scalars().all()
        now_ts = utcnow().timestamp()
        stale = not span or span[0] > start + timedelta(days=7) or (date.today() - span[-1]).days > 1
        if stale and now_ts - self._tbill_attempt > 3600:
            self._tbill_attempt = now_ts
            fetch_from = start if not span or span[0] > start + timedelta(days=7) else span[-1]
            try:
                resp = await self._http.get(TBILL_URL, params={"start_date": fetch_from.isoformat()})
                resp.raise_for_status()
                async with self._sessions() as session:
                    for obs in resp.json().get("observations", []):
                        v = (obs.get(TBILL_SERIES) or {}).get("v")
                        if not v:
                            continue
                        d = date.fromisoformat(obs["d"])
                        bp = int(Decimal(v) * 100)
                        row = await session.get(RiskFreeRate, d)
                        if row is None:
                            session.add(RiskFreeRate(date=d, rate_bp=bp))
                        else:
                            row.rate_bp = bp
                    await session.commit()
            except Exception as exc:
                log.warning("BoC T-bill fetch failed: %s", exc)
        async with self._sessions() as session:
            rows = (await session.execute(select(RiskFreeRate).where(RiskFreeRate.date >= start - timedelta(days=7)))).scalars().all()
        return {r.date: r.rate_bp for r in rows}
