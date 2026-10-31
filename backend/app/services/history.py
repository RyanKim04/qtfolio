"""Orchestration: activities + frozen Questrade snapshots → reconstruction →
valuation → series_daily.

recompute() reads only the DB (plus cached yfinance splits), so it reruns
cheaply after every CSV import. Network work (daily bars, FX) lives in
the EOD job and the import path.
"""

import json
import logging
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import (
    Account,
    Activity,
    BalanceSnapshot,
    Candle,
    CashLedgerRow,
    FxRate,
    HoldingsLedgerRow,
    PositionSnapshot,
    SeriesDaily,
    Setting,
    Symbol,
)
from ..yf import YF_INTERVAL, YfService, to_yf_symbol
from . import candles as candle_svc
from .activities import load_activities
from .reconstruction import BACK_ADJUSTED, LedgerState, add_states, match_transfers, walk_backward
from .series import (
    apply_ground_truth,
    conversion_cost_series,
    flows_to_cad,
    fx_translation_series,
    twr_index,
    value_series,
)

log = logging.getLogger(__name__)

DEFAULT_BENCHMARKS = ["QQQ", "SMH", "SPY", "XEQT.TO"]
QUESTRADE = "questrade"


async def tracked_ids(sessions: async_sessionmaker) -> list[str]:
    async with sessions() as session:
        return list((await session.execute(select(Account.qt_account_id).where(Account.tracked))).scalars())


async def first_activity_date(sessions: async_sessionmaker) -> date | None:
    async with sessions() as session:
        return (await session.execute(select(func.min(Activity.transaction_date)))).scalar_one_or_none()


async def tracked_accounts(sessions: async_sessionmaker) -> list[Account]:
    async with sessions() as session:
        return list((await session.execute(select(Account).where(Account.tracked))).scalars())


async def get_setting(sessions: async_sessionmaker, key: str, default=None):
    async with sessions() as session:
        row = await session.get(Setting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value_json)
    except json.JSONDecodeError:
        return row.value_json


async def set_setting(sessions: async_sessionmaker, key: str, value) -> None:
    async with sessions() as session:
        row = await session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value_json=json.dumps(value)))
        else:
            row.value_json = json.dumps(value)
        await session.commit()


async def _fx_micro(sessions: async_sessionmaker) -> dict[date, int]:
    async with sessions() as session:
        rows = (await session.execute(select(FxRate))).scalars().all()
    return {r.date: r.usdcad_micro for r in rows}


async def snapshot_dates(sessions: async_sessionmaker, account_ids: list[str], limit: int = 12) -> list:
    async with sessions() as session:
        return list((await session.execute(
            select(BalanceSnapshot.date.distinct())
            .where(BalanceSnapshot.account_id.in_(account_ids))
            .order_by(BalanceSnapshot.date.desc())
            .limit(limit)
        )).scalars())


async def anchor_state(sessions: async_sessionmaker, account_ids: list[str], at: date | None = None):
    """Snapshot-based reconstruction anchor at `at` (default: newest snapshot).
    Returns (date, qty_micro, cash_cents) or None."""
    async with sessions() as session:
        if at is not None:
            anchor_date = at
        else:
            anchor_date = (
                await session.execute(
                    select(BalanceSnapshot.date)
                    .where(BalanceSnapshot.account_id.in_(account_ids))
                    .order_by(BalanceSnapshot.date.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if anchor_date is None:
            return None
        qty: dict[str, int] = {}
        for p in (
            await session.execute(
                select(PositionSnapshot).where(
                    PositionSnapshot.date == anchor_date, PositionSnapshot.account_id.in_(account_ids)
                )
            )
        ).scalars():
            qty[p.ticker] = qty.get(p.ticker, 0) + p.qty_micro
        cash: dict[str, int] = {}
        for b in (
            await session.execute(
                select(BalanceSnapshot).where(
                    BalanceSnapshot.date == anchor_date, BalanceSnapshot.account_id.in_(account_ids)
                )
            )
        ).scalars():
            cash[b.currency] = cash.get(b.currency, 0) + (b.cash_cents or 0)
    return anchor_date, qty, cash


async def ground_truth_cad(sessions: async_sessionmaker, account_ids: list[str], fx_micro: dict[date, int]) -> dict[date, int]:
    from .series import _FFill

    fx = _FFill(fx_micro)
    async with sessions() as session:
        rows = (
            await session.execute(select(BalanceSnapshot).where(BalanceSnapshot.account_id.in_(account_ids)))
        ).scalars().all()
    out: dict[date, int] = {}
    for b in rows:
        te = b.total_equity_cents or 0
        if b.currency == "USD":
            rate = fx.at(b.date, flat_backfill=True) or 1_000_000
            te = te * rate // 1_000_000
        elif b.currency != "CAD":
            continue
        out[b.date] = out.get(b.date, 0) + te
    return out


def activity_dicts(rows: list[Activity]) -> list[dict]:
    return [
        {
            "id": a.id, "account_id": a.account_id, "transaction_date": a.transaction_date,
            "type": a.type, "action": a.action, "ticker": a.ticker,
            "qty_micro": a.qty_micro, "net_amount_cents": a.net_amount_cents, "currency": a.currency,
        }
        for a in rows
    ]


async def recompute(sessions: async_sessionmaker, yf: YfService) -> dict:
    accounts = await tracked_accounts(sessions)
    if not accounts:
        return {"ok": False, "reason": "no tracked accounts"}
    ids = [a.qt_account_id for a in accounts]
    # Snapshot accounts (Questrade) anchor on real balances; ledger accounts
    # (Wealthsimple CSV) have no balances API, so their full-life activity history
    # is their state (LedgerState). The anchor at any date is the sum of both.
    snap_ids = [a.qt_account_id for a in accounts if a.broker == QUESTRADE]
    ledger_ids = [a.qt_account_id for a in accounts if a.broker != QUESTRADE]
    candidates = await snapshot_dates(sessions, snap_ids) if snap_ids else []
    if not candidates and not ledger_ids:
        return {"ok": False, "reason": "no history yet; import a Wealthsimple activities export"}

    acts, transfer_warnings = match_transfers(activity_dicts(await load_activities(sessions, ids + [""])))
    # DECISION: valuation closes now come from yfinance (split-adjusted), so the
    # back-adjusted regime always applies. The stored Questrade probe result no
    # longer describes the candles in use.
    regime = BACK_ADJUSTED

    # Questrade (retired 2026-10-27) contributes only its frozen snapshots; the
    # series runs to today on the ledger accounts.
    last_act = max((a["transaction_date"] for a in acts if a.get("transaction_date")), default=date.today())
    series_end = max([date.today(), last_act] + candidates[:1])

    newest_anchor = await anchor_state(sessions, snap_ids, candidates[0]) if candidates else None
    tickers = sorted({a["ticker"] for a in acts if a["ticker"]} | set(newest_anchor[1] if newest_anchor else {}))
    async with sessions() as session:
        sym_rows = {s.ticker: s for s in (await session.execute(select(Symbol).where(Symbol.ticker.in_(tickers)))).scalars()}
    sym_currency = {t: (sym_rows[t].currency if t in sym_rows and sym_rows[t].currency else "CAD") for t in tickers}

    splits: dict[str, dict] = {}
    for t in tickers:
        yf_sym = to_yf_symbol(t, sym_rows[t].exchange if t in sym_rows else None)
        s = await yf.splits(yf_sym)
        if s:
            splits[t] = s

    # Anchor selection: the newest snapshot can contradict recorded history when
    # the broker hasn't posted this week's trades yet (posting lag). The walk
    # then produces negative quantities all the way back, which corrupts
    # capital-in and attribution. Anchor at the newest snapshot whose walk has no
    # negative quantities, carry the ledger forward from there, and pin the totals
    # to every real snapshot in between. This corrects itself once the rows post.
    from .reconstruction import extend_forward, has_negative_qty

    ledger = LedgerState([a for a in acts if a["account_id"] in ledger_ids], series_end, splits)
    recon = None
    if candidates:
        anchor_date = candidates[0]
        fallback = None
        for cand in candidates:
            state = await anchor_state(sessions, snap_ids, cand)
            if state is None:
                continue
            q, c = add_states((state[1], state[2]), ledger.at(cand))
            r = walk_backward(q, c, cand, acts, regime, splits)
            if fallback is None:
                fallback = (cand, r)
            if not has_negative_qty(r):
                recon, anchor_date = r, cand
                break
        if recon is None:
            anchor_date, recon = fallback
    else:
        anchor_date = series_end
        q, c = ledger.at(series_end)
        recon = walk_backward(q, c, anchor_date, acts, regime, splits)

    if anchor_date < series_end:
        extend_forward(recon, acts, anchor_date, series_end)
        recon.warnings = [w for w in recon.warnings if "post-date the anchor" not in w["message"]]
    if candidates and anchor_date < candidates[0]:
        recon.warnings.append({
            "date": anchor_date.isoformat(), "ticker": None,
            "message": f"the newest snapshot disagrees with recorded activities (likely trades not yet posted "
                       f"by Questrade). Anchored at {anchor_date} instead; later holdings are estimated and pinned "
                       f"to real snapshots until the trades post",
        })

    closes = {t: await candle_svc.daily_closes_cents(sessions, t) for t in tickers}
    fx_micro = await _fx_micro(sessions)
    values, val_warnings = value_series(recon.daily_qty, recon.daily_cash, closes, fx_micro, sym_currency)
    gt = await ground_truth_cad(sessions, snap_ids, fx_micro) if snap_ids else {}
    if ledger_ids and gt:
        # A Questrade snapshot only covers Questrade; on dates a ledger account
        # also held assets, the true total adds that account's exact ledger value
        # (otherwise the pin would erase it, e.g. 2026-10-07 when both brokers were funded).
        gt_states = {d: ledger.at(d) for d in gt}
        ledger_vals, _ = value_series(
            {d: s[0] for d, s in gt_states.items()}, {d: s[1] for d, s in gt_states.items()},
            closes, fx_micro, sym_currency,
        )
        gt = {d: v + ledger_vals.get(d, 0) for d, v in gt.items()}
    scaled = apply_ground_truth(values, gt)
    flows_cad = flows_to_cad(recon.flows, fx_micro)
    idx, twr_warnings = twr_index(scaled, flows_cad)

    dates = sorted(scaled)
    # Capital-in (net external deposits) must come from external flows whenever
    # our records reach the account's inception. "Reaches inception" is a question
    # of data coverage, decided from the raw activity timeline, not from whether
    # the backward walk zeroed out. Split/FX reconstruction noise (e.g. a
    # leveraged-ETF split journal) can leave a phantom opening_qty/opening_cash even
    # when we demonstrably have the full history; seeding capital from the day-one
    # value in that case silently adds the day-one market move and snapshot seam
    # factor to "net deposits". The old heuristic (`not opening_qty and
    # residual < $100`) flipped to False on any such residual, so capital regressed
    # every time the anchor was forced off "today", which unposted trades or a
    # fresh API key re-trigger. Learned 2026-09-21: capital read 18,421 vs the true
    # 19,000 because a phantom SOXL split residual + $681 opening cash tripped the check.
    # The account began empty iff no activity predates the first external flow.
    flow_dates = sorted(recon.flows)
    earliest_act = min((a["transaction_date"] for a in acts if a.get("transaction_date")), default=None)
    inception_covered = bool(flow_dates and earliest_act and earliest_act >= flow_dates[0])
    # FX share of P&L: USD/CAD moves on USD exposure, plus the broker's spread on
    # every conversion (otherwise the spread reads as an investment loss).
    fx_gain = fx_translation_series(recon.daily_qty, recon.daily_cash, closes, fx_micro, sym_currency)
    conv_cost = conversion_cost_series(acts, fx_micro, dates)
    residual_cents = sum(abs(c) for c in recon.opening_cash.values())
    if inception_covered and (recon.opening_qty or residual_cents >= 1):
        bits = []
        cash_bits = ", ".join(f"{cur} {cents / 100:+.2f}" for cur, cents in recon.opening_cash.items() if cents)
        if cash_bits:
            bits.append(cash_bits)
        qty_bits = ", ".join(f"{t} {q / 1_000_000:+.4f} sh" for t, q in recon.opening_qty.items() if q)
        if qty_bits:
            bits.append(qty_bits)
        recon.warnings.append({
            "date": dates[0].isoformat() if dates else None,
            "ticker": None,
            "message": f"opening residual ({'; '.join(bits)}) from split/FX rounding or missing small "
                       "income/fee rows. Counted as investment P&L, not as deposits, so capital-in still "
                       "equals external flows",
        })
    async with sessions() as session:
        await session.execute(delete(SeriesDaily))
        await session.execute(delete(HoldingsLedgerRow))
        await session.execute(delete(CashLedgerRow))
        if not dates:
            capital = 0
        elif inception_covered:
            capital = flows_cad.get(dates[0], 0)
        else:
            capital = scaled[dates[0]]
        for d in dates:
            if d != dates[0]:
                capital += flows_cad.get(d, 0)
            session.add(SeriesDaily(
                date=d,
                portfolio_value_cad_cents=scaled[d],
                capital_in_cad_cents=capital,
                pnl_cad_cents=scaled[d] - capital,
                twr_index=idx.get(d),
                flows_cad_cents=flows_cad.get(d, 0),
                fx_gain_cad_cents=fx_gain.get(d, 0) + conv_cost.get(d, 0),
            ))
        for d, per_ticker in recon.daily_qty.items():
            for t, q in per_ticker.items():
                session.add(HoldingsLedgerRow(date=d, ticker=t, qty_micro=q))
        for d, per_cur in recon.daily_cash.items():
            for cur, cents in per_cur.items():
                session.add(CashLedgerRow(date=d, currency=cur, balance_cents=cents))
        await session.commit()

    warnings = recon.warnings + transfer_warnings + val_warnings + twr_warnings
    await set_setting(sessions, "reconciliation_warnings", warnings[:100])
    await set_setting(sessions, "flow_events", recon.flow_events)
    log.info("series recomputed: %d days (%s..%s), %d warnings", len(dates),
             dates[0] if dates else "-", dates[-1] if dates else "-", len(warnings))
    return {"ok": True, "days": len(dates), "start": dates[0].isoformat() if dates else None,
            "end": dates[-1].isoformat() if dates else None, "warnings": len(warnings)}


async def trading_days(sessions: async_sessionmaker) -> set[date]:
    """Market sessions = any date with a daily bar (held tickers or benchmarks,
    US or TSX). The per-day stats are measured on this calendar."""
    async with sessions() as session:
        rows = (await session.execute(
            select(func.distinct(func.date(Candle.ts))).where(Candle.interval.in_(("OneDay", YF_INTERVAL)))
        )).scalars().all()
    return {date.fromisoformat(r) for r in rows if r}


async def header_stats(sessions: async_sessionmaker, live_total_cad: float | None = None,
                       today: date | None = None) -> dict:
    """MWR since data start + TWR YTD for the dashboard header. With a
    live total, today is priced live, the same last point as the Performance
    charts (api/performance._series_maps)."""
    from .series import apply_live_point, range_stats

    async with sessions() as session:
        rows = list((await session.execute(select(SeriesDaily).order_by(SeriesDaily.date))).scalars())
    if len(rows) < 2:
        return {"mwr": None, "mwr_since": None, "twr_ytd": None}
    dates = [r.date for r in rows]
    values = {r.date: r.portfolio_value_cad_cents or 0 for r in rows}
    idx = {r.date: r.twr_index or 100.0 for r in rows}
    flows = {r.date: r.flows_cad_cents or 0 for r in rows}
    if live_total_cad is not None and today is not None and dates[-1] <= today:
        apply_live_point(dates, values, idx, flows, today, round(live_total_cad * 100))
    full = range_stats(dates, values, idx, flows)
    year_start = date(dates[-1].year, 1, 1)
    base_candidates = [d for d in dates if d < year_start]
    base = base_candidates[-1] if base_candidates else dates[0]
    twr_ytd = idx[dates[-1]] / idx[base] - 1 if idx.get(base) else None
    return {"mwr": full["mwr"], "mwr_since": dates[0].isoformat(), "twr_ytd": twr_ytd}


async def series_coverage(sessions: async_sessionmaker) -> dict | None:
    async with sessions() as session:
        rows = (
            await session.execute(select(SeriesDaily.date).order_by(SeriesDaily.date))
        ).scalars().all()
    if not rows:
        return None
    return {"start": rows[0].isoformat(), "end": rows[-1].isoformat(), "days": len(rows)}


async def first_activity_date(sessions: async_sessionmaker) -> date | None:
    async with sessions() as session:
        return (
            await session.execute(
                select(Activity.transaction_date)
                .where(Activity.transaction_date.is_not(None))
                .order_by(Activity.transaction_date)
                .limit(1)
            )
        ).scalar_one_or_none()


async def ensure_symbols_yf(sessions: async_sessionmaker, yf: YfService, tickers: list[str]) -> list[str]:
    """Registry rows (currency/exchange/description) for tickers Questrade never
    saw, resolved on Yahoo. Returns the tickers that could not be resolved."""
    async with sessions() as session:
        known = {s.ticker for s in (await session.execute(select(Symbol))).scalars() if s.currency}
    unresolved = []
    for t in sorted(set(tickers) - known):
        info = await yf.resolve(t)
        if info is None:
            log.warning("could not resolve %s on Yahoo; valuation will flag it", t)
            unresolved.append(t)
            continue
        async with sessions() as session:
            row = await session.get(Symbol, t)
            if row is None:
                row = Symbol(ticker=t)
                session.add(row)
            row.currency = info["currency"]
            row.exchange = info["exchange"]
            row.description = info["description"]
            await session.commit()
    return unresolved


async def held_tickers(sessions: async_sessionmaker) -> list[str]:
    """Tickers in the newest reconstructed holdings (empty before the first recompute)."""
    async with sessions() as session:
        last = (await session.execute(select(func.max(HoldingsLedgerRow.date)))).scalar_one_or_none()
        if last is None:
            return []
        return sorted(
            (await session.execute(select(HoldingsLedgerRow.ticker).where(HoldingsLedgerRow.date == last))).scalars()
        )


async def refresh_market_data(sessions: async_sessionmaker, yf: YfService, tickers: list[str] | None = None) -> None:
    """yfinance daily bars (split-adjusted) from the first activity date for the
    given tickers, or all of the ledger's tickers by default. Replaces Questrade
    candles. The full range is re-fetched so a split re-adjusts all of history."""
    start = await first_activity_date(sessions) or date.today()
    async with sessions() as session:
        if tickers is None:
            tickers = sorted({t for (t,) in (await session.execute(select(Activity.ticker).distinct())) if t})
        sym_rows = {s.ticker: s for s in (await session.execute(select(Symbol).where(Symbol.ticker.in_(tickers)))).scalars()}
    for t in tickers:
        s = sym_rows.get(t)
        await yf.refresh_bars(t, to_yf_symbol(t, s.exchange if s else None), "OneDay", start)
