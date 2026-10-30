"""TFSA contribution-room tracker (services/tfsa.py). Recording mirrors the
activities import: commit=false previews the warnings, commit=true writes."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import utcnow
from ..models import TfsaTransaction
from ..services import history, tfsa

router = APIRouter()


def _cents(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _dollars(cents: int | None) -> float | None:
    return None if cents is None else cents / 100


class TxnIn(BaseModel):
    date: date
    institution: str = Field(min_length=1)
    type: str = Field(pattern="^(contribution|withdrawal|transfer)$")
    amount: Decimal = Field(gt=0)
    note: str | None = None
    commit: bool = False


class AnchorIn(BaseModel):
    year: int
    room: Decimal


class ConfigIn(BaseModel):
    limits: dict[int, Decimal | None]
    anchor: AnchorIn | None = None


def _config_payload(limits: dict[int, int | None], anchor: tuple[int, int] | None) -> dict:
    return {
        "limits": {str(y): _dollars(c) for y, c in sorted(limits.items())},
        "anchor": None if anchor is None else {"year": anchor[0], "room": anchor[1] / 100},
    }


@router.get("/tfsa")
async def get_tfsa(request: Request):
    sessions = request.app.state.sessions
    limits, anchor = await tfsa.load_limits(sessions), await tfsa.load_anchor(sessions)
    rows = await tfsa.load_rows(sessions)
    today = tfsa.today_local()
    out = {
        "today": today.isoformat(),
        **_config_payload(limits, anchor),
        "transactions": [
            {"id": r.id, "date": r.date.isoformat(), "institution": r.institution, "type": r.type,
             "amount": r.amount_cad_cents / 100, "note": r.note}
            for r in sorted(rows, key=lambda r: (r.date, r.id), reverse=True)
        ],
    }
    if anchor is None or anchor[0] > today.year:
        return {**out, "configured": False}
    s = tfsa.summarize(today, anchor[0], anchor[1], limits, [tfsa.to_txn(r) for r in rows])
    over, reconcile = s["overcontribution"], s["reconcile"]
    return {
        **out,
        "configured": True,
        "year": s["year"],
        "jan1": _dollars(s["jan1_cents"]),
        "contributed": _dollars(s["contributed_cents"]),
        "withdrawn": _dollars(s["withdrawn_cents"]),
        "remaining": _dollars(s["remaining_cents"]),
        "next_year": s["next_year"],
        "next_limit": _dollars(s["next_limit_cents"]),
        "next_limit_provisional": s["next_limit_provisional"],
        "projected_next_jan1": _dollars(s["projected_next_jan1_cents"]),
        "overcontribution": None if over is None else {
            "excess": _dollars(over["excess_cents"]), "monthly_tax": _dollars(over["monthly_tax_cents"]),
            "since": over["since"].isoformat() if over["since"] else None,
        },
        "reconcile": None if reconcile is None else {
            "year": reconcile["year"], "computed_jan1": _dollars(reconcile["computed_jan1_cents"]),
        },
        "years": [
            {"year": r.year, "limit": _dollars(r.limit_cents), "limit_provisional": r.limit_provisional,
             "jan1": _dollars(r.jan1_cents), "contributed": _dollars(r.contributed_cents),
             "withdrawn": _dollars(r.withdrawn_cents), "end": _dollars(r.end_cents),
             "first_negative": r.first_negative.isoformat() if r.first_negative else None}
            for r in s["years"]
        ],
    }


@router.post("/tfsa/transactions")
async def record_transaction(request: Request, body: TxnIn):
    sessions = request.app.state.sessions
    anchor = await tfsa.load_anchor(sessions)
    if anchor is None:
        raise HTTPException(409, "Set the CRA Jan 1 anchor in Settings first")
    rows = await tfsa.load_rows(sessions)
    candidate = tfsa.Txn(body.date, body.type, _cents(body.amount))
    result = tfsa.check(candidate, anchor[0], anchor[1], await tfsa.load_limits(sessions),
                        [tfsa.to_txn(r) for r in rows], tfsa.today_local())
    payload = {"year": result["year"], "room_after": _dollars(result["room_after_cents"]),
               "warnings": result["warnings"]}
    if not body.commit:
        return {"phase": "preview", **payload}
    async with sessions() as session:
        row = TfsaTransaction(date=body.date, institution=body.institution.strip(), type=body.type,
                              amount_cad_cents=candidate.amount_cents, note=(body.note or "").strip() or None,
                              created_at=utcnow())
        session.add(row)
        await session.commit()
        new_id = row.id
    return {"phase": "committed", "id": new_id, **payload}


@router.delete("/tfsa/transactions/{txn_id}")
async def delete_transaction(request: Request, txn_id: int):
    async with request.app.state.sessions() as session:
        row = await session.get(TfsaTransaction, txn_id)
        if row is None:
            raise HTTPException(404, "No such TFSA transaction")
        await session.delete(row)
        await session.commit()
    return {"ok": True}


@router.put("/tfsa/config")
async def put_config(request: Request, body: ConfigIn):
    sessions = request.app.state.sessions
    if not any(v is not None for v in body.limits.values()):
        raise HTTPException(400, "At least one annual limit is required")
    if any(not 2009 <= y <= 2100 for y in body.limits) or any(v is not None and v < 0 for v in body.limits.values()):
        raise HTTPException(400, "Limits need years 2009–2100 and non-negative amounts")
    limits = {y: (_cents(v) if v is not None else None) for y, v in sorted(body.limits.items())}
    await history.set_setting(sessions, tfsa.LIMITS_KEY, {str(y): c for y, c in limits.items()})
    if body.anchor is not None:
        if body.anchor.year > tfsa.today_local().year:
            raise HTTPException(400, "The anchor is CRA's Jan 1 room for a year that has started")
        await history.set_setting(sessions, tfsa.ANCHOR_KEY,
                                  {"year": body.anchor.year, "room_cents": _cents(body.anchor.room)})
    return _config_payload(limits, await tfsa.load_anchor(sessions))
