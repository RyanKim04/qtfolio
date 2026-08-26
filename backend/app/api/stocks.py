from fastapi import APIRouter, HTTPException, Query, Request

from ..services import history
from ..services.stocks import QT_INTERVAL

router = APIRouter()


@router.get("/stocks/{ticker}/candles")
async def candles(request: Request, ticker: str, interval: str = Query("1d")):
    if interval not in QT_INTERVAL:
        raise HTTPException(400, f"interval must be one of {sorted(QT_INTERVAL)}")
    payload = await request.app.state.stocks.candles_payload(ticker, interval)
    if payload is None:
        raise HTTPException(404, f"unknown symbol {ticker!r}")
    return payload


@router.get("/stocks/{ticker}/levels")
async def levels(request: Request, ticker: str):
    payload = await request.app.state.stocks.levels(ticker)
    if payload is None:
        raise HTTPException(404, f"unknown symbol {ticker!r}")
    return payload


@router.get("/stocks/{ticker}/position")
async def position(request: Request, ticker: str):
    ids = await history.tracked_ids(request.app.state.sessions)
    return await request.app.state.stocks.position(ticker, ids)


@router.get("/stocks/{ticker}/rs")
async def rs(request: Request, ticker: str, benchmark: str = Query("QQQ")):
    return await request.app.state.stocks.rs_series(ticker, benchmark)
