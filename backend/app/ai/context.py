"""Assembles the live portfolio context block fed to the AI analyst. Pure reads
over services that already exist — holdings, performance series, attribution."""

import json
from datetime import date

from ..config import TZ, utcnow

DEFAULT_PROFILE = (
    "Aggressive growth investor, tech/semiconductor focused, based in Canada (accounts in CAD/USD). "
    "Core philosophy: with a small seed (under ~$50k), aggressive positioning is the rational play — "
    "the goal is not to avoid volatility but to survive it. Uses leveraged ETFs (SOXL, TQQQ) as core "
    "holdings deliberately, and makes their own decisions: wants sharp analysis, facts, event "
    "calendars, and market context — NOT trade recommendations or action items. Comfortable with "
    "drawdowns; allergic to boilerplate caution. Prefers responses in English."
)


async def build_portfolio_context(state) -> str:
    """Compact JSON snapshot of the whole portfolio for the system prompt."""
    parts: dict = {"as_of": utcnow().replace(tzinfo=None).isoformat(timespec="minutes") + "Z",
                   "local_date": date.today().isoformat(), "base_currency": "CAD"}
    try:
        holdings = await state.portfolio.holdings("CAD")
        parts["total_equity_cad"] = round(holdings.get("total_equity", 0), 2)
        parts["cash"] = holdings.get("cash_native")
        parts["holdings"] = [
            {
                "ticker": r["ticker"], "currency": r["currency"], "qty": r["qty"],
                "avg_cost": r["avg_cost"], "last": r["last"],
                "market_value_cad": round(r["market_value"] or 0, 2),
                "weight_pct": round(r["weight"] or 0, 1),
                "open_pnl_pct": round(r["open_pnl_pct"], 1) if r["open_pnl_pct"] is not None else None,
                "day_pnl_cad": round(r["day_pnl"], 2) if r["day_pnl"] is not None else None,
            }
            for r in holdings.get("rows", [])
        ]
    except Exception as exc:
        parts["holdings_error"] = str(exc)

    try:
        summary = await state.portfolio.summary("CAD")
        parts["performance"] = {
            "day_pnl_cad": round(summary.get("day_pnl") or 0, 2),
            "day_pnl_pct": round(summary.get("day_pnl_pct") or 0, 2),
            "mwr_since_inception": round(summary["mwr"], 4) if summary.get("mwr") is not None else None,
            "mwr_since": summary.get("mwr_since"),
            "twr_ytd": round(summary["twr_ytd"], 4) if summary.get("twr_ytd") is not None else None,
        }
    except Exception as exc:
        parts["performance_error"] = str(exc)

    try:
        from ..api.performance import performance_attribution, performance_series

        class _Req:  # minimal shim: these handlers only touch request.app.state
            def __init__(self, s):
                self.app = type("A", (), {"state": s})()

        req = _Req(state)
        series = await performance_series(req, range="all", mode="value", baseline="inception")
        if not series.get("empty"):
            s = series["stats"]
            parts["lifetime"] = {
                "capital_in_cad": series["capital_in"][-1],
                "portfolio_value_cad": series["value"][-1],
                "pnl_cad": series["pnl"][-1],
                "twr": round(s["twr"], 4) if s.get("twr") is not None else None,
                "max_drawdown": round(s["max_drawdown"], 4) if s.get("max_drawdown") is not None else None,
                "annualized_vol": round(s["vol"], 4) if s.get("vol") is not None else None,
                "since": series["start"],
            }
        attr = await performance_attribution(req, range="all")
        if not attr.get("empty"):
            parts["pnl_by_position_lifetime_cad"] = {
                r["ticker"]: round(r["pnl"], 0) for r in attr["rows"]
            }
    except Exception as exc:
        parts["history_error"] = str(exc)

    return json.dumps(parts, ensure_ascii=False, default=str)
