"""token_keepalive — daily 06:00 backup to the startup refresh (§4.1.6).

The startup refresh is no longer strictly unconditional: if the chain rotated
within the last 30 minutes, skip. Rationale (incident 2026-08-13): uvicorn
hot-reload restarts the app on every code edit, each restart fired an exchange,
and a reload killed one mid-flight — the exchange succeeded server-side, the
response was lost, chain burned (§4.1.7 realized via --reload). A 30-minute
freshness guard removes that failure mode while keeping the daily-cold-start
keepalive fully intact.
"""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import utcnow
from ..questrade.tokens import TokenAbsent, TokenBroken, TokenManager
from .runner import Job

FRESH_WINDOW = timedelta(minutes=30)


def make_job(tokens: TokenManager, sessions: async_sessionmaker) -> Job:
    async def run() -> str:
        from ..services import history

        if await history.qt_retired(sessions):
            return "skipped: Questrade retired (moved to Wealthsimple) — no token chain to keep alive"
        status = await tokens.status()
        if status["status"] == "ok" and status["updated_at"]:
            age = utcnow() - datetime.fromisoformat(status["updated_at"])
            if age < FRESH_WINDOW:
                return f"skipped: chain rotated {int(age.total_seconds() // 60)}m ago (fresh)"
        try:
            await tokens.refresh()
        except TokenAbsent:
            return "skipped: no token configured yet"
        except TokenBroken:
            return "skipped: token chain broken — paste a fresh token in Settings"
        return "token chain refreshed"

    return Job(
        name="token_keepalive",
        func=run,
        interval=timedelta(hours=20),  # a hair under daily so a next-morning start counts as overdue
        cron={"hour": 6, "minute": 0},
    )
