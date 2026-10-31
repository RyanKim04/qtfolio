"""Cached daily closes for valuation (bars arrive via YfService.refresh_bars)."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Candle


async def daily_closes_cents(sessions: async_sessionmaker, ticker: str) -> dict[date, int]:
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Candle.ts, Candle.c)
                .where(Candle.ticker == ticker, Candle.interval == "OneDay", Candle.c.is_not(None))
                .order_by(Candle.ts)
            )
        ).all()
    return {ts.date(): round(c * 100) for ts, c in rows}
