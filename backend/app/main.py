"""FastAPI entrypoint. The lifespan runs the startup sequence:
migrations → (1) catch-up pass → (2) schedules. Catch-up is the primary job
execution path on a laptop; cron firing is the bonus.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api import health, imports, performance, portfolio, settings as settings_api, stocks as stocks_api, sync, tfsa
from .config import BACKEND_DIR, REPO_ROOT, settings
from .db import default_engine
from .fx import FxService
from .jobs import portfolio_jobs
from .jobs.runner import JobRunner
from .services.portfolio import PortfolioService
from .services.stocks import StockService
from .services.ws_watch import Watcher
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
    fx = FxService(sessions)
    yf = YfService(sessions)
    runner = JobRunner(sessions)
    runner.register(portfolio_jobs.make_eod_snapshot(sessions, fx, yf))
    runner.register(portfolio_jobs.make_db_backup(sessions))

    app.state.engine = engine
    app.state.sessions = sessions
    app.state.fx = fx
    app.state.yf = yf
    app.state.runner = runner
    app.state.portfolio = PortfolioService(sessions, yf, fx)
    app.state.stocks = StockService(sessions, yf, fx, app.state.portfolio)
    app.state.ws_watch = Watcher(app.state)

    async def startup_sequence():
        # (1) Run overdue jobs. (2) Only then do the cron schedules begin.
        await runner.catch_up()
        runner.start_scheduler()
        log.info("startup sequence complete; scheduler running")

    task = asyncio.create_task(startup_sequence())
    stream = asyncio.create_task(yf.stream_forever())
    watch = asyncio.create_task(app.state.ws_watch.run_forever())
    try:
        yield
    finally:
        task.cancel()
        stream.cancel()
        watch.cancel()
        runner.shutdown()
        await fx.aclose()
        await engine.dispose()


app = FastAPI(title="qtfolio", lifespan=lifespan)

# Binding 127.0.0.1 keeps other machines out, not other websites: any page the
# owner opens can POST a form here (fake activities via /import/activities), and
# DNS rebinding lets it read responses. Only local Hosts and local Origins pass;
# the Vite proxy forwards both unchanged.
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)


@app.middleware("http")
async def refuse_foreign_origins(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).hostname not in LOCAL_HOSTS:  # "null" (sandboxed/file) too
        return PlainTextResponse("cross-origin request refused", status_code=403)
    return await call_next(request)


app.include_router(health.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(performance.router, prefix="/api")
app.include_router(stocks_api.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(tfsa.router, prefix="/api")


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
