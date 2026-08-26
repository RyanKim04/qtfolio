from fastapi import APIRouter, Request
from sqlalchemy import func, select

from ..models import Account, FxRate, Setting

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    state = request.app.state
    token = await state.tokens.status()

    jobs = []
    for job in state.runner.jobs:
        last = await state.runner.last_run(job.name)
        jobs.append({
            "name": job.name,
            "cron": job.cron,
            "last_run": None if last is None else {
                "started_at": last.started_at.isoformat(),
                "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                "status": last.status,
                "detail": last.detail,
            },
        })

    async with state.sessions() as session:
        n_accounts = (await session.execute(select(func.count()).select_from(Account))).scalar_one()
        fx = (await session.execute(select(FxRate).order_by(FxRate.date.desc()).limit(1))).scalar_one_or_none()
        regime = await session.get(Setting, "candle_adjustment_regime")

    from ..services import history

    warnings = await history.get_setting(state.sessions, "reconciliation_warnings", []) or []
    series = await history.series_coverage(state.sessions)

    from ..config import settings as env

    return {
        "token": token,
        "jobs": jobs,
        "sources": {"anthropic": bool(env.anthropic_api_key)},
        "progress": dict(state.runner.live_progress),
        "fx": None if fx is None else {"date": fx.date.isoformat(), "usdcad": fx.usdcad_micro / 1_000_000},
        "candle_adjustment_regime": regime.value_json.strip('"') if regime else None,
        "series": series,
        "warnings": warnings,
        "onboarding": {
            "has_token": token["status"] != "absent",
            "token_ok": token["status"] == "ok",
            "has_accounts": n_accounts > 0,
            "has_history": series is not None,
        },
    }
