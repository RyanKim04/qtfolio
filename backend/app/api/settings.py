import json
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from ..models import Account, Setting
from ..questrade.tokens import TokenError

router = APIRouter()


class TokenPaste(BaseModel):
    refresh_token: str


class AccountTracking(BaseModel):
    qt_account_id: str
    tracked: bool


async def _accounts_payload(state) -> list[dict]:
    async with state.sessions() as session:
        rows = (await session.execute(select(Account))).scalars().all()
    return [
        {"qt_account_id": a.qt_account_id, "type": a.type, "status": a.status, "tracked": a.tracked, "broker": a.broker}
        for a in rows
    ]


async def _sync_accounts_from_qt(state) -> list[dict]:
    """Upsert the account list from Questrade; new accounts default to tracked (§4.2)."""
    fetched = await state.qt.accounts()
    async with state.sessions() as session:
        for acc in fetched:
            acc_id = str(acc.get("number"))
            row = await session.get(Account, acc_id)
            if row is None:
                row = Account(qt_account_id=acc_id, tracked=True)
                session.add(row)
            row.type = acc.get("type")
            row.status = acc.get("status")
        await session.commit()
    return await _accounts_payload(state)


@router.get("/settings")
async def get_settings(request: Request):
    state = request.app.state
    async with state.sessions() as session:
        rows = (await session.execute(select(Setting))).scalars().all()
    values = {}
    for r in rows:
        try:
            values[r.key] = json.loads(r.value_json)
        except json.JSONDecodeError:
            values[r.key] = r.value_json
    return {"settings": values, "accounts": await _accounts_payload(state)}


@router.put("/settings")
async def put_settings(request: Request, body: dict):
    state = request.app.state
    async with state.sessions() as session:
        for key, value in body.items():
            row = await session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value_json=json.dumps(value)))
            else:
                row.value_json = json.dumps(value)
        await session.commit()
    return {"ok": True}


@router.put("/settings/accounts")
async def put_accounts(request: Request, body: list[AccountTracking]):
    state = request.app.state
    async with state.sessions() as session:
        for item in body:
            row = await session.get(Account, item.qt_account_id)
            if row is None:
                raise HTTPException(404, f"unknown account {item.qt_account_id}")
            row.tracked = item.tracked
        await session.commit()
    return {"accounts": await _accounts_payload(state)}


@router.post("/settings/qt-token")
async def paste_token(request: Request, body: TokenPaste):
    state = request.app.state
    if not body.refresh_token.strip():
        raise HTTPException(400, "refresh token is empty")
    try:
        await state.tokens.set_refresh_token(body.refresh_token)
    except TokenError as exc:
        raise HTTPException(400, str(exc))
    accounts = await _sync_accounts_from_qt(state)
    return {"ok": True, "token": await state.tokens.status(), "accounts": accounts}


@router.post("/settings/qt-retire")
async def retire_questrade(request: Request):
    """Freeze Questrade after moving brokers (closing snapshot → jobs stand down)."""
    from ..services import history

    state = request.app.state
    result = await history.retire_questrade(state.sessions, state.qt)
    recompute = await history.recompute(state.sessions, state.yf)
    return {"ok": True, "retired": result, "recompute": recompute}


@router.post("/settings/qt-test")
async def test_connection(request: Request):
    state = request.app.state
    t0 = time.monotonic()
    try:
        accounts = await _sync_accounts_from_qt(state)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "latency_ms": round((time.monotonic() - t0) * 1000), "accounts": accounts}
