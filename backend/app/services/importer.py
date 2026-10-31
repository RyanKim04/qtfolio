"""Activities import — SPEC.md §4.3, two-phase: parse+preview first, commit on
confirmation. Rows land in `activities` through the multiset dedup, so
re-importing an overlapping export is safe. Parsing is per source
(services/wealthsimple.py); exports arrive by upload or from the Downloads
watcher (services/ws_watch.py), both through import_ws_export."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import utcnow
from ..models import Account
from . import history, wealthsimple
from .activities import assign_hashes, upsert_activities

# Upload and watcher may land the same export at once; one commit+recompute at a time.
_lock = asyncio.Lock()


def preview_report(parsed: dict) -> dict:
    rows = parsed["rows"]
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["type"] or "?"] = by_type.get(r["type"] or "?", 0) + 1
    dates = [r["transaction_date"] for r in rows]
    return {
        "n_rows": len(rows),
        "by_type": by_type,
        "date_range": [min(dates).isoformat(), max(dates).isoformat()] if dates else None,
        "accounts": sorted({r["account_id"] for r in rows}),
        "header_map": parsed["header_map"],
        "unmapped_headers": parsed["unmapped_headers"],
        "errors": parsed["errors"],
        "sample": [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items() if k != "raw_json"}
            for r in rows[:10]
        ],
    }


async def commit_import(sessions: async_sessionmaker, parsed: dict) -> dict:
    rows = assign_hashes(parsed["rows"])
    new = await upsert_activities(sessions, rows)
    return {"imported": new, "duplicates": len(rows) - new, "errors": parsed["errors"]}


async def import_ws_export(state, content: bytes, *, commit: bool, source: str | None = None) -> dict:
    """WS rows carry clean symbols (no Questrade-style junk codes), so no
    description repair — just ledger accounts, Yahoo symbol registry, bars."""
    parsed = wealthsimple.parse_ws_export(content)
    if not commit:
        return {"phase": "preview", "source": wealthsimple.BROKER, **preview_report(parsed)}
    async with _lock:
        async with state.sessions() as session:
            for acc_id, acc_type in parsed["accounts"].items():
                row = await session.get(Account, acc_id)
                if row is None:
                    row = Account(qt_account_id=acc_id, tracked=True, broker=wealthsimple.BROKER, status="Active")
                    session.add(row)
                row.type = acc_type or row.type
            await session.commit()
        result = await commit_import(state.sessions, parsed)
        if result["imported"]:
            tickers = sorted({r["ticker"] for r in parsed["rows"] if r["ticker"]})
            unresolved = await history.ensure_symbols_yf(state.sessions, state.yf, tickers)
            await history.refresh_market_data(state.sessions, state.yf)
            recompute = await history.recompute(state.sessions, state.yf)
            if unresolved:
                result["errors"] = result["errors"] + [f"could not resolve on Yahoo: {', '.join(unresolved)}"]
        else:
            # A re-download with nothing new (the common case) changes no state.
            recompute = {"ok": True, "days": 0, "skipped": "no new rows"}
    prev = await history.get_setting(state.sessions, "ws_last_import", None) or {}
    # An older export picked up later must not move "activity through" backwards.
    through = max(filter(None, [prev.get("through"), *(r["transaction_date"].isoformat() for r in parsed["rows"])]),
                  default=None)
    await history.set_setting(state.sessions, "ws_last_import", {
        "at": utcnow().isoformat(),
        "through": through,
        "imported": result["imported"],
        "file": source,
    })
    return {"phase": "committed", "source": wealthsimple.BROKER, **result, "recompute": recompute}
