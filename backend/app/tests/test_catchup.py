"""Startup catch-up framework — the primary job execution path."""

import asyncio
from datetime import timedelta

import pytest

from app.config import utcnow
from app.jobs.runner import Job, JobRunner
from app.models import JobRun


def recording_job(name: str, *, fail: bool = False, sleep: float = 0.0, due=None):
    calls: list[int] = []

    async def run() -> str:
        calls.append(1)
        if sleep:
            await asyncio.sleep(sleep)
        if fail:
            raise RuntimeError("kaput")
        return "did work"

    return Job(name=name, func=run, interval=timedelta(hours=20), cron={"hour": 6}, due=due), calls


async def seed_success(sessions, name: str, age: timedelta):
    async with sessions() as s:
        t = utcnow() - age
        s.add(JobRun(job_name=name, started_at=t, finished_at=t, status="success", detail="[schedule] ok"))
        await s.commit()


async def test_overdue_job_runs_on_catchup(sessions):
    runner = JobRunner(sessions)
    job, calls = recording_job("j1")
    runner.register(job)
    await seed_success(sessions, "j1", timedelta(days=2))

    await runner.catch_up()

    assert calls == [1]
    last = await runner.last_run("j1")
    assert last.status == "success"
    assert last.detail.startswith("[startup]")


async def test_fresh_job_skipped_on_catchup(sessions):
    runner = JobRunner(sessions)
    job, calls = recording_job("j1")
    runner.register(job)
    await seed_success(sessions, "j1", timedelta(hours=1))

    await runner.catch_up()

    assert calls == []


async def test_never_run_job_runs_on_catchup(sessions):
    runner = JobRunner(sessions)
    job, calls = recording_job("j1")
    runner.register(job)

    await runner.catch_up()

    assert calls == [1]


async def test_failed_run_does_not_count_as_success(sessions):
    """A failed scheduled run must leave the job overdue for the next catch-up."""
    runner = JobRunner(sessions)
    job, calls = recording_job("j1")
    runner.register(job)
    async with sessions() as s:
        t = utcnow() - timedelta(hours=1)
        s.add(JobRun(job_name="j1", started_at=t, finished_at=t, status="error", detail="[schedule] boom"))
        await s.commit()

    await runner.catch_up()

    assert calls == [1]


async def test_failure_is_recorded_and_does_not_break_catchup(sessions):
    runner = JobRunner(sessions)
    bad, bad_calls = recording_job("bad", fail=True)
    good, good_calls = recording_job("good")
    runner.register(bad)
    runner.register(good)

    await runner.catch_up()  # must not raise

    assert bad_calls == [1] and good_calls == [1]
    last = await runner.last_run("bad")
    assert last.status == "error"
    assert "kaput" in last.detail
    assert last.finished_at is not None


async def test_due_predicate_overrides_interval(sessions):
    runner = JobRunner(sessions)
    never, never_calls = recording_job("never", due=lambda last, now: False)
    always, always_calls = recording_job("always", due=lambda last, now: True)
    runner.register(never)
    runner.register(always)
    await seed_success(sessions, "always", timedelta(minutes=5))  # fresh, but predicate says due

    await runner.catch_up()

    assert never_calls == []  # never ran despite having no history
    assert always_calls == [1]


async def test_same_job_never_runs_concurrently(sessions):
    runner = JobRunner(sessions)
    job, calls = recording_job("slow", sleep=0.1)
    runner.register(job)

    results = await asyncio.gather(runner.run("slow"), runner.run("slow"))

    statuses = sorted(r["status"] for r in results)
    assert statuses == ["already-running", "success"]
    assert calls == [1]


async def test_unknown_job_raises(sessions):
    runner = JobRunner(sessions)
    with pytest.raises(KeyError):
        await runner.run("nope")
