"""Candle adjustment-regime check — SPEC.md §6.0. One empirical question: are
Questrade daily candles back-adjusted across splits, or unadjusted? Fetch
candles for a ticker with a known split; if the close series gaps by ~the split
factor at the split date, candles are unadjusted."""

import logging
import math
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Symbol
from ..questrade.client import QuestradeClient
from ..yf import YfService, to_yf_symbol
from .history import set_setting
from .reconstruction import BACK_ADJUSTED, UNADJUSTED

log = logging.getLogger(__name__)

FALLBACK_TICKERS = ["TQQQ", "SOXL"]  # both have splits (§6.0)


async def _candidates(sessions: async_sessionmaker, qt: QuestradeClient, yf: YfService):
    async with sessions() as session:
        rows = list((await session.execute(select(Symbol).where(Symbol.qt_symbol_id.is_not(None)))).scalars())
    for s in rows:
        yield s.ticker, s.qt_symbol_id, to_yf_symbol(s.ticker, s.exchange)
    for t in FALLBACK_TICKERS:
        try:
            matches = await qt.symbol_search(t)
            info = next((m for m in matches if m.get("symbol") == t), None)
            if info:
                yield t, info["symbolId"], t
        except Exception as exc:
            log.warning("symbol search for regime fallback %s failed: %s", t, exc)


async def detect_regime(sessions: async_sessionmaker, qt: QuestradeClient, yf: YfService) -> str:
    async for ticker, symbol_id, yf_sym in _candidates(sessions, qt, yf):
        splits = await yf.splits(yf_sym)
        # Newest split first: most likely inside Questrade's candle history.
        for split_date in sorted(splits, reverse=True):
            factor = splits[split_date]
            if not factor or factor == 1 or split_date > date.today() - timedelta(days=7):
                continue
            regime = await _probe(qt, symbol_id, split_date, factor)
            if regime:
                log.info("adjustment regime detected via %s split %s (×%g): %s", ticker, split_date, factor, regime)
                await set_setting(sessions, "candle_adjustment_regime", regime)
                await set_setting(
                    sessions,
                    "candle_adjustment_probe",
                    {"ticker": ticker, "split_date": split_date.isoformat(), "factor": factor, "regime": regime},
                )
                return regime
    log.warning("could not detect adjustment regime empirically; defaulting to back_adjusted")
    await set_setting(sessions, "candle_adjustment_regime", BACK_ADJUSTED)
    await set_setting(sessions, "candle_adjustment_probe", {"regime": "defaulted"})
    return BACK_ADJUSTED


async def _probe(qt: QuestradeClient, symbol_id: int, split_date: date, factor: float) -> str | None:
    start = datetime.combine(split_date - timedelta(days=21), time.min, tzinfo=timezone.utc)
    end = datetime.combine(split_date + timedelta(days=21), time.min, tzinfo=timezone.utc)
    try:
        bars = await qt.candles(symbol_id, start, end, "OneDay")
    except Exception as exc:
        log.warning("regime probe candles failed: %s", exc)
        return None
    dated = sorted(
        (datetime.fromisoformat(b["start"]).date(), b.get("close")) for b in bars if b.get("close")
    )
    before = [c for d, c in dated if d < split_date]
    after = [c for d, c in dated if d >= split_date]
    if not before or not after:
        return None
    ratio = before[-1] / after[0]
    # Unadjusted candles gap by ~factor at the split; back-adjusted stay ~1.
    dist_factor = abs(math.log(ratio / factor))
    dist_one = abs(math.log(ratio))
    return UNADJUSTED if dist_factor < dist_one else BACK_ADJUSTED
