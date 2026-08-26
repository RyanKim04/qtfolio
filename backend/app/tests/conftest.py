import pytest

from app.db import create_engine_and_sessionmaker
from app.models import Base


@pytest.fixture
async def sessions(tmp_path):
    engine, sessions = create_engine_and_sessionmaker(tmp_path / "test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield sessions
    await engine.dispose()
