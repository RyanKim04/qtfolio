from fastapi import APIRouter, File, Form, Request, UploadFile

from ..config import utcnow
from ..models import Account
from ..services import history, wealthsimple
from ..services.activities import rehash_all
from ..services.importer import commit_import, parse_export, preview_report

router = APIRouter()


@router.post("/import/activities")
async def import_activities(request: Request, file: UploadFile = File(...), commit: bool = Form(False)):
    """Two-phase (§4.3): default parses and returns the preview + row-mapping
    report; commit=true maps rows into `activities` (same multiset dedup) and
    triggers a recompute. Wealthsimple activities exports are auto-detected."""
    state = request.app.state
    content = await file.read()
    if wealthsimple.is_ws_export(content):
        return await _import_wealthsimple(state, content, commit)

    parsed = parse_export(file.filename or "upload.csv", content)
    # Register any well-formed new tickers from the file FIRST, so description
    # resolution of the junk-coded rows can match against them (e.g. the file
    # both introduces MCD and codes some MCD rows as "5BHYPY6.90").
    clean = sorted({
        r["ticker"] for r in parsed["rows"]
        if r.get("ticker") and not history._JUNK_SYMBOL.search(r["ticker"])
    })
    if clean:
        await history.resolve_symbols(state.sessions, state.qt, clean)
    # Cross-source dedup prep: normalize missing/junk symbols to real tickers
    # BEFORE hashing — both sources must produce the same tuple.
    await history.normalize_import_tickers(state.sessions, state.qt, parsed["rows"])
    if not commit:
        return {"phase": "preview", "source": "questrade", **preview_report(parsed)}
    await history.repair_missing_tickers(state.sessions, state.qt)
    # Unconditional: stored hashes must reflect the CURRENT tuple rule (trade-date
    # basis, repaired tickers) or cross-source dedup silently fails.
    await rehash_all(state.sessions)
    result = await commit_import(state.sessions, parsed)
    await history.repair_missing_tickers(state.sessions, state.qt)
    # New tickers need registry entries + candle coverage before valuation.
    await history.refresh_reference_data(state.sessions, state.qt, state.yf)
    recompute = await history.recompute(state.sessions, state.yf)
    return {"phase": "committed", **result, "recompute": recompute}


async def _import_wealthsimple(state, content: bytes, commit: bool) -> dict:
    """WS rows carry clean symbols (no Questrade-style junk codes), so no
    description repair — just ledger accounts, Yahoo symbol registry, bars."""
    parsed = wealthsimple.parse_ws_export(content)
    if not commit:
        return {"phase": "preview", "source": wealthsimple.BROKER, **preview_report(parsed)}
    async with state.sessions() as session:
        for acc_id, acc_type in parsed["accounts"].items():
            row = await session.get(Account, acc_id)
            if row is None:
                row = Account(qt_account_id=acc_id, tracked=True, broker=wealthsimple.BROKER, status="Active")
                session.add(row)
            row.type = acc_type or row.type
        await session.commit()
    result = await commit_import(state.sessions, parsed)
    tickers = sorted({r["ticker"] for r in parsed["rows"] if r["ticker"]})
    unresolved = await history.ensure_symbols_yf(state.sessions, state.yf, tickers)
    await history.refresh_market_data(state.sessions, state.yf)
    recompute = await history.recompute(state.sessions, state.yf)
    dates = [r["transaction_date"] for r in parsed["rows"]]
    await history.set_setting(state.sessions, "ws_last_import", {
        "at": utcnow().isoformat(),
        "through": max(dates).isoformat() if dates else None,
        "imported": result["imported"],
    })
    if unresolved:
        result["errors"] = result["errors"] + [f"could not resolve on Yahoo: {', '.join(unresolved)}"]
    return {"phase": "committed", "source": wealthsimple.BROKER, **result, "recompute": recompute}
