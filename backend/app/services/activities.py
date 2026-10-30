"""Activities: normalization, multiset dedup, API sync, deep backfill — SPEC.md §4.2.

Rows carry no stable IDs and two identical same-day fills are legal. The hash is
the typed-field tuple plus an occurrence index within each identical-tuple group
per (account, day); re-syncing a window regenerates the same hashes, so INSERT
OR IGNORE yields multiset semantics (counts per tuple, never set membership).

DECISION: the description is EXCLUDED from the hash tuple. Rationale: the CSV
export and the API phrase descriptions differently, and cross-source dedup must
collapse the same activity from both sources. Two genuinely distinct same-day
rows that differ only in description still survive, because they occupy distinct
occurrence indices within the same tuple group.
"""

import hashlib
import json
import logging
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import TZ
from ..models import Activity
from ..questrade.client import QuestradeClient

log = logging.getLogger(__name__)

WINDOW_DAYS = 31
OVERLAP_SYNC_DAYS = 35
# Backfill stops after this many consecutive empty 31-day windows.
# DECISION: the API gives no explicit "start of history" signal; six empty
# months is taken as the edge of the API's lookback.
MAX_EMPTY_WINDOWS = 6
MAX_WINDOWS = 400  # hard cap (~34 years)

MICRO = Decimal(1_000_000)
CENTS = Decimal(100)


def parse_money_cents(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int((Decimal(str(v)) * CENTS).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    s = str(v).strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    if not s or s in ("-", "—"):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if neg:
        d = -d
    return int((d * CENTS).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_qty_micro(v) -> int | None:
    if v is None or v == "":
        return None
    s = str(v).strip().replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return int((d * MICRO).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    # ISO with offset (API), plain ISO, and the export's US-style formats.
    for fmt in (None, "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %I:%M:%S %p", "%m/%d/%Y %I:%M:%S %p"):
        try:
            if fmt is None:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def normalize_api_row(account_id: str, raw: dict) -> dict:
    return {
        "account_id": str(account_id),
        "trade_date": parse_date(raw.get("tradeDate")),
        "transaction_date": parse_date(raw.get("transactionDate")),
        "settlement_date": parse_date(raw.get("settlementDate")),
        "type": _norm(raw.get("type")),
        "action": _norm(raw.get("action")),
        "ticker": (str(raw.get("symbol")).strip().upper() or None) if raw.get("symbol") else None,
        "qty_micro": parse_qty_micro(raw.get("quantity")),
        "price_micro": (
            int((Decimal(str(raw["price"])) * MICRO).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            if raw.get("price") not in (None, "", 0)
            else None
        ),
        "gross_amount_cents": parse_money_cents(raw.get("grossAmount")),
        "commission_cents": parse_money_cents(raw.get("commission")),
        "net_amount_cents": parse_money_cents(raw.get("netAmount")),
        "currency": (str(raw.get("currency")).strip().upper() or None) if raw.get("currency") else None,
        "description": (str(raw.get("description")).strip() or None) if raw.get("description") else None,
        "raw_json": json.dumps(raw),
    }


def _is_dividend_row(row: dict) -> bool:
    """Dividend / DRIP income. Questrade returns the SAME payment under a clean
    ticker or an internal registry key (e.g. QQQM vs H082968, TQQQ vs P025429)
    with cosmetically different descriptions and a sometimes-missing trade_date —
    so keeping the symbol in the dedup key lets one dividend land twice (learned
    2026-09-21: QQQM/TQQQ dividends double-counted after a post-API-key re-sync)."""
    t = (row.get("type") or "").lower()
    return "dividend" in t or t == "div" or "drip" in t


def tuple_key(row: dict) -> tuple:
    """Typed-field identity for dedup. Description deliberately excluded (see
    module doc). Date basis = trade_date when present, else transaction_date:
    the API posts trades at T+1 (transactionDate) while the web export stamps
    the trade day itself — the same fill must produce the same key from both.
    Dividends dedup symbol-free (account+date+amount+currency identifies the cash
    event); the symbol is unreliable and would defeat cross-symbol dedup. Two real
    same-day same-amount dividends from different stocks still survive via distinct
    occurrence indices."""
    effective = row.get("trade_date") or row.get("transaction_date")
    ticker = "" if _is_dividend_row(row) else (row["ticker"] or "")
    return (
        row["account_id"],
        effective.isoformat() if effective else "",
        row["type"],
        row["action"],
        ticker,
        row["qty_micro"] or 0,
        row["price_micro"] or 0,
        row["gross_amount_cents"] or 0,
        row["commission_cents"] or 0,
        row["net_amount_cents"] or 0,
        row["currency"] or "",
    )


def assign_hashes(rows: list[dict]) -> list[dict]:
    """Occurrence-indexed hashes. Must be called with complete days — a calendar
    day must never be split across two calls, or occurrence indices drift."""
    seen: dict[tuple, int] = {}
    out = []
    for row in sorted(rows, key=lambda r: (r["transaction_date"] or date.min, tuple_key(r))):
        key = tuple_key(row)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        digest = hashlib.sha256(("|".join(map(str, key)) + f"#occ{occ}").encode()).hexdigest()
        out.append({**row, "hash": digest, "occurrence_idx": occ})
    return out


def _row_dict(a) -> dict:
    return {
        "account_id": a.account_id, "trade_date": a.trade_date, "transaction_date": a.transaction_date,
        "type": a.type, "action": a.action, "ticker": a.ticker,
        "qty_micro": a.qty_micro, "price_micro": a.price_micro,
        "gross_amount_cents": a.gross_amount_cents, "commission_cents": a.commission_cents,
        "net_amount_cents": a.net_amount_cents, "currency": a.currency,
    }


async def rehash_all(sessions: async_sessionmaker) -> int:
    """Recompute every stored hash from current typed fields. Needed after
    ticker repair: a row ingested with an empty symbol carries a hash built on
    '', so the same trade arriving later from the CSV (with a real symbol)
    would not dedup against it. Deterministic: occurrence indices assigned in
    insertion order. Two-phase update avoids transient UNIQUE collisions."""
    async with sessions() as session:
        rows = list((await session.execute(select(Activity).order_by(Activity.id))).scalars())
        # One global pass in id order: the tuple already contains account + the
        # effective date, so occurrence indices need no external grouping (and
        # grouping by posting date would split same-trade-date twins).
        seen: dict[tuple, int] = {}
        new_hashes: dict[int, tuple[str, int]] = {}
        for r in rows:
            key = tuple_key(_row_dict(r))
            occ = seen.get(key, 0)
            seen[key] = occ + 1
            digest = hashlib.sha256(("|".join(map(str, key)) + f"#occ{occ}").encode()).hexdigest()
            new_hashes[r.id] = (digest, occ)
        changed = [r for r in rows if new_hashes[r.id][0] != r.hash]
        if not changed:
            return 0
        for r in changed:  # phase 1: park on unique placeholders
            r.hash = f"rehash-tmp-{r.id}"
        await session.flush()
        for r in changed:  # phase 2: final hashes
            r.hash, r.occurrence_idx = new_hashes[r.id]
        await session.commit()
    log.info("rehashed %d activity rows", len(changed))
    return len(changed)


def _dividend_ticker_rank(ticker: str) -> tuple:
    """Survivor rank among duplicate dividend rows — LOWER is cleaner. A plain
    ticker (QQQM, TQQQ, TD) beats a registry key (H082968, P025429) or a mangled
    leading-dot symbol (.TD)."""
    t = ticker or ""
    registry = bool(re.fullmatch(r"[A-Z]\d{5,}", t))  # Questrade internal key
    return (registry, t.startswith("."), t == "", len(t), t)


async def collapse_duplicate_dividends(sessions: async_sessionmaker) -> int:
    """Remove duplicate dividend rows Questrade returned under BOTH a clean ticker
    and an internal registry key (same account+effective-date+action+amount+
    currency) — keeping the cleanest-symbol row. Self-healing repair for data
    ingested before the symbol-free dividend dedup key; new syncs no longer create
    these (see tuple_key). Also strips mangled leading-dot dividend tickers
    (.TD -> TD) so the payment rolls into the real position's row."""
    from collections import defaultdict

    async with sessions() as session:
        rows = list((await session.execute(select(Activity))).scalars())
        groups: dict[tuple, list] = defaultdict(list)
        for r in rows:
            if not _is_dividend_row(_row_dict(r)):
                continue
            eff = r.trade_date or r.transaction_date
            # Group on the cash event AND the normalized description. Requiring the
            # description to match (whitespace-collapsed) guarantees we only merge the
            # SAME payment recorded under two symbols — never two different stocks that
            # happen to pay the same amount on the same day.
            desc = re.sub(r"\s+", " ", (r.description or "")).strip().upper()
            groups[(r.account_id, eff, r.action or "", r.net_amount_cents or 0, r.currency or "", desc)].append(r)
        removed = 0
        survivors = []
        for grp in groups.values():
            grp.sort(key=lambda r: _dividend_ticker_rank(r.ticker or ""))
            for dupe in grp[1:]:
                await session.delete(dupe)
                removed += 1
                log.info("collapsed duplicate dividend %s -> kept %s (%s %s %.2f %s)",
                         dupe.ticker, grp[0].ticker, dupe.account_id,
                         (dupe.trade_date or dupe.transaction_date), (dupe.net_amount_cents or 0) / 100, dupe.currency)
            survivors.append(grp[0])
        fixed_dot = 0
        for r in survivors:  # normalize the kept row's mangled symbol
            if r.ticker and r.ticker.startswith("."):
                r.ticker = r.ticker.lstrip(".")
                fixed_dot += 1
        if removed or fixed_dot:
            await session.commit()
    if removed or fixed_dot:
        log.info("dividend cleanup: %d duplicates removed, %d mangled tickers fixed", removed, fixed_dot)
    return removed + fixed_dot


async def upsert_activities(sessions: async_sessionmaker, rows: list[dict]) -> int:
    """Multiset-aware insert; returns the number of genuinely new rows."""
    if not rows:
        return 0
    new = 0
    async with sessions() as session:
        for row in rows:
            stmt = sqlite_insert(Activity).values(**row).on_conflict_do_nothing(index_elements=["hash"])
            result = await session.execute(stmt)
            new += result.rowcount or 0
        await session.commit()
    return new


def _day_bounds(start_day: date, end_day: date) -> tuple[datetime, datetime]:
    """Full-day window boundaries in America/Toronto, so no day is ever split
    between two requests (occurrence-index stability depends on this)."""
    start = datetime.combine(start_day, time.min, tzinfo=TZ)
    end = datetime.combine(end_day, time.max.replace(microsecond=0), tzinfo=TZ)
    return start, end


async def sync_window(
    qt: QuestradeClient, sessions: async_sessionmaker, account_id: str, start_day: date, end_day: date
) -> tuple[int, int]:
    start, end = _day_bounds(start_day, end_day)
    raw = await qt.activities(account_id, start, end)
    rows = assign_hashes([normalize_api_row(account_id, r) for r in raw])
    new = await upsert_activities(sessions, rows)
    return len(rows), new


async def backfill_account(
    qt: QuestradeClient, sessions: async_sessionmaker, account_id: str, progress=None
) -> dict:
    """Walk 31-day windows backwards from today until the API stops returning rows."""
    end_day = date.today()
    fetched_total = new_total = 0
    empty_streak = 0
    windows = 0
    earliest: date | None = None
    while empty_streak < MAX_EMPTY_WINDOWS and windows < MAX_WINDOWS:
        start_day = end_day - timedelta(days=WINDOW_DAYS - 1)
        try:
            fetched, new = await sync_window(qt, sessions, account_id, start_day, end_day)
        except Exception as exc:
            # Deep history often ends with an API error rather than empty pages.
            log.info("activities backfill for %s stopped at %s: %s", account_id, start_day, exc)
            break
        fetched_total += fetched
        new_total += new
        if fetched == 0:
            empty_streak += 1
        else:
            empty_streak = 0
            earliest = start_day
        if progress:
            progress(f"activities {account_id}: back to {start_day}, {new_total} new")
        windows += 1
        end_day = start_day - timedelta(days=1)
    return {"account_id": account_id, "fetched": fetched_total, "new": new_total, "earliest": earliest}


async def sync_recent(qt: QuestradeClient, sessions: async_sessionmaker, account_ids: list[str]) -> int:
    """Daily overlap sync: last 35 days per account (§13 activities_sync)."""
    new_total = 0
    end_day = date.today()
    start_day = end_day - timedelta(days=OVERLAP_SYNC_DAYS)
    for acc in account_ids:
        # Two 31-day-max windows to cover the 35-day overlap span.
        mid = start_day + timedelta(days=WINDOW_DAYS - 1)
        for s, e in ((start_day, mid), (mid + timedelta(days=1), end_day)):
            if s > e:
                continue
            _, new = await sync_window(qt, sessions, acc, s, e)
            new_total += new
    return new_total


async def activity_count(sessions: async_sessionmaker) -> int:
    async with sessions() as session:
        return (await session.execute(select(func.count()).select_from(Activity))).scalar_one()


async def load_activities(sessions: async_sessionmaker, account_ids: list[str]) -> list[Activity]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    select(Activity)
                    .where(Activity.account_id.in_(account_ids))
                    .order_by(Activity.transaction_date)
                )
            ).scalars()
        )
