from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .config import settings


def create_engine_and_sessionmaker(db_path: Path) -> tuple[AsyncEngine, async_sessionmaker]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)


def default_engine() -> tuple[AsyncEngine, async_sessionmaker]:
    return create_engine_and_sessionmaker(settings.db_path)
