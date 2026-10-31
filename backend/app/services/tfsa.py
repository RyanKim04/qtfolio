"""TFSA contribution room — one person, summed across every institution.

CRA rules this module encodes:
- A contribution uses room the moment it lands.
- A withdrawal gives its room back on Jan 1 of the NEXT year — never the same year.
- Trades inside the account don't touch room, and a direct institution-to-
  institution transfer (type "transfer") is neither a contribution nor a withdrawal.
- Negative room = over-contribution, taxed 1% per month on the excess.
- Room accrues from the year the holder turns 18 — the first year of the limits table.

The live figure walks forward from an anchor (CRA's confirmed Jan 1 room for some
year) using only transactions dated in or after that year. `from_inception` walks
from the first limit year instead; it exists to prove the two agree.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import TZ
from ..models import TfsaTransaction
from . import history

CONTRIBUTION, WITHDRAWAL, TRANSFER = "contribution", "withdrawal", "transfer"
OVER_TAX_MONTHLY = 0.01
RECONCILE_FROM_MONTH = 3  # CRA My Account shows the new Jan 1 room once issuers have filed (~March)

LIMITS_KEY = "tfsa_annual_limits"  # {"2022": cents | null (TBA), ...}
ANCHOR_KEY = "tfsa_anchor"  # {"year": 2026, "room_cents": ...}
# DECISION: the table starts the year the owner turned 18 (2022), not the TFSA's
# 2009 launch. None = not announced yet (November); projections stand in the
# latest known limit and say so.
DEFAULT_LIMITS = {2022: 600_000, 2023: 650_000, 2024: 700_000, 2025: 700_000, 2026: 700_000, 2027: None}


@dataclass(frozen=True)
class Txn:
    date: date
    type: str
    amount_cents: int  # positive


@dataclass
class YearRoom:
    year: int
    limit_cents: int
    limit_provisional: bool  # limit TBA — the latest known one stands in
    jan1_cents: int
    contributed_cents: int = 0
    withdrawn_cents: int = 0
    first_negative: date | None = None  # first day the room went below zero

    @property
    def end_cents(self) -> int:
        """Room left after every contribution recorded in the year."""
        return self.jan1_cents - self.contributed_cents


def limit_for(year: int, limits: dict[int, int | None]) -> tuple[int, bool]:
    """(annual limit, provisional). A TBA year borrows the latest known earlier limit."""
    if limits.get(year) is not None:
        return limits[year], False
    known = [y for y, c in limits.items() if c is not None and y < year]
    return (limits[max(known)], True) if known else (0, True)


def walk(first_year: int, first_jan1_cents: int, limits: dict[int, int | None],
         txns: list[Txn], through_year: int) -> list[YearRoom]:
    """Room year by year from a known Jan 1 figure; transactions before `first_year`
    are already inside that figure and are ignored."""
    by_year: dict[int, list[Txn]] = {}
    for t in sorted(txns, key=lambda t: t.date):
        if t.type != TRANSFER and t.date.year >= first_year:
            by_year.setdefault(t.date.year, []).append(t)
    out: list[YearRoom] = []
    for y in range(first_year, through_year + 1):
        if out:
            prev = out[-1]
            limit, provisional = limit_for(y, limits)
            jan1 = prev.end_cents + limit + prev.withdrawn_cents
        else:
            limit, provisional = limit_for(y, limits)[0], False  # already inside the given figure
            jan1 = first_jan1_cents
        yr = YearRoom(y, limit, provisional, jan1)
        if jan1 < 0:  # an excess carried over from last year
            yr.first_negative = date(y, 1, 1)
        for t in by_year.get(y, []):
            if t.type == CONTRIBUTION:
                yr.contributed_cents += t.amount_cents
                if yr.first_negative is None and yr.end_cents < 0:
                    yr.first_negative = t.date
            else:  # withdrawal: room returns next Jan 1, via the formula above
                yr.withdrawn_cents += t.amount_cents
        out.append(yr)
    return out


def from_inception(limits: dict[int, int | None], txns: list[Txn], through_year: int) -> list[YearRoom]:
    first = min(y for y, c in limits.items() if c is not None)
    return walk(first, limits[first], limits, txns, through_year)


def _through(today: date, txns: list[Txn]) -> int:
    return max([today.year] + [t.date.year for t in txns])


def summarize(today: date, anchor_year: int, anchor_cents: int,
              limits: dict[int, int | None], txns: list[Txn]) -> dict:
    """Everything the dashboard card shows, in cents."""
    years = walk(anchor_year, anchor_cents, limits, txns, _through(today, txns))
    cur = next(r for r in years if r.year == today.year)
    next_limit, next_provisional = limit_for(today.year + 1, limits)
    over = None
    if cur.end_cents < 0:
        excess = -cur.end_cents
        over = {"excess_cents": excess, "monthly_tax_cents": round(excess * OVER_TAX_MONTHLY),
                "since": cur.first_negative}
    reconcile = None
    if today.month >= RECONCILE_FROM_MONTH and anchor_year < today.year:
        reconcile = {"year": today.year, "computed_jan1_cents": cur.jan1_cents}
    return {
        "year": today.year,
        "jan1_cents": cur.jan1_cents,
        "contributed_cents": cur.contributed_cents,
        "withdrawn_cents": cur.withdrawn_cents,
        "remaining_cents": cur.end_cents,
        "next_year": today.year + 1,
        "next_limit_cents": next_limit,
        "next_limit_provisional": next_provisional,
        "projected_next_jan1_cents": cur.end_cents + next_limit + cur.withdrawn_cents,
        "overcontribution": over,
        "reconcile": reconcile,
        "years": years,
    }


def check(candidate: Txn, anchor_year: int, anchor_cents: int,
          limits: dict[int, int | None], txns: list[Txn], today: date) -> dict:
    """Warnings for recording `candidate`: {"warnings": [{level, code, message}],
    "year", "room_after_cents"} — levels danger | warning | info."""
    y = candidate.date.year
    if y < anchor_year:
        return {"year": y, "room_after_cents": None, "warnings": [{
            "level": "info", "code": "before_anchor",
            "message": f"Dated before the {anchor_year} anchor — CRA's Jan 1, {anchor_year} room already "
                       f"reflects it, so the numbers won't change.",
        }]}
    through = max(_through(today, txns), y)
    before = {r.year: r for r in walk(anchor_year, anchor_cents, limits, txns, through)}
    after = {r.year: r for r in walk(anchor_year, anchor_cents, limits, txns + [candidate], through)}
    out = {"year": y, "room_after_cents": after[y].end_cents, "warnings": []}
    warnings = out["warnings"]
    if candidate.type == TRANSFER:
        warnings.append({"level": "info", "code": "transfer",
                         "message": "A direct transfer between institutions doesn't touch contribution room."})
        return out
    if candidate.type == WITHDRAWAL:
        warnings.append({"level": "info", "code": "withdrawal",
                         "message": f"This room comes back on Jan 1, {y + 1} — not this year."})
        return out

    # Contribution. Adding room use in year y can also push a later year with
    # already-recorded contributions negative, so look at y and everything after.
    worst = min((r for r in after.values() if r.year >= y), key=lambda r: r.end_cents)
    over = worst.end_cents < 0 and worst.end_cents < before[worst.year].end_cents
    if over:
        excess = -worst.end_cents
        tax = round(excess * OVER_TAX_MONTHLY) / 100
        where = (f"exceeds your remaining {y} room ({before[y].end_cents / 100:,.2f})" if worst.year == y
                 else f"leaves {worst.year} over-contributed")
        warnings.append({
            "level": "danger", "code": "over_contribution",
            "message": f"This {where} by {excess / 100:,.2f}. Over-contributions are taxed 1% a month "
                       f"(≈ {tax:,.2f}/month) until withdrawn.",
        })
    withdrawn = sum(t.amount_cents for t in txns
                    if t.type == WITHDRAWAL and t.date.year == y and t.date <= candidate.date)
    if withdrawn:
        warnings.append({
            "level": "danger" if over else "warning", "code": "recontribution",
            "message": f"You withdrew {withdrawn / 100:,.2f} earlier in {y}. Withdrawals only come back as room "
                       f"on Jan 1, {y + 1} — re-contributing now uses this year's room "
                       f"({before[y].end_cents / 100:,.2f} left).",
        })
    return out


# -- storage -------------------------------------------------------------


def today_local() -> date:
    return datetime.now(TZ).date()


async def load_limits(sessions: async_sessionmaker) -> dict[int, int | None]:
    stored = await history.get_setting(sessions, LIMITS_KEY, None)
    if not isinstance(stored, dict):
        return dict(DEFAULT_LIMITS)
    return {int(y): (int(c) if c is not None else None) for y, c in stored.items()}


async def load_anchor(sessions: async_sessionmaker) -> tuple[int, int] | None:
    stored = await history.get_setting(sessions, ANCHOR_KEY, None)
    if not isinstance(stored, dict) or "year" not in stored:
        return None
    return int(stored["year"]), int(stored["room_cents"])


async def load_rows(sessions: async_sessionmaker) -> list[TfsaTransaction]:
    async with sessions() as session:
        return list((await session.execute(
            select(TfsaTransaction).order_by(TfsaTransaction.date, TfsaTransaction.id))).scalars())


def to_txn(row: TfsaTransaction) -> Txn:
    return Txn(row.date, row.type, row.amount_cad_cents)
