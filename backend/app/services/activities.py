"""Activities: normalization helpers and multiset dedup — SPEC.md §4.2.

Rows carry no stable IDs and two identical same-day fills are legal. The hash is
the typed-field tuple plus an occurrence index within each identical-tuple group
per (account, day); re-importing an overlapping export regenerates the same
hashes, so INSERT OR IGNORE yields multiset semantics (counts per tuple, never
set membership). Stored hashes (Questrade API/CSV era included) were built with
these exact rules — changing tuple_key would break dedup against them.

DECISION: the description is EXCLUDED from the hash tuple. Rationale: sources
phrase descriptions differently, and cross-source dedup must collapse the same
activity from both. Two genuinely distinct same-day rows that differ only in
description still survive, because they occupy distinct occurrence indices
within the same tuple group.
"""

import hashlib
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Activity

log = logging.getLogger(__name__)

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
