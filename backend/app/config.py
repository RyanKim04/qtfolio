"""App configuration. Questrade tokens are NOT here — they rotate and live in the DB."""

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
    token_backup_path: Path = DATA_DIR / "qt_token_backup.json"


settings = Settings()


def utcnow() -> datetime:
    """Naive UTC — the storage convention for every datetime column."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
