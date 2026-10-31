"""Wealthsimple activities-export CSV → normalized activity rows.

Wealthsimple has no public API; the official export (Activity → Download
activities → CSV) is the data source. Owner decision 2026-10-27: official CSV
over the unofficial GraphQL clients (no trading-capable session token stored,
no ToS exposure). Columns:

  effective_date, effective_time, settlement_date, account_id, account_type,
  activity_type, activity_sub_type, description, direction, symbol, name,
  currency, quantity, unit_price, commission, net_cash_amount

Rows are mapped onto the type/action vocabulary reconstruction.classify()
already speaks, so both brokers flow through one ledger:

- Trade BUY/SELL            → trades / buy|sell
- MoneyMovement (EFT, …)    → deposits | withdrawals by sign (external flows)
- MoneyMovement *TRANSFER*  → transfers (broker-to-broker; internal iff matched)
- SecurityTransfer          → transfers with signed qty and NO cash: the export
  puts the book value in net_cash_amount (in CAD even for USD stocks), which is
  not a cash movement
- FxExchange → fx conversion · Dividend → dividends · Interest → interest
- Tax / Fee / AdministrativePayment → fees and rebates (cash, no flow)
- anything else keeps its own type → classify() "unknown" → reconciliation warning
"""

import csv
import io
import json
import re

from .activities import parse_date, parse_money_cents, parse_qty_micro

BROKER = "wealthsimple"
REQUIRED = {"effective_date", "account_id", "activity_type", "net_cash_amount"}


def is_ws_export(content: bytes) -> bool:
    head = content[:2048].decode("utf-8-sig", errors="replace").splitlines()[:1]
    cols = {c.strip().lower() for c in head[0].split(",")} if head else set()
    return REQUIRED <= cols


def app_ticker(symbol: str | None, currency: str | None) -> str | None:
    """WS symbol → the app's ticker. DECISION: CAD listings take Questrade's
    `.TO` convention (QT listed the Alphabet CDR as GOOG.TO), so a position keeps
    one ticker across the transfer, and WS's bare `GOOG` in CAD (the CDR) can
    never collide with the US `GOOG`."""
    s = (symbol or "").strip().upper()
    if not s:
        return None
    if (currency or "").upper() == "CAD" and not re.search(r"\.(TO|V|NE|CN)$", s):
        return f"{s}.TO"
    return s


def _map(rec: dict) -> tuple[str, str, int | None, int]:
    """(type, action, signed qty_micro, net_amount_cents) for one export row."""
    at = (rec.get("activity_type") or "").strip()
    sub = (rec.get("activity_sub_type") or "").strip()
    net = parse_money_cents(rec.get("net_cash_amount")) or 0
    qty = parse_qty_micro(rec.get("quantity"))
    desc = (rec.get("description") or "").lower()

    if at == "Trade":
        return "trades", sub.lower(), qty, net
    if at == "MoneyMovement":
        if "TRANSFER" in sub.upper():
            return "transfers", "tfi" if net > 0 else "tfo", None, net
        return ("deposits", "dep", None, net) if net >= 0 else ("withdrawals", "wdr", None, net)
    if at == "SecurityTransfer":
        if qty and qty > 0 and "out of the account" in desc:
            qty = -qty
        return "transfers", "tfi" if (qty or 0) > 0 else "tfo", qty, 0
    if at == "FxExchange":
        return "fx conversion", "fxt", None, net
    if at == "Dividend":
        # The export repeats the cash amount in `quantity` (ORCL "1.5" = US$1.50),
        # not shares. A reinvestment would arrive as its own Trade row.
        return "dividends", "div", None, net
    if at == "Interest":
        return "interest", "int", None, net
    if at in ("Tax", "Fee", "AdministrativePayment"):
        return "fees and rebates", (sub if sub not in ("", "-") else at).lower(), None, net
    return at.lower() or "unknown", sub.lower(), qty, net


def parse_ws_export(content: bytes) -> dict:
    """Same shape as importer.parse_export, plus account metadata for upserting
    the WS accounts themselves."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    reader.fieldnames = fields
    rows, errors = [], []
    accounts: dict[str, str] = {}
    for line_no, rec in enumerate(reader, start=2):
        if not any((v or "").strip() for v in rec.values()):
            continue
        d = parse_date(rec.get("effective_date"))
        if d is None:
            errors.append(f"line {line_no}: unparseable effective_date {rec.get('effective_date')!r}")
            continue
        acc = (rec.get("account_id") or "").strip()
        accounts[acc] = (rec.get("account_type") or "").strip() or accounts.get(acc, "")
        type_, action, qty, net = _map(rec)
        currency = (rec.get("currency") or "").strip().upper() or None
        price = parse_qty_micro(rec.get("unit_price"))
        rows.append({
            "account_id": acc,
            # DECISION: dated by effective (trade) date. The export leaves
            # settlement blank for non-trades, and trade-date holdings value the
            # day correctly. Same-day legs of a transfer match on this date.
            "trade_date": d,
            "transaction_date": d,
            "settlement_date": parse_date(rec.get("settlement_date")),
            "type": type_,
            "action": action,
            "ticker": app_ticker(rec.get("symbol"), currency),
            "qty_micro": qty,
            "price_micro": price or None,
            "gross_amount_cents": None,
            "commission_cents": parse_money_cents(rec.get("commission")),
            "net_amount_cents": net,
            "currency": currency,
            "description": (rec.get("description") or "").strip() or None,
            "raw_json": json.dumps({"source": "ws_csv", "line": line_no, **rec}),
        })
    unknown = sorted({r["type"] for r in rows} - {
        "trades", "deposits", "withdrawals", "transfers", "fx conversion", "dividends", "interest", "fees and rebates",
    })
    if unknown:
        errors.append(f"unrecognized activity types (kept, flagged in reconciliation): {', '.join(unknown)}")
    return {
        "rows": rows,
        "errors": errors,
        "header_map": {f: f for f in fields},
        "unmapped_headers": [],
        "accounts": accounts,
        "source": BROKER,
    }
