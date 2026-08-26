"""Orchestration: snapshots → reconstruction → valuation → series_daily.

recompute() is pure-DB (no Questrade calls) so it can rerun cheaply after every
activities sync or CSV import (§6.7). Network work (activities/candles/FX
backfill, snapshot taking) lives in the jobs and in full_bootstrap().
"""

import json
import logging
import re
from datetime import date

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import TZ

from ..models import (
    Account,
    Activity,
    BalanceSnapshot,
    CashLedgerRow,
    FxRate,
    HoldingsLedgerRow,
    PositionSnapshot,
    SeriesDaily,
    Setting,
    Symbol,
)
from ..questrade.client import QuestradeClient
from ..yf import YfService, to_yf_symbol
from . import candles as candle_svc
from .activities import backfill_account, load_activities
from .reconstruction import BACK_ADJUSTED, walk_backward
from .series import apply_ground_truth, flows_to_cad, twr_index, value_series

log = logging.getLogger(__name__)

DEFAULT_BENCHMARKS = ["QQQ", "SMH", "SPY"]


async def tracked_ids(sessions: async_sessionmaker) -> list[str]:
    async with sessions() as session:
        return list((await session.execute(select(Account.qt_account_id).where(Account.tracked))).scalars())


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


def _activity_dicts(rows: list[Activity]) -> list[dict]:
    return [
        {
            "id": a.id, "account_id": a.account_id, "transaction_date": a.transaction_date,
            "type": a.type, "action": a.action, "ticker": a.ticker,
            "qty_micro": a.qty_micro, "net_amount_cents": a.net_amount_cents, "currency": a.currency,
        }
        for a in rows
    ]


async def recompute(sessions: async_sessionmaker, yf: YfService) -> dict:
    ids = await tracked_ids(sessions)
    if not ids:
        return {"ok": False, "reason": "no tracked accounts"}
    candidates = await snapshot_dates(sessions, ids)
    if not candidates:
        return {"ok": False, "reason": "no snapshot anchor yet — run bootstrap"}

    acts = _activity_dicts(await load_activities(sessions, ids + [""]))
    regime = await get_setting(sessions, "candle_adjustment_regime", BACK_ADJUSTED)
    if regime not in ("back_adjusted", "unadjusted"):
        regime = BACK_ADJUSTED

    newest_anchor = await anchor_state(sessions, ids, candidates[0])
    tickers = sorted({a["ticker"] for a in acts if a["ticker"]} | set(newest_anchor[1]))
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

    recon = None
    anchor_date = candidates[0]
    fallback = None
    for cand in candidates:
        state = await anchor_state(sessions, ids, cand)
        if state is None:
            continue
        r = walk_backward(state[1], state[2], cand, acts, regime, splits)
        if fallback is None:
            fallback = (cand, r)
        if not has_negative_qty(r):
            recon, anchor_date = r, cand
            break
    if recon is None:
        anchor_date, recon = fallback

    if anchor_date < candidates[0]:
        extend_forward(recon, acts, anchor_date, candidates[0])
        recon.warnings = [w for w in recon.warnings if "post-date the anchor" not in w["message"]]
        recon.warnings.append({
            "date": anchor_date.isoformat(), "ticker": None,
            "message": f"today's snapshot disagrees with recorded activities (likely trades not yet posted "
                       f"by Questrade) — anchored at {anchor_date}, holdings after that date approximated and "
                       f"pinned to real snapshots; corrects itself when the trades post",
        })

    closes = {t: await candle_svc.daily_closes_cents(sessions, t) for t in tickers}
    fx_micro = await _fx_micro(sessions)
    values, val_warnings = value_series(recon.daily_qty, recon.daily_cash, closes, fx_micro, sym_currency)
    gt = await ground_truth_cad(sessions, ids, fx_micro)
    scaled = apply_ground_truth(values, gt)
    flows_cad = flows_to_cad(recon.flows, fx_micro)
    idx, twr_warnings = twr_index(scaled, flows_cad)

    dates = sorted(scaled)
    # When the activity window demonstrably reaches the account's inception
    # (no opening holdings, near-zero opening cash), capital-in is pure external
    # flows — the first day's value would smuggle the day-one market move, the
    # §6.6 seam factor, AND any small unrecorded-income residual into "net
    # deposits". A sub-$100 cash residual (missing micro income/fee rows) is
    # tolerated but surfaced as a warning — it belongs to P&L, not capital.
    residual_cents = sum(abs(c) for c in recon.opening_cash.values())
    inception_covered = not recon.opening_qty and residual_cents < 100_00
    if not recon.opening_qty and residual_cents >= 1:
        pretty = ", ".join(f"{cur} {cents / 100:+.2f}" for cur, cents in recon.opening_cash.items() if cents)
        recon.warnings.append({
            "date": dates[0].isoformat() if dates else None,
            "ticker": None,
            "message": f"opening cash residual ({pretty}) — a small amount of cash activity "
                       "(likely dividends/interest/fees) is missing from the records; it is "
                       "counted as investment P&L, not as deposits",
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
            ))
        for d, per_ticker in recon.daily_qty.items():
            for t, q in per_ticker.items():
                session.add(HoldingsLedgerRow(date=d, ticker=t, qty_micro=q))
        for d, per_cur in recon.daily_cash.items():
            for cur, cents in per_cur.items():
                session.add(CashLedgerRow(date=d, currency=cur, balance_cents=cents))
        await session.commit()

    warnings = recon.warnings + val_warnings + twr_warnings
    await set_setting(sessions, "reconciliation_warnings", warnings[:100])
    await set_setting(sessions, "flow_events", recon.flow_events)
    log.info("series recomputed: %d days (%s..%s), %d warnings", len(dates),
             dates[0] if dates else "-", dates[-1] if dates else "-", len(warnings))
    return {"ok": True, "days": len(dates), "start": dates[0].isoformat() if dates else None,
            "end": dates[-1].isoformat() if dates else None, "warnings": len(warnings)}


async def header_stats(sessions: async_sessionmaker) -> dict:
    """MWR since data start + TWR YTD for the dashboard header (§8.1)."""
    from .series import range_stats

    async with sessions() as session:
        rows = list((await session.execute(select(SeriesDaily).order_by(SeriesDaily.date))).scalars())
    if len(rows) < 2:
        return {"mwr": None, "mwr_since": None, "twr_ytd": None}
    dates = [r.date for r in rows]
    values = {r.date: r.portfolio_value_cad_cents or 0 for r in rows}
    idx = {r.date: r.twr_index or 100.0 for r in rows}
    flows = {r.date: r.flows_cad_cents or 0 for r in rows}
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
            return 0
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
        from .activities import rehash_all

        log.info("repaired %d symbol-less activity rows", repaired)
        # Hashes were built on the empty symbol — recompute so the same trade
        # arriving from another source (CSV) dedups instead of double-counting.
        await rehash_all(sessions)
    return repaired


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


async def refresh_reference_data(sessions: async_sessionmaker, qt: QuestradeClient) -> None:
    """Make the symbol registry and QT daily-candle coverage catch up with
    whatever tickers now exist in `activities` (new tickers arrive via CSV
    imports and repairs, not just bootstrap)."""
    async with sessions() as session:
        act_tickers = sorted({t for (t,) in (await session.execute(select(Activity.ticker).distinct())) if t})
        first_act = (
            await session.execute(select(Activity.transaction_date).order_by(Activity.transaction_date).limit(1))
        ).scalar_one_or_none()
    if not act_tickers or first_act is None:
        return
    await resolve_symbols(sessions, qt, act_tickers)
    async with sessions() as session:
        sym_rows = list((await session.execute(select(Symbol).where(Symbol.ticker.in_(act_tickers)))).scalars())
    from . import candles as candle_svc

    for s in sym_rows:
        if s.qt_symbol_id:
            try:
                await candle_svc.backfill_daily(qt, sessions, s.ticker, s.qt_symbol_id, first_act)
            except Exception as exc:
                log.warning("candle backfill failed for %s: %s", s.ticker, exc)


async def full_bootstrap(sessions, qt: QuestradeClient, fx, yf: YfService, progress=None) -> str:
    """Manual bootstrap (§13): regime check → backfills → snapshot anchor → recompute."""
    from .regime import detect_regime

    def report(msg: str):
        log.info("bootstrap: %s", msg)
        if progress:
            progress(msg)

    ids = await tracked_ids(sessions)
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
