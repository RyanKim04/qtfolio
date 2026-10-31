"""Risk tab and the Dashboard's risk row. Heavy work runs in a worker thread
inside services/risk/service.py and is cached until the book or the day changes.
The POST routes only compute; nothing is stored."""

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..services.risk.metrics import drawdowns
from ..services.risk.service import WINDOWS
from .performance import _series_maps

router = APIRouter()

EMPTY = {"empty": True, "reason": "no holdings yet; import a Wealthsimple activities export in Settings"}


def _risk(request: Request):
    return request.app.state.risk


@router.get("/risk/summary")
async def risk_summary(request: Request):
    out = await _risk(request).summary()
    if out is None:
        return EMPTY
    m = await _series_maps(request.app.state)
    dd = None
    if m is not None:
        idx = np.array([m["idx"][d] for d in m["dates"]])
        dd = float(drawdowns(idx)[-1])
    return {"empty": False, **out, "drawdown": dd}


@router.get("/risk/covariance")
async def risk_covariance(request: Request, method: str = Query("lw", pattern="^(sample|lw|ewma|factor)$"),
                          window: str = Query("1y")):
    if window not in WINDOWS:
        raise HTTPException(400, f"window must be one of {tuple(WINDOWS)}")
    out = await _risk(request).covariance(method, window)
    return EMPTY if out is None else {"empty": False, **out}


@router.get("/risk/var")
async def risk_var(request: Request, conf: float = Query(0.95, ge=0.9, le=0.995), horizon: int = Query(1, ge=1, le=21)):
    out = await _risk(request).var(conf, horizon)
    return EMPTY if out is None else {"empty": False, **out}


@router.get("/risk/factors")
async def risk_factors(request: Request, window: str = Query("1y")):
    if window not in WINDOWS:
        raise HTTPException(400, f"window must be one of {tuple(WINDOWS)}")
    out = await _risk(request).factors(window)
    return EMPTY if out is None else {"empty": False, **out}


@router.get("/risk/stress")
async def risk_stress(request: Request):
    out = await _risk(request).stress()
    return EMPTY if out is None else {"empty": False, **out}


class ShockBody(BaseModel):
    shocks: dict[str, float]


@router.post("/risk/stress/custom")
async def risk_shock(request: Request, body: ShockBody):
    shocks = {k: v for k, v in body.shocks.items() if v}
    if not shocks:
        raise HTTPException(400, "set at least one driver shock")
    try:
        out = await _risk(request).shock(shocks)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return EMPTY if out is None else {"empty": False, **out}


class ReverseBody(BaseModel):
    loss: float  # fraction, 0.2 = a 20% loss
    horizon: int = 21


@router.post("/risk/stress/reverse")
async def risk_reverse(request: Request, body: ReverseBody):
    if not 0 < body.loss < 1 or not 1 <= body.horizon <= 252:
        raise HTTPException(400, "loss must be between 0 and 1, horizon between 1 and 252 sessions")
    out = await _risk(request).reverse(body.loss, body.horizon)
    return EMPTY if out is None else {"empty": False, **out}


@router.get("/risk/causal/graph")
async def risk_causal_graph(request: Request):
    out = await _risk(request).causal_graph()
    return EMPTY if out is None else {"empty": False, **out}


@router.get("/risk/causal/irf")
async def risk_causal_irf(request: Request, driver: str = Query(...), horizon: int = Query(20, ge=1, le=60)):
    try:
        out = await _risk(request).irf(driver, horizon)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return EMPTY if out is None else {"empty": False, **out}
