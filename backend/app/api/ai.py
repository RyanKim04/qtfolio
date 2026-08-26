import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..ai.advisor import stream_analysis, stream_chat
from ..ai.context import DEFAULT_PROFILE, build_portfolio_context
from ..config import utcnow
from ..services import history

router = APIRouter()

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ChatBody(BaseModel):
    messages: list[dict]


@router.get("/ai/last")
async def last_analysis(request: Request):
    state = request.app.state
    saved = await history.get_setting(state.sessions, "ai_last_analysis", None)
    profile = await history.get_setting(state.sessions, "investor_profile", DEFAULT_PROFILE)
    return {"analysis": saved, "profile": profile}


@router.post("/ai/analyze")
async def analyze(request: Request):
    state = request.app.state
    profile = await history.get_setting(state.sessions, "investor_profile", DEFAULT_PROFILE)
    context = await build_portfolio_context(state)

    async def gen():
        async for chunk in stream_analysis(state, profile, context):
            # persist the finished report so the dashboard shows it on next load
            if chunk.startswith("data: "):
                try:
                    payload = json.loads(chunk[6:])
                    if payload.get("type") == "done" and payload.get("full_text"):
                        await history.set_setting(state.sessions, "ai_last_analysis", {
                            "text": payload["full_text"],
                            "created_at": utcnow().isoformat(timespec="seconds") + "Z",
                            "model": payload.get("model"),
                        })
                except json.JSONDecodeError:
                    pass
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/ai/chat")
async def chat(request: Request, body: ChatBody):
    state = request.app.state
    profile = await history.get_setting(state.sessions, "investor_profile", DEFAULT_PROFILE)
    context = await build_portfolio_context(state)
    saved = await history.get_setting(state.sessions, "ai_last_analysis", None)
    last_text = saved.get("text") if isinstance(saved, dict) else None
    return StreamingResponse(
        stream_chat(state, profile, context, last_text, body.messages),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
