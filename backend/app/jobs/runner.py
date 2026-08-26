"""Job framework — SPEC.md §13.

Every run is logged to job_runs. Catch-up-on-startup is the PRIMARY execution
path on a laptop; the APScheduler cron firing is the bonus. A job is overdue
when its last success is older than its cadence (or a custom due-predicate says
so — M2's eod_snapshot "only if a market close occurred" plugs in there).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import TZ, utcnow
from ..models import JobRun

log = logging.getLogger(__name__)


@dataclass
class Job:
    name: str
    func: Callable[..., Awaitable[str | None]]  # returns a human-readable detail line
    interval: timedelta | None                  # overdue threshold; None = manual-only (never caught up)
    cron: dict | None                           # CronTrigger kwargs; None = manual-only (never scheduled)
    # (last_success_utc | None, now_utc) -> bool; overrides the interval check.
    due: Callable[[datetime | None, datetime], bool] | None = None
    # func accepts a progress(msg) callback (long jobs like bootstrap)
    wants_progress: bool = False


class JobRunner:
    def __init__(self, sessions: async_sessionmaker):
        self._sessions = sessions
        self._jobs: dict[str, Job] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.scheduler: AsyncIOScheduler | None = None
        self.live_progress: dict[str, str] = {}  # latest progress line per running job

    def register(self, job: Job) -> None:
        self._jobs[job.name] = job
        self._locks[job.name] = asyncio.Lock()

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs.values())

    async def last_run(self, name: str) -> JobRun | None:
        async with self._sessions() as session:
            return (
                await session.execute(
                    select(JobRun)
                    .where(JobRun.job_name == name)
                    .order_by(JobRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def last_success(self, name: str) -> datetime | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(JobRun)
                    .where(JobRun.job_name == name, JobRun.status == "success")
                    .order_by(JobRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return row.started_at if row else None

    async def run(self, name: str, trigger: str = "manual") -> dict:
        """Run a job now, serialized per job. Exceptions land in job_runs, never
        propagate — a failing job must not take down startup or the scheduler."""
        job = self._jobs.get(name)
        if job is None:
            raise KeyError(f"unknown job {name!r}")
        lock = self._locks[name]
        if lock.locked():
            return {"job": name, "status": "already-running"}
        async with lock:
            async with self._sessions() as session:
                row = JobRun(job_name=name, started_at=utcnow(), status="running", detail=f"[{trigger}]")
                session.add(row)
                await session.commit()
                run_id = row.id

            def progress(msg: str) -> None:
                self.live_progress[name] = msg

            try:
                detail = await (job.func(progress) if job.wants_progress else job.func())
                status, detail = "success", f"[{trigger}] {detail or 'ok'}"
            except Exception as exc:
                status, detail = "error", f"[{trigger}] {type(exc).__name__}: {exc}"
                log.warning("job %s failed: %s", name, detail)
            finally:
                self.live_progress.pop(name, None)
            async with self._sessions() as session:
                row = await session.get(JobRun, run_id)
                row.finished_at = utcnow()
                row.status = status
                row.detail = detail
                await session.commit()
            return {"job": name, "status": status, "detail": detail}

    async def catch_up(self, trigger: str = "startup") -> None:
        now = utcnow()
        for job in self._jobs.values():
            if job.due is None and job.interval is None:
                continue  # manual-only (e.g. bootstrap)
            last = await self.last_success(job.name)
            if job.due is not None:
                overdue = job.due(last, now)
            else:
                overdue = last is None or now - last >= job.interval
            if overdue:
                log.info("catch-up: %s is overdue (last success %s)", job.name, last)
                await self.run(job.name, trigger=trigger)

    def start_scheduler(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone=TZ)
        for job in self._jobs.values():
            if job.cron is None:
                continue  # manual-only
            self.scheduler.add_job(
                self.run,
                CronTrigger(timezone=TZ, **job.cron),
                args=[job.name, "schedule"],
                id=job.name,
                misfire_grace_time=3600,
                coalesce=True,
            )
        # Long-running processes need catch-up too, not just boots: a cron run
        # that fails transiently (e.g. wake-from-sleep before Wi-Fi is back)
        # would otherwise never retry until the next restart. The hourly sweep
        # re-runs anything overdue; per-job locks make overlaps safe.
        self.scheduler.add_job(
            self.catch_up,
            CronTrigger(timezone=TZ, minute=55),
            args=["catch-up"],
            id="catch_up_sweep",
            misfire_grace_time=1800,
            coalesce=True,
        )
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
