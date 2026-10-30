"""Orchestration: snapshots → reconstruction → valuation → series_daily.

recompute() is pure-DB (no Questrade calls) so it can rerun cheaply after every
activities sync or CSV import (§6.7). Network work (activities/candles/FX
backfill, snapshot taking) lives in the jobs and in full_bootstrap().
"""

import json
import logging
import re
from datetime import date

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import TZ

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
from ..questrade.client import QuestradeClient
from ..yf import YF_INTERVAL, YfService, to_yf_symbol
from . import candles as candle_svc
from .activities import backfill_account, load_activities
from .reconstruction import BACK_ADJUSTED, LedgerState, add_states, match_transfers, walk_backward
from .series import apply_ground_truth, flows_to_cad, fx_translation_series, twr_index, value_series

log = logging.getLogger(__name__)

DEFAULT_BENCHMARKS = ["QQQ", "SMH", "SPY"]
QUESTRADE = "questrade"


async def tracked_ids(sessions: async_sessionmaker) -> list[str]:
    async with sessions() as session:
        return list((await session.execute(select(Account.qt_account_id).where(Account.tracked))).scalars())


async def tracked_accounts(sessions: async_sessionmaker) -> list[Account]:
    async with sessions() as session:
        return list((await session.execute(select(Account).where(Account.tracked))).scalars())


async def qt_account_ids(sessions: async_sessionmaker) -> list[str]:
    """Tracked Questrade accounts — the only ids the Questrade API may be asked about."""
    return [a.qt_account_id for a in await tracked_accounts(sessions) if a.broker == QUESTRADE]


async def qt_retired(sessions: async_sessionmaker) -> bool:
    """Owner moved to Wealthsimple (2026-10-27): Questrade history is frozen and
    every Questrade job, token refresh and banner stands down."""
    return bool(await get_setting(sessions, "qt_retired", None))


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


async def take_snapshots(
    sessions: async_sessionmaker, qt: QuestradeClient, account_ids: list[str], force: bool = False
) -> date | None:
    """Daily ground-truth points (§6.8): balances + positions per account, native currency.

    Guard rails (the snapshot IS the day's close in the series, so protect it):
    - weekends: skip — a Sat/Sun-dated snapshot manufactures a phantom weekend step
    - after ~16:05 ET: write at most once — the first post-close snapshot (the 17:30
      job, or the user's first evening refresh) captures ≈the close; later evening
      refreshes must not overwrite it with drifting after-hours marks.
    `force=True` (bootstrap) bypasses both — it needs an anchor no matter what.
    """
    from datetime import datetime as _dt

    today = date.today()
    now_et = _dt.now(TZ)
    after_close = now_et.hour * 60 + now_et.minute >= 16 * 60 + 5
    if not force:
        if now_et.weekday() >= 5:
            log.info("snapshot skipped: weekend (series correctly ends at Friday's close)")
            return None
        meta = await get_setting(sessions, "snapshot_meta", None)
        if after_close and isinstance(meta, dict) and meta.get("date") == today.isoformat() and meta.get("after_close"):
            log.info("snapshot skipped: today's post-close snapshot already recorded")
            return None
    async with sessions() as session:
        sym_currency = {
            s.ticker: s.currency for s in (await session.execute(select(Symbol))).scalars()
        }
        for acc in account_ids:
            balances = await qt.balances(acc)
            for pcb in balances.get("perCurrencyBalances", []):
                cur = pcb.get("currency")
                if cur not in ("CAD", "USD"):
                    continue
                row = await session.get(BalanceSnapshot, (today, acc, cur))
                if row is None:
                    row = BalanceSnapshot(date=today, account_id=acc, currency=cur)
                    session.add(row)
                row.cash_cents = round((pcb.get("cash") or 0) * 100)
                row.market_value_cents = round((pcb.get("marketValue") or 0) * 100)
                row.total_equity_cents = round((pcb.get("totalEquity") or 0) * 100)
            positions = await qt.positions(acc)
            for p in positions:
                if not (p.get("openQuantity") or 0):
                    continue
                t = p["symbol"]
                row = await session.get(PositionSnapshot, (today, acc, t))
                if row is None:
                    row = PositionSnapshot(date=today, account_id=acc, ticker=t)
                    session.add(row)
                row.qty_micro = round(p["openQuantity"] * 1_000_000)
                row.price_micro = round((p.get("currentPrice") or 0) * 1_000_000) or None
                row.market_value_cents = round((p.get("currentMarketValue") or 0) * 100)
                row.currency = sym_currency.get(t)
        await session.commit()
    await set_setting(sessions, "snapshot_meta", {"date": today.isoformat(), "after_close": after_close})
    return today


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
    # (Wealthsimple CSV) have no balances API — their full-life activity history
    # IS their state (LedgerState). The anchor at any date is the sum of both.
    snap_ids = [a.qt_account_id for a in accounts if a.broker == QUESTRADE]
    ledger_ids = [a.qt_account_id for a in accounts if a.broker != QUESTRADE]
    candidates = await snapshot_dates(sessions, snap_ids) if snap_ids else []
    if not candidates and not ledger_ids:
        return {"ok": False, "reason": "no snapshot anchor yet — run bootstrap"}

    acts, transfer_warnings = match_transfers(activity_dicts(await load_activities(sessions, ids + [""])))
    # DECISION: valuation closes now come from yfinance (split-adjusted), so the
    # back-adjusted regime holds by construction — the stored Questrade probe
    # result no longer describes the candles in use.
    regime = BACK_ADJUSTED

    # A live Questrade snapshot ends the series each day; ledger accounts and a
    # retired Questrade don't, so the series runs to today.
    live_snapshots = bool(snap_ids) and not await qt_retired(sessions)
    last_act = max((a["transaction_date"] for a in acts if a.get("transaction_date")), default=date.today())
    if ledger_ids or not live_snapshots:
        series_end = max([date.today(), last_act] + candidates[:1])
    else:
        series_end = candidates[0]

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
    # the broker hasn't posted this week's trades yet (§4.2 posting lag) — the
    # walk then fabricates negative quantities all the way back, corrupting
    # capital-in and attribution. Anchor at the newest snapshot whose walk is
    # CLEAN; carry the ledger forward from there and let §6.6 pin the totals to
    # every real snapshot in between. Self-heals once the rows post.
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
            "message": f"today's snapshot disagrees with recorded activities (likely trades not yet posted "
                       f"by Questrade) — anchored at {anchor_date}, holdings after that date approximated and "
                       f"pinned to real snapshots; corrects itself when the trades post",
        })

    closes = {t: await candle_svc.daily_closes_cents(sessions, t) for t in tickers}
    fx_micro = await _fx_micro(sessions)
    values, val_warnings = value_series(recon.daily_qty, recon.daily_cash, closes, fx_micro, sym_currency)
    gt = await ground_truth_cad(sessions, snap_ids, fx_micro) if snap_ids else {}
    if ledger_ids and gt:
        # A Questrade snapshot only covers Questrade; on dates a ledger account
        # also held assets, the true total adds that account's exact ledger value
        # (else the pin would erase it — e.g. 2026-10-07, both brokers funded).
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
    # Capital-in (net external deposits) must be driven by external FLOWS whenever
    # our records reach the account's inception. Crucially, "reaches inception" is a
    # data-COVERAGE question — decided from the raw activity timeline — NOT from
    # whether the backward walk zeroed out. Split/FX reconstruction noise (e.g. a
    # leveraged-ETF split journal) can leave a phantom opening_qty/opening_cash even
    # when we demonstrably have the full history; seeding capital from the day-one
    # VALUE in that case silently smuggles the day-one market move + §6.6 seam
    # factor into "net deposits". The old heuristic (`not opening_qty and
    # residual < $100`) flipped to False on any such residual, so capital regressed
    # every time the anchor was forced off "today" — unposted trades or a fresh API
    # key re-trigger it (learned 2026-09-21: capital read 18,421 vs the true 19,000
    # because a phantom SOXL split residual + $681 opening cash tripped the check).
    # The account began empty iff no activity predates the first external flow.
    flow_dates = sorted(recon.flows)
    earliest_act = min((a["transaction_date"] for a in acts if a.get("transaction_date")), default=None)
    inception_covered = bool(flow_dates and earliest_act and earliest_act >= flow_dates[0])
    fx_gain = fx_translation_series(recon.daily_qty, recon.daily_cash, closes, fx_micro, sym_currency)
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
            "message": f"opening residual ({'; '.join(bits)}) — split/FX reconstruction noise "
                       "or missing micro income/fee rows; folded into investment P&L, not counted "
                       "as deposits (capital-in stays equal to external flows)",
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
                fx_gain_cad_cents=fx_gain.get(d, 0),
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
    US or TSX) — the calendar the per-day stats are measured on."""
    async with sessions() as session:
        rows = (await session.execute(
            select(func.distinct(func.date(Candle.ts))).where(Candle.interval.in_(("OneDay", YF_INTERVAL)))
        )).scalars().all()
    return {date.fromisoformat(r) for r in rows if r}


async def header_stats(sessions: async_sessionmaker, live_total_cad: float | None = None,
                       today: date | None = None) -> dict:
    """MWR since data start + TWR YTD for the dashboard header (§8.1). With a
    live total, today is priced live — the same last point as the Performance
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


# Questrade's API returns some (typically older) trade rows with BOTH `symbol`
# and `symbolId` empty — only the issuer name in the description. Left alone,
# those shares get misattributed to a pre-window opening balance, double-counting
# their purchase money as capital-in. Repair by matching the description's
# issuer-name prefix to a ticker.
_STOP_TOKENS = {
    "COM", "INC", "CORP", "CORPORATION", "CO", "LTD", "PLC", "SA", "THE",
    "TRUST", "TR", "UNIT", "UNITS", "SER", "SERIES", "CL", "CLASS", "A", "B",
    "ETF", "FD", "FUND", "NEW", "1", "I", "II",
}


def _desc_key(s: str) -> str:
    """Issuer-name comparison key: first double-space-delimited chunk, punctuation
    → space (so AMAZON.COM ≡ AMAZON COM), stop-suffixes dropped, then joined
    (so MCDONALDS ≡ MCDONALD'S)."""
    first_chunk = re.split(r"\s{2,}", s.strip())[0]
    toks = re.sub(r"[^A-Z0-9 ]", " ", first_chunk.upper()).split()
    return "".join(t for t in toks if t not in _STOP_TOKENS)


def _keys_match(a: str, b: str) -> bool:
    n = min(len(a), len(b))
    return n >= 4 and (a.startswith(b) or b.startswith(a))


async def repair_missing_tickers(sessions: async_sessionmaker, qt: QuestradeClient) -> int:
    from .activities import collapse_duplicate_dividends, rehash_all

    # First collapse duplicate dividends (same payment under a clean ticker + an
    # internal registry key) and fix mangled dividend symbols — a separate class of
    # dirty Questrade data from the symbol-less trades handled below.
    divs_fixed = await collapse_duplicate_dividends(sessions)
    async with sessions() as session:
        rows = list(
            (
                await session.execute(
                    select(Activity).where(
                        or_(Activity.ticker.is_(None), Activity.ticker == ""),
                        Activity.qty_micro.is_not(None),
                        Activity.qty_micro != 0,
                    )
                )
            ).scalars()
        )
        if not rows:
            if divs_fixed:
                await rehash_all(sessions)  # survivors adopt the symbol-free dividend key
            return divs_fixed
        known = [
            (s.ticker, _desc_key(s.description))
            for s in (await session.execute(select(Symbol))).scalars()
            if s.description and not _JUNK_SYMBOL.search(s.ticker)  # junk registry rows never win
        ]

    repaired = 0
    cache: dict[str, str | None] = {}
    for row in rows:
        if not row.description:
            continue
        key = _desc_key(row.description)
        if key not in cache:
            matches = {t for t, k in known if _keys_match(key, k)}
            ticker = matches.pop() if len(matches) == 1 else None
            if ticker is None and key:
                ticker = await _search_by_name(sessions, qt, key, row.currency)
            cache[key] = ticker
            if cache[key] is None:
                log.warning("could not resolve ticker for activity description %r", row.description[:60])
        if cache[key]:
            async with sessions() as session:
                db_row = await session.get(Activity, row.id)
                db_row.ticker = cache[key]
                await session.commit()
            repaired += 1
    if repaired:
        log.info("repaired %d symbol-less activity rows", repaired)
    if repaired or divs_fixed:
        # Hashes were built on the old symbol — recompute so the same activity
        # arriving from another source/symbol dedups instead of double-counting.
        await rehash_all(sessions)
    return repaired + divs_fixed


# The export uses internal codes for some rows (N003056, 5BHYPY6.90, …). Real
# North-American tickers carry no digits, so any digit marks a junk candidate;
# if description resolution fails the original symbol is kept (plus a warning
# downstream), never silently dropped.
_JUNK_SYMBOL = re.compile(r"\d")


async def normalize_import_tickers(sessions: async_sessionmaker, qt: QuestradeClient, rows: list[dict]) -> int:
    """CSV rows: replace missing/junk symbols with real tickers resolved from
    the description, BEFORE hashing — cross-source dedup depends on both
    sources producing the same tuple."""
    async with sessions() as session:
        known = [
            (s.ticker, _desc_key(s.description))
            for s in (await session.execute(select(Symbol))).scalars()
            if s.description and not _JUNK_SYMBOL.search(s.ticker)
        ]
    cache: dict[str, str | None] = {}
    fixed = 0
    for row in rows:
        t = row.get("ticker")
        if t and not _JUNK_SYMBOL.search(t):
            continue
        desc = row.get("description") or ""
        key = _desc_key(desc)
        if not key:
            continue  # keep whatever the export said
        if key not in cache:
            matches = {tk for tk, k in known if _keys_match(key, k)}
            ticker = matches.pop() if len(matches) == 1 else None
            if ticker is None:
                ticker = await _search_by_name(sessions, qt, key, row.get("currency"))
            cache[key] = ticker
        if cache[key] is not None and cache[key] != t:
            row["ticker"] = cache[key]
            fixed += 1
    if fixed:
        log.info("normalized %d import rows' symbols", fixed)
    return fixed


async def _search_by_name(
    sessions: async_sessionmaker, qt: QuestradeClient, key: str, currency: str | None = None
) -> str | None:
    """Questrade's search matches TICKER prefixes, not names — so probe with
    shrinking prefixes of the name key (MCDONALDS → MCDON → MCDO → MCD hits)
    and validate every candidate against the description key. Ambiguity between
    cross-listings is broken by the row's currency."""
    candidates: dict[str, dict] = {}
    for n in (len(key), 5, 4, 3):
        if n > len(key):
            continue
        try:
            results = await qt.symbol_search(key[:n])
        except Exception as exc:
            log.warning("symbol search for %r failed: %s", key[:n], exc)
            continue
        candidates = {
            r["symbol"]: r for r in results
            if r.get("description") and _keys_match(_desc_key(r["description"]), key)
            # Equities only: name search also surfaces bonds/notes (e.g. the
            # "MCDONALDS CORP MEDIUM TERM NOTE" 5BHYPY6.90) which must never
            # win a match for an equity trade row.
            and r.get("securityType", "Stock") == "Stock"
        }
        if len(candidates) > 1 and currency:
            narrowed = {s: r for s, r in candidates.items() if r.get("currency") == currency}
            if narrowed:
                candidates = narrowed
        if candidates:
            break
    if len(candidates) != 1:
        return None
    info = next(iter(candidates.values()))
    async with sessions() as session:
        row = await session.get(Symbol, info["symbol"])
        if row is None:
            row = Symbol(ticker=info["symbol"])
            session.add(row)
        row.qt_symbol_id = info.get("symbolId")
        row.currency = info.get("currency")
        row.exchange = info.get("listingExchange")
        row.description = info.get("description")
        await session.commit()
    return info["symbol"]


async def resolve_symbols(sessions: async_sessionmaker, qt: QuestradeClient, tickers: list[str]) -> None:
    """Make sure every historical ticker has currency/exchange/symbol_id cached."""
    async with sessions() as session:
        known = {s.ticker for s in (await session.execute(select(Symbol))).scalars() if s.currency}
    for t in sorted(set(tickers) - known):
        try:
            matches = await qt.symbol_search(t)
        except Exception as exc:
            log.warning("symbol search failed for %s: %s", t, exc)
            continue
        info = next((m for m in matches if m.get("symbol") == t), None)
        if not info:
            continue
        async with sessions() as session:
            row = await session.get(Symbol, t)
            if row is None:
                row = Symbol(ticker=t)
                session.add(row)
            row.qt_symbol_id = info.get("symbolId")
            row.currency = info.get("currency")
            row.exchange = info.get("listingExchange")
            row.description = info.get("description")
            await session.commit()


async def refresh_reference_data(sessions: async_sessionmaker, qt: QuestradeClient, yf: YfService) -> None:
    """Make the symbol registry and daily-candle coverage catch up with
    whatever tickers now exist in `activities` (new tickers arrive via CSV
    imports and repairs, not just bootstrap)."""
    async with sessions() as session:
        act_tickers = sorted({t for (t,) in (await session.execute(select(Activity.ticker).distinct())) if t})
    if not act_tickers:
        return
    if not await qt_retired(sessions):
        await resolve_symbols(sessions, qt, act_tickers)
    await ensure_symbols_yf(sessions, yf, act_tickers)
    await refresh_market_data(sessions, yf)


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
            log.warning("could not resolve %s on Yahoo — valuation will flag it", t)
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
    """yfinance daily bars (split-adjusted) for the ledger's tickers — all of
    them by default — from the first activity date. Replaces Questrade candles:
    the full range is re-fetched so a split re-adjusts history seamlessly."""
    start = await first_activity_date(sessions) or date.today()
    async with sessions() as session:
        if tickers is None:
            tickers = sorted({t for (t,) in (await session.execute(select(Activity.ticker).distinct())) if t})
        sym_rows = {s.ticker: s for s in (await session.execute(select(Symbol).where(Symbol.ticker.in_(tickers)))).scalars()}
    for t in tickers:
        s = sym_rows.get(t)
        await yf.refresh_bars(t, to_yf_symbol(t, s.exchange if s else None), "OneDay", start)


async def retire_questrade(sessions: async_sessionmaker, qt: QuestradeClient) -> dict:
    """Freeze Questrade after the move to Wealthsimple. One last forced snapshot
    while the token still works — the live (zero) balances become the closing
    ground truth the reconstruction anchors on, so the frozen history ends
    exactly at zero instead of carrying old activity-vs-snapshot noise into
    today. Closed accounts are marked; Questrade jobs, token refreshes and the
    broken-token banner then stand down for good."""
    ids = await qt_account_ids(sessions)
    closing = None
    try:
        active = {str(a.get("number")) for a in await qt.accounts()}
        async with sessions() as session:
            for acc in (await session.execute(select(Account).where(Account.broker == QUESTRADE))).scalars():
                acc.status = "Active" if acc.qt_account_id in active else "Closed"
            await session.commit()
        closing = await take_snapshots(sessions, qt, ids, force=True)
    except Exception as exc:
        log.warning("no closing Questrade snapshot (%s); history stays anchored at the last one", exc)
    value = {"at": date.today().isoformat(), "closing_snapshot": closing.isoformat() if closing else None}
    await set_setting(sessions, "qt_retired", value)
    return value


async def full_bootstrap(sessions, qt: QuestradeClient, fx, yf: YfService, progress=None) -> str:
    """Manual bootstrap (§13): regime check → backfills → snapshot anchor → recompute."""
    from .regime import detect_regime

    def report(msg: str):
        log.info("bootstrap: %s", msg)
        if progress:
            progress(msg)

    if await qt_retired(sessions):
        return "skipped: Questrade is retired — Wealthsimple history comes from the CSV import"
    ids = await qt_account_ids(sessions)
    if not ids:
        return "no tracked accounts — pick accounts in Settings first"

    report("checking candle adjustment regime")
    regime = await detect_regime(sessions, qt, yf)

    report("backfilling activities")
    earliest = date.today()
    total_new = 0
    for acc in ids:
        r = await backfill_account(qt, sessions, acc, progress=report)
        total_new += r["new"]
        if r["earliest"]:
            earliest = min(earliest, r["earliest"])

    async with sessions() as session:
        first_act = (
            await session.execute(select(Activity.transaction_date).order_by(Activity.transaction_date).limit(1))
        ).scalar_one_or_none()
    if first_act:
        earliest = min(earliest, first_act)

    report(f"backfilling FX from {earliest}")
    await fx.backfill(earliest)

    async with sessions() as session:
        act_tickers = sorted({t for (t,) in (await session.execute(select(Activity.ticker).distinct())) if t})
    report(f"resolving {len(act_tickers)} symbols")
    await resolve_symbols(sessions, qt, act_tickers)

    report("repairing symbol-less trade rows")
    repaired = await repair_missing_tickers(sessions, qt)
    if repaired:
        async with sessions() as session:
            act_tickers = sorted({t for (t,) in (await session.execute(select(Activity.ticker).distinct())) if t})
        await resolve_symbols(sessions, qt, act_tickers)

    report("backfilling daily candles")
    async with sessions() as session:
        sym_rows = list((await session.execute(select(Symbol).where(Symbol.ticker.in_(act_tickers)))).scalars())
    for s in sym_rows:
        if s.qt_symbol_id:
            try:
                await candle_svc.backfill_daily(qt, sessions, s.ticker, s.qt_symbol_id, earliest)
            except Exception as exc:
                log.warning("candle backfill failed for %s: %s", s.ticker, exc)

    benchmarks = await get_setting(sessions, "benchmarks", DEFAULT_BENCHMARKS)
    report("fetching benchmark history")
    for b in benchmarks:
        await yf.refresh_daily_closes(b, earliest)

    report("taking ground-truth snapshot")
    await take_snapshots(sessions, qt, ids, force=True)

    report("reconstructing history")
    result = await recompute(sessions, yf)
    return (
        f"regime={regime}; {total_new} new activities; series {result.get('start')}→{result.get('end')} "
        f"({result.get('days', 0)} days, {result.get('warnings', 0)} warnings)"
        if result.get("ok")
        else f"regime={regime}; {total_new} new activities; recompute failed: {result.get('reason')}"
    )
