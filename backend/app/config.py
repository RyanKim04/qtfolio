"""App configuration (optional `.env` at the repo root; no API keys are needed)."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = REPO_ROOT / "data"

TZ = ZoneInfo("America/Toronto")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    db_path: Path = DATA_DIR / "qtfolio.db"


settings = Settings()


def utcnow() -> datetime:
    """Naive UTC, the storage convention for every datetime column."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
