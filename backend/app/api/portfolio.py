from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()

Currency = Query("CAD", pattern="^(CAD|USD)$")


@router.get("/portfolio/summary")
async def summary(request: Request, currency: str = Currency):
    return await request.app.state.portfolio.summary(currency)


@router.get("/portfolio/holdings")
async def holdings(request: Request, currency: str = Currency):
    return await request.app.state.portfolio.holdings(currency)


@router.get("/portfolio/allocation")
async def allocation(request: Request, by: str = Query("position"), currency: str = Currency):
    if by not in ("position", "currency", "sector"):
        raise HTTPException(400, "by must be position | currency | sector")
    return await request.app.state.portfolio.allocation(by, currency)
