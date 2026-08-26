import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

target_metadata = Base.metadata


def get_url() -> str:
    return context.config.get_main_option("sqlalchemy.url") or f"sqlite:///{settings.db_path}"


def run_migrations_offline() -> None:
    context.configure(url=get_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # batch mode: SQLite can't ALTER most things in place
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
