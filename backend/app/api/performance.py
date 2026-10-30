import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from ..models import Activity, CashLedgerRow, FxRate, HoldingsLedgerRow, SeriesDaily, Symbol
from ..services import candles as candle_svc
from ..services import history
from ..services.reconstruction import classify
from ..services.series import _FFill, apply_live_point, range_start, range_stats
from ..yf import to_yf_symbol

log = logging.getLogger(__name__)
router = APIRouter()

RANGES = ("1m", "3m", "6m", "1y", "all")
MAX_STACKED_POINTS = 400


async def _load_series(state) -> list[SeriesDaily]:
    async with state.sessions() as session:
        return list((await session.execute(select(SeriesDaily).order_by(SeriesDaily.date))).scalars())


async def _series_maps(state) -> dict | None:
    """series_daily as per-date maps with TODAY priced live — the dashboard's
    figure (current book × quotes, extended hours included), so the charts'
    last point moves with after-hours / pre-market trading instead of waiting
    for the next close. Display-only: the stored rows stay close-based."""
    rows = await _load_series(state)
    if len(rows) < 2:
        return None
    m = {
        "dates": [r.date for r in rows],
        "values": {r.date: r.portfolio_value_cad_cents or 0 for r in rows},
        "idx": {r.date: r.twr_index or 100.0 for r in rows},
        "flows": {r.date: r.flows_cad_cents or 0 for r in rows},
        "capital": {r.date: r.capital_in_cad_cents or 0 for r in rows},
        "fx_cum": {r.date: r.fx_gain_cad_cents or 0 for r in rows},  # cumulative FX gain since inception
        "ledger_end": rows[-1].date,  # last date with holdings/cash ledger rows
        "live_price": {},  # ticker -> native last price, set only when today is live
        "live_reason": "",
    }
    today, last = date.today(), rows[-1].date
    if last > today:
        m["live_reason"] = f"series end {last} is after today {today}"
        return m
    try:
        pos = await state.portfolio.positions()
        total = await state.portfolio.total_equity_cad()
    except Exception as exc:
        log.warning("live series point unavailable: %s", exc)
        m["live_reason"] = f"{type(exc).__name__}: {exc}"
        return m
    if pos is None or total is None:
        m["live_reason"] = "no current book"
        return m
    # DECISION: no recompute yet today (pre-market, weekend, before the 17:30
    # EOD job) → append today. Every import recomputes, so the book can't have
    # moved since the last row: carry capital and the cumulative FX gain, zero
    # flows — the gap is pure price, its FX share left unsplit.
    if apply_live_point(m["dates"], m["values"], m["idx"], m["flows"], today, round(total * 100)):
        m["capital"][today] = m["capital"][last]
        m["fx_cum"][today] = m["fx_cum"][last]
    m["live_price"] = {p["ticker"]: p["last"] for p in pos["rows"] if p["last"]}
    return m


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
    m = await _series_maps(state)
    if m is None:
        return {"empty": True, "reason": "no reconstructed history yet — run bootstrap in Settings"}

    all_dates, values, idx, flows = m["dates"], m["values"], m["idx"], m["flows"]
    start = range_start(all_dates, range)
    dates = [d for d in all_dates if d >= start]

    stats = range_stats(dates, values, idx, flows, await history.trading_days(state.sessions))
    base = {"empty": False, "range": range, "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
            "live": bool(m["live_price"]), "stats": stats}

    if mode == "indexed":
        base_idx = idx[dates[0]] or 100.0
        return {**base, "dates": [d.isoformat() for d in dates],
                "portfolio": [round(idx[d] / base_idx * 100, 4) for d in dates]}

    if mode == "stacked":
        return {**base, **(await _stacked(state, dates, m["ledger_end"], m["live_price"]))}

    # value mode. Two capital baselines:
    #   range     (§7 default): V(range start) counts as capital → band = P&L earned in the window
    #   inception (owner-requested): net deposits since data start → band = lifetime P&L, same line on every range
    cap_series, val_series, pnl_series = [], [], []
    stored = m["capital"]
    if baseline == "inception":
        for d in dates:
            cap_series.append(stored[d] / 100)
            val_series.append(values[d] / 100)
            pnl_series.append((values[d] - stored[d]) / 100)
    else:
        # Window baseline: the portfolio value at range start counts as capital, so
        # the band = P&L earned within the window. EXCEPTION: when the window reaches
        # back to inception (range covers the first series date) there is no starting
        # stake — the account began empty and the deposits ARE the capital. Using the
        # day-one reconstructed value there understates capital by the day-one opening
        # residual (split/FX noise), pushing capital-in below true net deposits; fall
        # back to the flows-based capital so all-time Window P&L is identical to
        # Lifetime P&L and capital-in equals net deposits exactly (learned 2026-09-21).
        capital =stored[dates[0]] if dates[0] == all_dates[0] else values[dates[0]]
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
    # Split the window's P&L into currency-neutral investment gain vs FX translation.
    fx_cum = m["fx_cum"]
    fx_pnl = (fx_cum.get(dates[-1], 0) - fx_cum.get(dates[0], 0)) / 100
    return {**base, "baseline": baseline, "dates": [d.isoformat() for d in dates], "value": val_series,
            "capital_in": cap_series, "pnl": pnl_series,
            "fx_pnl": fx_pnl, "investment_pnl": pnl_series[-1] - fx_pnl,
            "flows": [{"date": e["date"], "amount": e["amount_cents"] / 100,
                       "currency": e["currency"], "kind": e["kind"]} for e in events],
            "opening_value": values[dates[0]] / 100}


async def _stacked(state, dates, ledger_end: date, live_price: dict[str, float]) -> dict:
    """Per-position CAD values. A live-appended today (past `ledger_end`) reuses
    the last ledger quantities; today's prices are the live quotes."""
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
    today = date.today()
    for d in sampled:
        rate = fx.at(d, flat_backfill=True) or 1_000_000
        book_d = min(d, ledger_end)
        for t in tickers:
            q = qty_by_date.get(book_d, {}).get(t, 0)
            if q and d == today and t in live_price:
                close = round(live_price[t] * 100)
            else:
                close = closes[t].at(d, flat_backfill=True) if q else None
            if not q or close is None:
                series[t].append(0.0)
                continue
            native_cents = q * close // 1_000_000
            cur = sym_rows[t].currency if t in sym_rows and sym_rows[t].currency else "CAD"
            cad = native_cents * rate // 1_000_000 if cur == "USD" else native_cents
            series[t].append(cad / 100)
        cash_cad = 0
        for cur, cents in cash_by_date.get(book_d, {}).items():
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
    m = await _series_maps(state)
    if m is None:
        return {"empty": True}
    all_dates = m["dates"]
    start = range_start(all_dates, range)
    dates = [d for d in all_dates if d >= start]
    a = await _attribute(state, m, dates[0], dates[-1])
    del a["trades"]
    return {"empty": False, "range": range, **a}


async def _attribute(state, m: dict, s: date, e: date) -> dict:
    """Window attribution (s, e] over the live-priced series maps — shared by the
    range table and the single-day breakdown."""
    all_dates = m["dates"]
    dates = [d for d in all_dates if s <= d <= e]
    values, flows, fx_cum = m["values"], m["flows"], m["fx_cum"]

    # The window always ends at the series' last point, which _series_maps prices
    # live when it is today (same source as the dashboard, extended hours
    # included) so after-hours moves land in each ticker's row instead of pooling
    # in "other". PRICES are live but QUANTITIES stay ledger-based — mixing live
    # position sizes in would corrupt the rows whenever today's trades haven't
    # been imported yet. Falls back to official closes when live pricing fails.
    live_price = {t: round(p * 100) for t, p in m["live_price"].items()} if e == all_dates[-1] else {}
    live_reason = m["live_reason"]

    # Window total. The capital base is the value at range start, EXCEPT when the
    # window reaches inception (account began empty) — then NET DEPOSITS are the base,
    # so the all-time total matches the Lifetime chart and true net deposits exactly
    # rather than the day-one reconstructed value (learned 2026-09-21). A live end
    # value is the actual live portfolio total — the figure the dashboard shows — so
    # the Total reconciles to the headline; per-position rows stay ledger-based, and
    # the gap between the ledger row sum and the authoritative total lands in "other".
    stored_capital = m["capital"]
    window_flows = sum(flows.get(d, 0) for d in dates[1:])
    baseline_capital = stored_capital[s] if s == all_dates[0] else values[s]
    total_pnl = values[e] - baseline_capital - window_flows

    async with state.sessions() as session:
        h_start = {r.ticker: r.qty_micro for r in (await session.execute(
            select(HoldingsLedgerRow).where(HoldingsLedgerRow.date == s))).scalars()}
        h_end = {r.ticker: r.qty_micro for r in (await session.execute(
            select(HoldingsLedgerRow).where(HoldingsLedgerRow.date == min(e, m["ledger_end"])))).scalars()}
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
    trade_list = []
    for a in acts:
        kind = classify({"type": a.type, "action": a.action, "ticker": a.ticker, "qty_micro": a.qty_micro})
        net = to_cad(a.net_amount_cents or 0, a.currency, a.transaction_date)
        if kind == "trade" and a.ticker:
            trades[a.ticker] = trades.get(a.ticker, 0) + net
            trade_list.append({"date": a.transaction_date.isoformat(), "ticker": a.ticker, "action": a.action,
                               "qty": abs(a.qty_micro or 0) / 1_000_000,
                               "price": (a.price_micro or 0) / 1_000_000, "currency": a.currency})
        elif kind == "dividend" and a.ticker:
            dividends[a.ticker] = dividends.get(a.ticker, 0) + net
        elif kind in ("interest", "fee"):
            interest_fees += net

    tickers = sorted(set(h_start) | set(h_end) | set(trades) | set(dividends))
    closes = {t: _FFill(await candle_svc.daily_closes_cents(state.sessions, t)) for t in tickers}

    def cur_of(t: str) -> str:
        return sym_rows[t].currency if t in sym_rows and sym_rows[t].currency else "CAD"

    def price(t: str, on) -> int | None:  # native cents
        if on == e and t in live_price:
            return live_price[t]
        return closes[t].at(on, flat_backfill=True)

    def mv(t: str, qty: int, on) -> int:
        p = price(t, on) if qty else None
        return to_cad(qty * p // 1_000_000, cur_of(t), on) if p is not None else 0

    out = []
    attributed = 0
    for t in tickers:
        mv_s, mv_e = mv(t, h_start.get(t, 0), s), mv(t, h_end.get(t, 0), e)
        pnl = mv_e - mv_s + trades.get(t, 0) + dividends.get(t, 0)
        attributed += pnl
        p_s, p_e = price(t, s), price(t, e)
        out.append({
            "ticker": t,
            "currency": cur_of(t),
            "qty_start": h_start.get(t, 0) / 1_000_000,
            "qty_end": h_end.get(t, 0) / 1_000_000,
            "price_start": p_s / 100 if p_s is not None else None,
            "price_end": p_e / 100 if p_e is not None else None,
            "mv_start": mv_s / 100,
            "mv_end": mv_e / 100,
            "invested_net": -trades.get(t, 0) / 100,  # positive = net money put in
            "dividends": dividends.get(t, 0) / 100,
            "pnl": pnl / 100,
        })
    out.sort(key=lambda r: -r["pnl"])
    other = total_pnl - attributed - interest_fees
    # Orthogonal split of the SAME total: how much of the CAD P&L came from USD/CAD
    # moving (fx_pnl) vs the securities themselves (investment_pnl). Independent of
    # the per-position rows, which already carry each position's FX inside its CAD P&L.
    fx_pnl = fx_cum.get(e, 0) - fx_cum.get(s, 0)
    return {
        "start": s.isoformat(), "end": e.isoformat(),
        "live": bool(live_price),
        "live_reason": live_reason,
        "rows": out,
        "trades": trade_list,
        "interest_fees": interest_fees / 100,
        "other": other / 100,  # unresolved rows, seam scaling (FX spread now split out below)
        "total_pnl": total_pnl / 100,
        "fx_pnl": fx_pnl / 100,
        "investment_pnl": (total_pnl - fx_pnl) / 100,
        "opening_value": values[s] / 100,
        "closing_value": values[e] / 100,
        "flows": window_flows / 100,
    }


@router.get("/performance/day")
async def performance_day(request: Request, date_: date = Query(..., alias="date"), prev: date | None = None):
    """One session's breakdown for the Best/Worst day cards: the portfolio move,
    each position's contribution, the day's trades and the benchmarks/USDCAD for
    market context. `prev` = the previous session (from the stats); defaults to
    the prior series date."""
    state = request.app.state
    m = await _series_maps(state)
    if m is None or date_ not in m["values"]:
        raise HTTPException(404, f"no series point on {date_}")
    earlier = [d for d in m["dates"] if d < date_]
    if not earlier:
        raise HTTPException(404, f"{date_} is the first series day")
    s = prev if prev is not None and prev in m["values"] and prev < date_ else earlier[-1]
    a = await _attribute(state, m, s, date_)
    a["rows"] = [r for r in a["rows"] if r["mv_start"] or r["mv_end"] or r["pnl"]]
    a["rows"].sort(key=lambda r: -abs(r["pnl"]))
    r = m["idx"][date_] / m["idx"][s] - 1

    async with state.sessions() as session:
        fx_rows = (await session.execute(
            select(FxRate).where(FxRate.date <= date_).order_by(FxRate.date.desc()).limit(10))).scalars().all()
        sym_rows = {sym.ticker: sym for sym in (await session.execute(select(Symbol))).scalars()}
    fx = _FFill({x.date: x.usdcad_micro for x in fx_rows})
    fx_s, fx_e = fx.at(s, flat_backfill=True), fx.at(date_, flat_backfill=True)

    market = []
    for b in await history.get_setting(state.sessions, "benchmarks", history.DEFAULT_BENCHMARKS):
        yf_sym = to_yf_symbol(b, sym_rows[b].exchange if b in sym_rows else None)
        try:
            closes = _FFill(await state.yf.daily_closes(yf_sym, s - timedelta(days=10)))
        except Exception as exc:
            log.info("benchmark %s unavailable for %s: %s", b, date_, exc)
            continue
        c_s, c_e = closes.at(s, flat_backfill=True), closes.at(date_, flat_backfill=True)
        if c_s and c_e:
            market.append({"symbol": b, "r": c_e / c_s - 1})
    return {
        "date": date_.isoformat(), "prev": s.isoformat(), "r": r, **a,
        "market": market,
        "usdcad": {"start": fx_s / 1e6, "end": fx_e / 1e6} if fx_s and fx_e else None,
    }


@router.get("/performance/compare")
async def performance_compare(request: Request, symbols: str = Query(...), range: str = Query("1y")):
    if range not in RANGES:
        raise HTTPException(400, f"range must be one of {RANGES}")
    state = request.app.state
    m = await _series_maps(state)  # same dates as the portfolio line, incl. a live today
    if m is None:
        return {"empty": True, "series": []}
    all_dates = m["dates"]
    start = range_start(all_dates, range)
    dates = [d for d in all_dates if d >= start]
    live_on = dates[-1] if m["live_price"] else None

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
        if live_on:  # benchmarks get the same live (extended-hours) pricing as the portfolio's today
            quote = (await state.yf.quotes([yf_sym])).get(yf_sym)
            if quote:
                closes = {**closes, live_on: quote["last"]}
        ff = _FFill(closes)
        base = ff.at(dates[0], flat_backfill=True)
        if not base:
            out.append({"symbol": raw, "error": "no data in range", "indexed": None})
            continue
        out.append({"symbol": raw,
                    "indexed": [round((ff.at(d, flat_backfill=True) or base) / base * 100, 4) for d in dates]})
    return {"empty": False, "range": range, "dates": [d.isoformat() for d in dates], "series": out}
