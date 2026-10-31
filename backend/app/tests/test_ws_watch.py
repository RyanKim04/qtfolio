"""Downloads watcher: WS exports import themselves once, by content, whatever
their name; other CSVs and half-written files are left alone."""

import os
import time
from types import SimpleNamespace

from app.services import history, ws_watch
from app.services.ws_watch import SEEN_KEY, Watcher
from app.tests.test_wealthsimple import WS_CSV


def _write(path, content, age_s=10):
    path.write_bytes(content)
    t = time.time() - age_s
    os.utime(path, (t, t))


async def test_imports_each_export_once(tmp_path, sessions, monkeypatch):
    calls = []

    async def fake_import(state, content, *, commit, source=None):
        calls.append(source)
        return {"imported": 1, "duplicates": 0}

    monkeypatch.setattr(ws_watch, "import_ws_export", fake_import)
    w = Watcher(SimpleNamespace(sessions=sessions), tmp_path)
    _write(tmp_path / "activities-export-2026-10-30.csv", WS_CSV)
    _write(tmp_path / "budget.csv", b"date,amount\n2026-10-01,5\n")
    _write(tmp_path / "activities-export-2026-10-31.csv", WS_CSV + b"\n", age_s=0)  # still downloading

    await w.scan()
    assert calls == ["activities-export-2026-10-30.csv"]

    # Same content under the browser's re-download name → already seen.
    _write(tmp_path / "activities-export-2026-10-30 (1).csv", WS_CSV)
    await w.scan()
    assert calls == ["activities-export-2026-10-30.csv"]

    # The settled in-progress file is new content → imported.
    os.utime(tmp_path / "activities-export-2026-10-31.csv", (time.time() - 10,) * 2)
    await w.scan()
    assert calls[-1] == "activities-export-2026-10-31.csv"

    # Seen digests survive a restart.
    assert len(await history.get_setting(sessions, SEEN_KEY)) == 2


async def test_failed_import_is_retried_and_reported(tmp_path, sessions, monkeypatch):
    calls = []

    async def flaky_import(state, content, *, commit, source=None):
        calls.append(source)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return {"imported": 1, "duplicates": 0}

    monkeypatch.setattr(ws_watch, "import_ws_export", flaky_import)
    w = Watcher(SimpleNamespace(sessions=sessions), tmp_path)
    _write(tmp_path / "activities-export-2026-10-30.csv", WS_CSV)

    await w.scan()
    assert w.status()["error"] == "activities-export-2026-10-30.csv: database is locked"
    await w.scan()  # unchanged file, but the failed import is retried
    assert len(calls) == 2 and w.status()["error"] is None
    await w.scan()
    assert len(calls) == 2  # imported → looked at
