"""FastAPI entrypoint. The lifespan runs the SPEC.md §13 startup sequence:
migrations → (1) unconditional token refresh → (2) catch-up pass → (3) schedules.
Catch-up is the primary job execution path on a laptop; cron firing is the bonus.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import ai as ai_api, health, imports, performance, portfolio, settings as settings_api, stocks as stocks_api, sync
from .config import BACKEND_DIR, REPO_ROOT, settings
from .db import default_engine
from .fx import FxService
from .jobs import portfolio_jobs, token_keepalive
from .jobs.runner import JobRunner
from .questrade.client import QuestradeAPIError, QuestradeClient
from .questrade.tokens import TokenAbsent, TokenBroken, TokenError, TokenManager
from .services.portfolio import PortfolioService
from .services.quotes import QuoteCache
from .services.stocks import StockService
from .yf import YfService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("qtfolio")


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{settings.db_path}")
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(run_migrations)

    engine, sessions = default_engine()
    tokens = TokenManager(sessions)
    qt = QuestradeClient(tokens)
    fx = FxService(sessions)
    yf = YfService(sessions)
    runner = JobRunner(sessions)
    runner.register(token_keepalive.make_job(tokens))
    runner.register(portfolio_jobs.make_eod_snapshot(sessions, qt, fx, yf))
    runner.register(portfolio_jobs.make_activities_sync(sessions, qt, yf))
    runner.register(portfolio_jobs.make_db_backup(sessions))
    runner.register(portfolio_jobs.make_bootstrap(sessions, qt, fx, yf))

    app.state.engine = engine
    app.state.sessions = sessions
    app.state.tokens = tokens
    app.state.qt = qt
    app.state.fx = fx
    app.state.yf = yf
    app.state.runner = runner
    app.state.portfolio = PortfolioService(sessions, qt, QuoteCache(qt), fx)
    app.state.stocks = StockService(sessions, qt, fx)

    async def startup_sequence():
        # (1) Refresh the token chain unconditionally on every process start
        # (§4.1.6) — this IS the primary keepalive; logged as a token_keepalive run.
        await runner.run("token_keepalive", trigger="startup")
        # (2) Run overdue jobs (token_keepalive just succeeded, so it's skipped).
        await runner.catch_up()
        # (3) Only now do the cron schedules begin.
        runner.start_scheduler()
        log.info("startup sequence complete; scheduler running")

    task = asyncio.create_task(startup_sequence())
    try:
        yield
    finally:
        task.cancel()
        runner.shutdown()
        await qt.aclose()
        await tokens.aclose()
        await fx.aclose()
        await engine.dispose()


app = FastAPI(title="qtfolio", lifespan=lifespan)

app.include_router(health.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(performance.router, prefix="/api")
app.include_router(stocks_api.router, prefix="/api")
app.include_router(ai_api.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(sync.router, prefix="/api")


@app.exception_handler(TokenAbsent)
async def _absent(request, exc):
    return JSONResponse(status_code=503, content={"error": "token_absent", "message": str(exc)})


@app.exception_handler(TokenBroken)
async def _broken(request, exc):
    return JSONResponse(status_code=503, content={"error": "token_broken", "message": str(exc)})


@app.exception_handler(TokenError)
async def _token_err(request, exc):
    return JSONResponse(status_code=503, content={"error": "token_error", "message": str(exc)})


@app.exception_handler(QuestradeAPIError)
async def _qt_err(request, exc):
    return JSONResponse(status_code=502, content={"error": "questrade_api", "message": str(exc)})


# Prod mode: serve the built SPA (vite build output) with a client-route fallback.
_dist = REPO_ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        file = _dist / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(_dist / "index.html")
