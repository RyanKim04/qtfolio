from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..services import wealthsimple
from ..services.importer import import_ws_export

router = APIRouter()


@router.post("/import/activities")
async def import_activities(request: Request, file: UploadFile = File(...), commit: bool = Form(False)):
    """Two-phase (§4.3): default parses and returns the preview + row-mapping
    report; commit=true maps rows into `activities` (same multiset dedup) and
    triggers a recompute. Accepts the Wealthsimple activities export."""
    content = await file.read()
    if not wealthsimple.is_ws_export(content):
        raise HTTPException(400, "not a Wealthsimple activities export (CSV with effective_date/activity_type columns)")
    return await import_ws_export(request.app.state, content, commit=commit, source=file.filename)
