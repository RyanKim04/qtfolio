import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from ..models import Account, Setting

router = APIRouter()


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
