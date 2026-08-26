from fastapi import APIRouter, File, Form, Request, UploadFile

from ..services import history
from ..services.activities import rehash_all
from ..services.importer import commit_import, parse_export, preview_report

router = APIRouter()


@router.post("/import/activities")
async def import_activities(request: Request, file: UploadFile = File(...), commit: bool = Form(False)):
    """Two-phase (§4.3): default parses and returns the preview + row-mapping
    report; commit=true maps rows into `activities` (same multiset dedup) and
    triggers a recompute."""
    state = request.app.state
    content = await file.read()
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
        return {"phase": "preview", **preview_report(parsed)}
    await history.repair_missing_tickers(state.sessions, state.qt)
    # Unconditional: stored hashes must reflect the CURRENT tuple rule (trade-date
    # basis, repaired tickers) or cross-source dedup silently fails.
    await rehash_all(state.sessions)
    result = await commit_import(state.sessions, parsed)
    await history.repair_missing_tickers(state.sessions, state.qt)
    # New tickers need registry entries + candle coverage before valuation.
    await history.refresh_reference_data(state.sessions, state.qt)
    recompute = await history.recompute(state.sessions, state.yf)
    return {"phase": "committed", **result, "recompute": recompute}
