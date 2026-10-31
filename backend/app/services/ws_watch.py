"""Downloads watcher — the Wealthsimple export imports itself.

Owner decision 2026-10-30: CSV stays the only WS source (the unofficial API
was tried and rolled back), so the manual part is cut to "download the export
in Wealthsimple". WS saves it as ~/Downloads/activities-export-YYYY-MM-DD.csv
(" (1)" on a same-day re-download); this loop polls the folder, recognizes any
CSV by its header (is_ws_export, not the name) and commits it through the same
dedup as an upload — overlapping or repeated exports add nothing.

Files are never moved or deleted. Seen content digests persist in settings
(`ws_watch_seen`), so a restart doesn't re-read old exports; one that is new
to the DB still imports (an export downloaded while the backend was down).
"""

import asyncio
import hashlib
import logging
import time
from pathlib import Path

from . import history, wealthsimple
from .importer import import_ws_export

log = logging.getLogger(__name__)

WATCH_DIR = Path.home() / "Downloads"
POLL_S = 3
SETTLE_S = 2           # a file modified this recently may still be mid-write
MAX_BYTES = 20 << 20   # an activities export is KBs; skip anything huge
SEEN_KEY = "ws_watch_seen"
SEEN_CAP = 200


class Watcher:
    def __init__(self, state, directory: Path = WATCH_DIR):
        self.state = state
        self.dir = directory
        self.error: str | None = None  # the scan itself failed (e.g. no folder permission)
        self._stat: dict[Path, tuple[float, int]] = {}  # files already looked at, by (mtime, size)
        self._failed: dict[Path, str] = {}  # exports whose import failed — retried every scan
        self._seen: list[str] = []

    def status(self) -> dict:
        error = self.error or next((f"{p.name}: {e}" for p, e in self._failed.items()), None)
        return {"dir": str(self.dir).replace(str(Path.home()), "~", 1), "error": error}

    async def run_forever(self) -> None:
        self._seen = list(await history.get_setting(self.state.sessions, SEEN_KEY, []) or [])
        while True:
            try:
                await self.scan()
                self.error = None
            except asyncio.CancelledError:
                raise
            except PermissionError:
                # macOS gates ~/Downloads per app (Privacy & Security → Files and Folders).
                self.error = f"no permission to read {self.status()['dir']}"
            except Exception as e:  # never let the loop die
                log.exception("downloads watcher scan failed")
                self.error = str(e)
            await asyncio.sleep(POLL_S)

    async def scan(self) -> list[dict]:
        results = []
        now = time.time()
        paths = sorted(self.dir.glob("*.csv"), key=lambda p: p.stat().st_mtime)
        self._failed = {p: e for p, e in self._failed.items() if p in paths}  # deleted → forgotten
        for path in paths:
            st = path.stat()
            sig = (st.st_mtime, st.st_size)
            if self._stat.get(path) == sig or now - st.st_mtime < SETTLE_S or st.st_size > MAX_BYTES:
                continue
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest in self._seen or not wealthsimple.is_ws_export(content):
                self._stat[path] = sig
                continue
            try:
                result = await import_ws_export(self.state, content, commit=True, source=path.name)
            except Exception as e:
                # Not marked looked-at, so the next scan retries (a locked DB clears
                # itself); the error stays up until this file imports. Logged once
                # per distinct failure — a broken file would otherwise log every scan.
                msg = str(e).splitlines()[0]  # SQLAlchemy appends the SQL + parameters
                if self._failed.get(path) != msg:
                    log.exception("auto-import of %s failed; retrying each scan", path.name)
                self._failed[path] = msg
                continue
            self._failed.pop(path, None)
            self._stat[path] = sig
            log.info("auto-imported %s: %d new, %d duplicates", path.name, result["imported"], result["duplicates"])
            self._seen = [*self._seen, digest][-SEEN_CAP:]
            await history.set_setting(self.state.sessions, SEEN_KEY, self._seen)
            results.append(result)
        return results
