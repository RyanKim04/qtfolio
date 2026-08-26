from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from ..models import Activity, CashLedgerRow, FxRate, HoldingsLedgerRow, SeriesDaily, Symbol
from ..services import candles as candle_svc
from ..services import history
from ..services.reconstruction import classify
from ..services.series import _FFill, range_start, range_stats
from ..yf import to_yf_symbol

router = APIRouter()

RANGES = ("1m", "3m", "6m", "1y", "all")
MAX_STACKED_POINTS = 400


async def _load_series(state) -> list[SeriesDaily]:
    async with state.sessions() as session:
        return list((await session.execute(select(SeriesDaily).order_by(SeriesDaily.date))).scalars())


@router.get("/performance/series")
async def performance_series(
    request: Request,
    range: str = Query("1y"),
    mode: str = Query("value"),
    baseline: str = Query("range", pattern="^(range|inception)$"),
):
    if range not in RANGES:
        raise HTTPException(400, f"range must be one of {RANGES}")
    state = request.app.state
    rows = await _load_series(state)
    if len(rows) < 2:
        return {"empty": True, "reason": "no reconstructed history yet — run bootstrap in Settings"}

    all_dates = [r.date for r in rows]
    values = {r.date: r.portfolio_value_cad_cents or 0 for r in rows}
    idx = {r.date: r.twr_index or 100.0 for r in rows}
    flows = {r.date: r.flows_cad_cents or 0 for r in rows}
    start = range_start(all_dates, range)
    dates = [d for d in all_dates if d >= start]

    stats = range_stats(dates, values, idx, flows)
    base = {"empty": False, "range": range, "start": dates[0].isoformat(), "end": dates[-1].isoformat(), "stats": stats}

    if mode == "indexed":
        base_idx = idx[dates[0]] or 100.0
        return {**base, "dates": [d.isoformat() for d in dates],
                "portfolio": [round(idx[d] / base_idx * 100, 4) for d in dates]}

    if mode == "stacked":
        return {**base, **(await _stacked(state, dates))}

    # value mode. Two capital baselines:
    #   range     (§7 default): V(range start) counts as capital → band = P&L earned in the window
    #   inception (owner-requested): net deposits since data start → band = lifetime P&L, same line on every range
    cap_series, val_series, pnl_series = [], [], []
    if baseline == "inception":
        stored = {r.date: r.capital_in_cad_cents or 0 for r in rows}
        for d in dates:
            cap_series.append(stored[d] / 100)
            val_series.append(values[d] / 100)
            pnl_series.append((values[d] - stored[d]) / 100)
    else:
        capital = values[dates[0]]
        for i, d in enumerate(dates):
            if i:
                capital += flows.get(d, 0)
            cap_series.append(capital / 100)
            val_series.append(values[d] / 100)
            pnl_series.append((values[d] - capital) / 100)
    events = [
        e for e in (await history.get_setting(state.sessions, "flow_events", []) or [])
        if dates[0].isoformat() <= e["date"] <= dates[-1].isoformat()
    ]
    return {**base, "baseline": baseline, "dates": [d.isoformat() for d in dates], "value": val_series,
            "capital_in": cap_series, "pnl": pnl_series,
            "flows": [{"date": e["date"], "amount": e["amount_cents"] / 100,
                       "currency": e["currency"], "kind": e["kind"]} for e in events],
            "opening_value": values[dates[0]] / 100}


async def _stacked(state, dates) -> dict:
    async with state.sessions() as session:
        h_rows = (
            await session.execute(
                select(HoldingsLedgerRow).where(HoldingsLedgerRow.date >= dates[0], HoldingsLedgerRow.date <= dates[-1])
            )
        ).scalars().all()
        c_rows = (
            await session.execute(
                select(CashLedgerRow).where(CashLedgerRow.date >= dates[0], CashLedgerRow.date <= dates[-1])
            )
        ).scalars().all()
        sym_rows = {s.ticker: s for s in (await session.execute(select(Symbol))).scalars()}
    from ..models import FxRate
    async with state.sessions() as session:
        fx_rows = (await session.execute(select(FxRate))).scalars().all()
    fx = _FFill({r.date: r.usdcad_micro for r in fx_rows})

    tickers = sorted({r.ticker for r in h_rows})
    closes = {t: _FFill(await candle_svc.daily_closes_cents(state.sessions, t)) for t in tickers}
    qty_by_date: dict = {}
    for r in h_rows:
        qty_by_date.setdefault(r.date, {})[r.ticker] = r.qty_micro
    cash_by_date: dict = {}
    for r in c_rows:
        cash_by_date.setdefault(r.date, {})[r.currency] = r.balance_cents

    step = max(1, len(dates) // MAX_STACKED_POINTS)
    sampled = dates[::step]
    if sampled[-1] != dates[-1]:
        sampled.append(dates[-1])

    series = {t: [] for t in tickers}
    cash_series = []
    for d in sampled:
        rate = fx.at(d, flat_backfill=True) or 1_000_000
        for t in tickers:
            q = qty_by_date.get(d, {}).get(t, 0)
            close = closes[t].at(d, flat_backfill=True) if q else None
            if not q or close is None:
                series[t].append(0.0)
                continue
            native_cents = q * close // 1_000_000
            cur = sym_rows[t].currency if t in sym_rows and sym_rows[t].currency else "CAD"
            cad = native_cents * rate // 1_000_000 if cur == "USD" else native_cents
            series[t].append(cad / 100)
        cash_cad = 0
        for cur, cents in cash_by_date.get(d, {}).items():
            cash_cad += cents * rate // 1_000_000 if cur == "USD" else cents
        cash_series.append(cash_cad / 100)

    out = [{"ticker": t, "values": v} for t, v in series.items() if any(v)]
    out.sort(key=lambda s: -s["values"][-1])
    out.append({"ticker": "Cash", "values": cash_series})
    return {"dates": [d.isoformat() for d in sampled], "series": out}


@router.get("/performance/attribution")
async def performance_attribution(request: Request, range: str = Query("1y")):
    """Per-position P&L over the selected window: (MV_end − MV_start) + trade
    cash flows + dividends, in CAD. Interest/fees and FX residue get their own
    buckets so the table reconciles exactly to the chart's window P&L."""
    if range not in RANGES:
        raise HTTPException(400, f"range must be one of {RANGES}")
    state = request.app.state
    rows = await _load_series(state)
    if len(rows) < 2:
        return {"empty": True}
    all_dates = [r.date for r in rows]
    start = range_start(all_dates, range)
    dates = [d for d in all_dates if d >= start]
    s, e = dates[0], dates[-1]
    values = {r.date: r.portfolio_value_cad_cents or 0 for r in rows}
    flows = {r.date: r.flows_cad_cents or 0 for r in rows}

    # When the window ends TODAY, price the end at live quotes (same source as
    # the dashboard, extended hours included) so after-hours moves land in each
    # ticker's row instead of pooling in "other". PRICES are live but QUANTITIES
    # stay ledger-based — mixing live position sizes in would corrupt the rows
    # whenever today's trades haven't posted to the activities API yet (§4.2 lag).
    # Historical windows stay on official closes; falls back silently on failure.
    live_price: dict[str, int] = {}  # ticker -> native price in cents
    live_reason = ""
    if e == date.today():
        try:
            h = await state.portfolio.holdings("CAD")
            for r in h.get("rows", []):
                if r.get("last"):
                    live_price[r["ticker"]] = round(r["last"] * 100)
            if not live_price:
                live_reason = f"holdings empty={h.get('empty')} rows={len(h.get('rows', []))}"
        except Exception as exc:
            live_reason = f"{type(exc).__name__}: {exc}"
    else:
        live_reason = f"end {e} != today {date.today()}"

    total_pnl = values[e] - values[s] - sum(flows.get(d, 0) for d in dates[1:])  # provisional; re-derived below if live

    async with state.sessions() as session:
        h_start = {r.ticker: r.qty_micro for r in (await session.execute(
            select(HoldingsLedgerRow).where(HoldingsLedgerRow.date == s))).scalars()}
        h_end = {r.ticker: r.qty_micro for r in (await session.execute(
            select(HoldingsLedgerRow).where(HoldingsLedgerRow.date == e))).scalars()}
        cash_end = {r.currency: r.balance_cents for r in (await session.execute(
            select(CashLedgerRow).where(CashLedgerRow.date == e))).scalars()}
        acts = list((await session.execute(
            select(Activity).where(Activity.transaction_date > s, Activity.transaction_date <= e))).scalars())
        sym_rows = {sym.ticker: sym for sym in (await session.execute(select(Symbol))).scalars()}
        fx_rows = (await session.execute(select(FxRate))).scalars().all()
    fx = _FFill({r.date: r.usdcad_micro for r in fx_rows})

    def to_cad(cents: int, currency: str | None, on) -> int:
        if currency == "USD":
            return cents * (fx.at(on, flat_backfill=True) or 1_000_000) // 1_000_000
        return cents

    trades: dict[str, int] = {}
    dividends: dict[str, int] = {}
    interest_fees = 0
    for a in acts:
        kind = classify({"type": a.type, "action": a.action, "ticker": a.ticker, "qty_micro": a.qty_micro})
        net = to_cad(a.net_amount_cents or 0, a.currency, a.transaction_date)
        if kind == "trade" and a.ticker:
            trades[a.ticker] = trades.get(a.ticker, 0) + net
        elif kind == "dividend" and a.ticker:
            dividends[a.ticker] = dividends.get(a.ticker, 0) + net
        elif kind in ("interest", "fee"):
            interest_fees += net

    tickers = sorted(set(h_start) | set(h_end) | set(trades) | set(dividends))
    closes = {t: _FFill(await candle_svc.daily_closes_cents(state.sessions, t)) for t in tickers}

    def mv(t: str, qty: int, on) -> int:
        if not qty:
            return 0
        cur = sym_rows[t].currency if t in sym_rows and sym_rows[t].currency else "CAD"
        if on == e and t in live_price:
            return to_cad(qty * live_price[t] // 1_000_000, cur, on)
        close = closes[t].at(on, flat_backfill=True)
        if close is None:
            return 0
        return to_cad(qty * close // 1_000_000, cur, on)

    if live_price:
        # Re-derive the window total on the same basis as the rows: ledger
        # quantities at live prices, plus ledger cash — internally consistent,
        # so "other" keeps meaning only FX spread / unresolved rows.
        end_value = sum(mv(t, h_end.get(t, 0), e) for t in set(h_end)) + sum(
            to_cad(c, cur, e) for cur, c in cash_end.items()
        )
        total_pnl = end_value - values[s] - sum(flows.get(d, 0) for d in dates[1:])

    out = []
    attributed = 0
    for t in tickers:
        mv_s, mv_e = mv(t, h_start.get(t, 0), s), mv(t, h_end.get(t, 0), e)
        pnl = mv_e - mv_s + trades.get(t, 0) + dividends.get(t, 0)
        attributed += pnl
        out.append({
            "ticker": t,
            "mv_start": mv_s / 100,
            "mv_end": mv_e / 100,
            "invested_net": -trades.get(t, 0) / 100,  # positive = net money put in
            "dividends": dividends.get(t, 0) / 100,
            "pnl": pnl / 100,
        })
    out.sort(key=lambda r: -r["pnl"])
    other = total_pnl - attributed - interest_fees
    return {
        "empty": False, "range": range, "start": s.isoformat(), "end": e.isoformat(),
        "live": bool(live_price),
        "live_reason": live_reason,
        "rows": out,
        "interest_fees": interest_fees / 100,
        "other": other / 100,  # FX conversion spread, unresolved rows, seam scaling
        "total_pnl": total_pnl / 100,
    }


@router.get("/performance/compare")
async def performance_compare(request: Request, symbols: str = Query(...), range: str = Query("1y")):
    if range not in RANGES:
        raise HTTPException(400, f"range must be one of {RANGES}")
    state = request.app.state
    rows = await _load_series(state)
    if len(rows) < 2:
        return {"empty": True, "series": []}
    all_dates = [r.date for r in rows]
    start = range_start(all_dates, range)
    dates = [d for d in all_dates if d >= start]

    async with state.sessions() as session:
        sym_rows = {s.ticker: s for s in (await session.execute(select(Symbol))).scalars()}

    out = []
    for raw in [s.strip().upper() for s in symbols.split(",") if s.strip()][:12]:
        exchange = sym_rows[raw].exchange if raw in sym_rows else None
        yf_sym = to_yf_symbol(raw, exchange)
        closes = await state.yf.daily_closes(yf_sym, start - timedelta(days=14))
        if not closes:
            out.append({"symbol": raw, "error": "source unavailable", "indexed": None})
            continue
        ff = _FFill(closes)
        base = ff.at(dates[0], flat_backfill=True)
        if not base:
            out.append({"symbol": raw, "error": "no data in range", "indexed": None})
            continue
        out.append({"symbol": raw,
                    "indexed": [round((ff.at(d, flat_backfill=True) or base) / base * 100, 4) for d in dates]})
    return {"empty": False, "range": range, "dates": [d.isoformat() for d in dates], "series": out}
