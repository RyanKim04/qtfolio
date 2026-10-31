"""Orchestration for the risk analytics: builds the return panel from the
current book and cached closes, runs the pure modules in a worker thread and
caches each result until the book or the day changes.
"""

import asyncio
import logging
import math
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ...config import utcnow
from ...fx import FxService
from ...models import Activity, SeriesDaily, Symbol
from ...yf import YfService, to_yf_symbol
from .. import history
from ..reconstruction import classify, signed_qty_micro
from . import causal, counterfactual, covariance, factors, stress
from . import var as var_mod
from .data import FX_SYMBOL, HISTORY_START, Holding, Panel, build_panel, complete_rows, fill_missing, market_symbols

log = logging.getLogger(__name__)

WINDOWS = {"6m": 126, "1y": 252, "2y": 504}
PANEL_TTL = 15 * 60
ANN = 252
# Default intervention sizes per driver, in display units (bp / % / points):
# roughly a bad week for each.
STRESS_UNITS = {"rates": 50.0, "oil": -20.0, "usd": 5.0, "credit": -3.0, "vix": 15.0, "equity": -10.0, "usdcad": 5.0}


def to_internal(unit: str, v: float) -> float:
    return v / 100 if unit == "pct" else v


def to_display(unit: str, v: float) -> float:
    return v * 100 if unit == "pct" else v


class Prepared:
    """A window of the panel with short histories proxy-filled and incomplete
    sessions dropped, so every estimator sees the same rows."""

    def __init__(self, panel: Panel, window: int):
        P = panel.tail(window)
        R, share = fill_missing(P.R, P.F)
        ok = complete_rows(R, P.F, P.D)
        self.panel = P
        self.dates = [d for d, k in zip(P.dates, ok) if k]
        self.R, self.F, self.D = R[ok], P.F[ok], P.D[ok]
        self.w = P.weights
        self.share = share
        k = P.factor_keys
        mkt, fx, can = self.F[:, k.index("mkt")], self.F[:, k.index("usdcad")], self.F[:, k.index("canada")]
        self.spy_cad = (1 + mkt) * (1 + fx) - 1
        self.xiu = can + mkt  # XIU.TO is CAD-listed: canada = XIU − SPY

    @property
    def rp(self) -> np.ndarray:
        return self.R @ self.w

    def as_panel(self) -> Panel:
        P = self.panel
        return Panel(self.dates, P.assets, self.R, P.weights, P.factor_keys, P.factor_labels, self.F,
                     P.driver_keys, P.driver_labels, P.driver_units, self.D, P.total_cad, P.currencies,
                     P.yf_symbols, P.closes)


def _beta(y: np.ndarray, x: np.ndarray) -> float | None:
    v = float(np.var(x, ddof=1))
    return float(np.cov(y, x, ddof=1)[0, 1] / v) if v else None


def _f(x) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


class RiskService:
    def __init__(self, sessions: async_sessionmaker, yf: YfService, fx: FxService, portfolio):
        self._sessions = sessions
        self._yf = yf
        self._fx = fx
        self._portfolio = portfolio
        self._panel: tuple[tuple, float, Panel] | None = None
        self._results: dict[tuple, object] = {}
        self._lock = asyncio.Lock()

    # -- data ------------------------------------------------------------

    async def _symbols(self) -> dict[str, Symbol]:
        async with self._sessions() as session:
            return {s.ticker: s for s in (await session.execute(select(Symbol))).scalars()}

    async def closes(self, symbols: list[str], start: date = HISTORY_START) -> dict[str, dict[date, float]]:
        sem = asyncio.Semaphore(4)

        async def one(sym):
            async with sem:
                try:
                    return sym, await self._yf.ensure_daily_closes(sym, start)
                except Exception as exc:  # never crash a page on one symbol
                    log.warning("closes for %s unavailable: %s", sym, exc)
                    return sym, {}

        return dict(await asyncio.gather(*(one(s) for s in symbols)))

    async def _book(self) -> tuple[list[Holding], float, float] | None:
        pos = await self._portfolio.positions()
        if pos is None:
            return None
        latest = await self._fx.latest()
        rate = float(latest[1]) if latest else 1.0
        syms = await self._symbols()
        rows = []
        for p in pos["rows"]:
            cad = p["mv"] * (rate if p["currency"] == "USD" else 1.0)
            ex = syms[p["ticker"]].exchange if p["ticker"] in syms else None
            rows.append((p["ticker"], to_yf_symbol(p["ticker"], ex), p["currency"], cad))
        usd_cash = pos["cash"]["USD"] * rate
        total = sum(r[3] for r in rows) + pos["cash"]["CAD"] + usd_cash
        if total <= 0:
            return None
        holdings = [Holding(t, y, c, v / total, v) for t, y, c, v in rows if v > 0]
        return holdings, usd_cash / total, total

    async def panel(self) -> Panel | None:
        async with self._lock:
            book = await self._book()
            if book is None:
                return None
            holdings, usd_w, total = book
            key = (date.today(), tuple((h.ticker, round(h.weight, 3)) for h in holdings), round(usd_w, 3))
            now = utcnow().timestamp()
            if self._panel and self._panel[0] == key and now - self._panel[1] < PANEL_TTL:
                return self._panel[2]
            syms = sorted(set(market_symbols()) | {h.yf_symbol for h in holdings})
            closes = await self.closes(syms)
            panel = await asyncio.to_thread(build_panel, closes, holdings, usd_w, total)
            self._panel = (key, now, panel)
            self._results.clear()
            return panel

    async def _cached(self, name: str, params: tuple, fn):
        panel = await self.panel()
        if panel is None:
            return None
        key = (name, params)
        if key not in self._results:
            self._results[key] = await asyncio.to_thread(fn, panel)
        return self._results[key]

    async def realized(self) -> tuple[list[date], np.ndarray]:
        """Stored TWR index on market sessions (close-based, no live point)."""
        from ..series import trading_sample

        async with self._sessions() as session:
            rows = list((await session.execute(select(SeriesDaily).order_by(SeriesDaily.date))).scalars())
        if len(rows) < 3:
            return [], np.array([])
        dates = trading_sample([r.date for r in rows], await history.trading_days(self._sessions))
        idx = {r.date: r.twr_index or 100.0 for r in rows}
        return dates, np.array([idx[d] for d in dates])

    # -- analyses --------------------------------------------------------

    async def summary(self) -> dict | None:
        def run(panel: Panel) -> dict:
            p1, p2 = Prepared(panel, WINDOWS["1y"]), Prepared(panel, WINDOWS["2y"])
            w = p1.w
            S_lw = covariance.ledoit_wolf(p1.R)
            S_ewma = covariance.ewma_cov(p2.R)
            vol_d = math.sqrt(max(float(w @ S_ewma @ w), 0.0))
            tab95 = var_mod.var_table(p2.rp, vol_d, 0.95)
            tab975 = var_mod.var_table(p2.rp, vol_d, 0.975)
            eu = covariance.euler(w, S_lw)
            enb, _ = covariance.enb_torsion(w, S_lw)
            betas = [_beta(p1.R[:, j], p1.spy_cad) for j in range(len(w))]
            invested = float(w.sum())
            return {
                "as_of": p2.dates[-1].isoformat(), "n": len(p2.dates), "total_cad": panel.total_cad,
                "vol": vol_d * math.sqrt(ANN),
                "var95": tab95["fhs"]["var"], "es975": tab975["fhs"]["es"],
                "beta_spy": _f(_beta(p1.rp, p1.spy_cad)), "beta_xiu": _f(_beta(p1.rp, p1.xiu)),
                "enb": _f(enb), "effective_n": _f(covariance.herfindahl_n(w)),
                "diversification_ratio": _f(covariance.diversification_ratio(w, S_lw)),
                "invested": invested,
                "holdings": [
                    {"asset": a, "weight": float(w[j]), "beta_spy": _f(betas[j]), "risk_pct": float(eu["pct"][j]),
                     "proxied": float(p1.share[j])}
                    for j, a in enumerate(panel.assets)
                ],
            }

        return await self._cached("summary", (), run)

    async def covariance(self, method: str, window: str) -> dict | None:
        def run(panel: Panel) -> dict:
            p = Prepared(panel, WINDOWS[window])
            S = covariance.estimate(p.R, method, p.F)
            C = covariance.corr_from_cov(S)
            order = covariance.cluster_order(C)
            cut = np.quantile(p.spy_cad, 0.10)
            calm = p.spy_cad > cut
            Cs = np.corrcoef(p.R[~calm], rowvar=False).reshape(C.shape) if (~calm).sum() > 5 else C
            Cn = np.corrcoef(p.R[calm], rowvar=False).reshape(C.shape)
            eu = covariance.euler(p.w, S)
            enb_t, bets = covariance.enb_torsion(p.w, S)
            a = math.sqrt(ANN)
            iu = np.triu_indices(len(order), 1)

            def avg(M):
                return float(np.nanmean(M[iu])) if len(iu[0]) else None

            return {
                "method": method, "window": window, "n": len(p.dates),
                "start": p.dates[0].isoformat(), "end": p.dates[-1].isoformat(),
                "assets": [panel.assets[i] for i in order],
                "corr": [[round(float(C[i, j]), 4) for j in order] for i in order],
                "stress_corr": [[round(float(Cs[i, j]), 4) for j in order] for i in order],
                "avg_corr": avg(Cn), "avg_stress_corr": avg(Cs), "stress_days": int((~calm).sum()),
                "vol": eu["vol"] * a,
                "diversification_ratio": _f(covariance.diversification_ratio(p.w, S)),
                "enb_pca": _f(covariance.enb_pca(p.w, S)), "enb_torsion": _f(enb_t),
                "effective_n": _f(covariance.herfindahl_n(p.w)),
                "rows": [
                    {"asset": panel.assets[j], "weight": float(p.w[j]), "vol": float(math.sqrt(S[j, j]) * a),
                     "beta": float(eu["beta"][j]), "mctr": float(eu["mctr"][j] * a), "ctr": float(eu["ctr"][j] * a),
                     "pct": float(eu["pct"][j]), "bet_share": float(bets[j]), "proxied": float(p.share[j])}
                    for j in range(len(p.w))
                ],
            }

        return await self._cached("covariance", (method, window), run)

    async def var(self, conf: float, horizon: int) -> dict | None:
        r_dates, r_idx = await self.realized()

        def run(panel: Panel) -> dict:
            p = Prepared(panel, WINDOWS["2y"])
            S = covariance.ewma_cov(p.R)
            vol = math.sqrt(max(float(p.w @ S @ p.w), 0.0))
            tab = var_mod.var_table(p.rp, vol, conf, horizon)
            h = math.sqrt(horizon)
            cvar = var_mod.component_var(p.w, S, conf) * h
            ces = var_mod.component_es(p.R, p.w, conf) * h
            counts, edges = np.histogram(p.rp, bins=50)
            bt = None
            if len(r_idx) > 300:
                rr = r_idx[1:] / r_idx[:-1] - 1
                rd = r_dates[1:]
                keep = min(len(rr), 750)
                rr, rd = rr[-keep:], rd[-keep:]
                bt = {m: var_mod.backtest(rr, conf, method=m) for m in ("fhs", "historical")}
                win = 250
                bt["dates"] = [d.isoformat() for d in rd[win:]]
                bt["returns"] = [float(x) for x in rr[win:]]
            return {
                "conf": conf, "horizon": horizon, "n": len(p.dates), "total_cad": panel.total_cad,
                "vol": vol * math.sqrt(ANN),
                "methods": {k: {kk: _f(vv) for kk, vv in v.items()} for k, v in tab.items()},
                "components": [{"asset": a, "var": float(cvar[j]), "es": float(ces[j]), "weight": float(p.w[j])}
                               for j, a in enumerate(panel.assets)],
                "hist": {"edges": edges.tolist(), "counts": counts.tolist()},
                "backtest": bt,
            }

        return await self._cached("var", (conf, horizon, len(r_idx)), run)

    async def factors(self, window: str) -> dict | None:
        def run(panel: Panel) -> dict:
            p = Prepared(panel, WINDOWS[window])
            return {
                "window": window, "n": len(p.dates),
                "factors": [{"key": k, "label": lbl} for k, lbl in zip(panel.factor_keys, panel.factor_labels)],
                "holdings": factors.exposures(p.R, p.F, panel.assets, panel.factor_keys),
                "portfolio": factors.risk_split(p.R, p.F, p.w, panel.factor_keys),
            }

        return await self._cached("factors", (window,), run)

    async def stress(self) -> dict | None:
        def run(panel: Panel) -> dict:
            p = Prepared(panel, WINDOWS["2y"])
            return {
                "total_cad": panel.total_cad,
                "scenarios": stress.historical(panel, panel.tail(WINDOWS["2y"])),
                "monte_carlo": stress.monte_carlo(p.R, p.w),
                "drivers": self._driver_meta(panel),
            }

        return await self._cached("stress", (), run)

    @staticmethod
    def _driver_meta(panel: Panel) -> list[dict]:
        return [{"key": k, "label": lbl, "unit": u, "default": STRESS_UNITS[k]}
                for k, lbl, u in zip(panel.driver_keys, panel.driver_labels, panel.driver_units)]

    async def shock(self, shocks: dict[str, float]) -> dict | None:
        panel = await self.panel()
        if panel is None:
            return None
        units = dict(zip(panel.driver_keys, panel.driver_units))
        bad = set(shocks) - set(units)
        if bad:
            raise ValueError(f"unknown drivers: {sorted(bad)}")
        idx = {panel.driver_keys.index(k): to_internal(units[k], v) for k, v in shocks.items()}

        def run(panel: Panel) -> dict:
            p = Prepared(panel, WINDOWS["2y"])
            cond = stress.conditional(p.D, p.R, p.w, idx)
            eff = causal.shock_effects(p.D, p.R, p.w, panel.driver_keys, panel.assets, idx)
            labels = dict(zip(panel.driver_keys, panel.driver_labels))
            m = len(panel.driver_keys)
            return {
                "total_cad": panel.total_cad,
                "shocks": shocks,
                "causal": {
                    "portfolio": eff["portfolio"], "direct": eff["direct"], "indirect": eff["indirect"],
                    "ci90": eff["ci90"],
                    "channels": [{"path": [labels.get(n, n) for n in c["path"]], "contribution": c["contribution"]}
                                 for c in eff["channels"][:12]],
                },
                "correlational": {"portfolio": cond["portfolio"]},
                "drivers": [
                    {"key": k, "label": labels[k], "unit": units[k], "shocked": i in idx,
                     "causal": to_display(units[k], float(eff["drivers"][i])),
                     "correlational": to_display(units[k], float(cond["drivers"][i]))}
                    for i, k in enumerate(panel.driver_keys)
                ],
                "assets": [
                    {"asset": a, "weight": float(p.w[j]), "causal": float(eff["assets"][j]),
                     "correlational": float(cond["assets"][j])}
                    for j, a in enumerate(panel.assets)
                ],
                "n": len(p.dates), "drivers_n": m,
            }

        key = tuple(sorted(shocks.items()))
        return await self._cached("shock", key, run)

    async def reverse(self, loss: float, horizon: int) -> dict | None:
        def run(panel: Panel) -> dict:
            p = Prepared(panel, WINDOWS["2y"])
            r = stress.reverse(p.D, p.R, p.w, loss, horizon)
            return {
                "loss": loss, "horizon": horizon, "total_cad": panel.total_cad,
                "drivers": [{"key": k, "label": lbl, "unit": u, "move": to_display(u, float(r["drivers"][i]))}
                            for i, (k, lbl, u) in enumerate(zip(panel.driver_keys, panel.driver_labels,
                                                                  panel.driver_units))],
                "driver_distance": _f(r["driver_distance"]), "driver_r2": r["driver_r2"],
                "driver_every_years": _f(r["driver_every_years"]),
                "assets": [{"asset": a, "move": float(r["assets"][j]), "weight": float(p.w[j])}
                           for j, a in enumerate(panel.assets)],
                "asset_distance": _f(r["asset_distance"]), "asset_every_years": _f(r["asset_every_years"]),
            }

        return await self._cached("reverse", (loss, horizon), run)

    async def causal_graph(self) -> dict | None:
        def run(panel: Panel) -> dict:
            p = Prepared(panel, WINDOWS["2y"])
            keys, units = panel.driver_keys, panel.driver_units
            m = len(keys)
            scm = causal.fit_scm(p.D, p.R, keys, panel.assets)
            sd = np.concatenate([p.D.std(axis=0, ddof=1), p.R.std(axis=0, ddof=1)])
            edges = [
                {"from": keys[pa], "to": keys[v], "coef": float(scm.A[v, pa]),
                 "std_coef": float(scm.A[v, pa] * sd[pa] / sd[v]) if sd[v] else None}
                for v in range(m) for pa in scm.parents(v)
            ]
            loading = p.w @ scm.A[m:, :m]
            impacts = []
            for i, k in enumerate(keys):
                one_sd = float(sd[i])
                eff = causal.shock_effects(p.D, p.R, p.w, keys, panel.assets, {i: one_sd}, boot=100)
                cond = stress.conditional(p.D, p.R, p.w, {i: one_sd})
                impacts.append({
                    "key": k, "unit": units[i],
                    "shock": to_display(units[i], one_sd),
                    "portfolio": eff["portfolio"], "direct": eff["direct"], "indirect": eff["indirect"],
                    "ci90": eff["ci90"], "correlational": cond["portfolio"],
                    "direct_loading": float(loading[i]),
                })
            X = np.column_stack([p.D, p.rp])
            disc = causal.pcmci(X, keys + ["portfolio"])
            prior = {frozenset((e["from"], e["to"])) for e in edges} | {frozenset((k, "portfolio")) for k in keys}
            found = {frozenset((e["a"], e["b"])) for e in disc["contemporaneous"]}
            return {
                "n": len(p.dates), "start": p.dates[0].isoformat(), "end": p.dates[-1].isoformat(),
                "nodes": [{"key": k, "label": lbl, "unit": u} for k, lbl, u in zip(keys, panel.driver_labels, units)]
                + [{"key": "portfolio", "label": "Portfolio", "unit": "pct"}],
                "edges": edges,
                "impacts": impacts,
                "discovery": {
                    **disc,
                    "confirmed": [sorted(e) for e in prior & found],
                    "not_found": [sorted(e) for e in prior - found],
                    "unexpected": [sorted(e) for e in found - prior],
                },
                "r2": float(1 - np.var(scm.resid[:, m:] @ p.w) / np.var(p.rp[scm.rows])) if np.var(p.rp) else None,
            }

        return await self._cached("causal_graph", (), run)

    async def irf(self, driver: str, horizon: int = 20) -> dict | None:
        def run(panel: Panel) -> dict:
            if driver not in panel.driver_keys:
                raise ValueError(f"unknown driver {driver}")
            k = panel.driver_keys.index(driver)
            p = Prepared(panel, WINDOWS["2y"])
            scm, y, controls = causal.lp_inputs(p.D, p.R, p.w, panel.driver_keys, panel.assets)
            sig = float(scm.sigma[k])
            shock = scm.resid[:, k] / sig  # one structural standard deviation
            port = causal.local_projection(shock, y, controls, horizon)
            assets = []
            for j, a in enumerate(panel.assets):
                lp = causal.local_projection(shock, p.R[scm.rows, j], controls, horizon)
                assets.append({"asset": a, "beta": lp["beta"]})
            unit = panel.driver_units[k]
            return {"driver": driver, "label": panel.driver_labels[k], "unit": unit,
                    "shock": to_display(unit, sig), "horizon": horizon, "portfolio": port, "assets": assets,
                    "n": len(y)}

        return await self._cached("irf", (driver, horizon), run)

    # -- decisions -------------------------------------------------------

    async def trades(self) -> list[counterfactual.Trade]:
        ids = await history.tracked_ids(self._sessions)
        async with self._sessions() as session:
            rows = (await session.execute(select(Activity).where(Activity.account_id.in_(ids)))).scalars().all()
        out = []
        for r in rows:
            row = {"type": r.type, "action": r.action, "qty_micro": r.qty_micro}
            if classify(row) != "trade" or not r.price_micro or not r.ticker:
                continue
            sq = signed_qty_micro(row, "trade")
            day = r.trade_date or r.transaction_date
            if sq and day:
                out.append(counterfactual.Trade(day, r.ticker, sq / 1_000_000, r.price_micro / 1_000_000,
                                                (r.currency or "CAD").upper()))
        return out

    async def decisions(self, since: date | None) -> dict:
        trades = await self.trades()
        if not trades:
            return {"rows": [], "summary": {}}
        first = min(t.day for t in trades)
        start = first - timedelta(days=4 * 365)
        syms = await self._symbols()
        tickers = sorted({t.ticker for t in trades})
        yf_of = {t: to_yf_symbol(t, syms[t].exchange if t in syms else None) for t in tickers}
        closes = await self.closes(sorted(set(yf_of.values()) | {"SPY", FX_SYMBOL}), start)
        key = ("decisions", since, len(trades), date.today())
        if key not in self._results:
            def run():
                calendar = sorted(closes.get("SPY", {}))
                by_ticker = {t: closes.get(yf_of[t], {}) for t in tickers}
                rows = counterfactual.decisions(trades, calendar, by_ticker, closes.get(FX_SYMBOL, {}))
                if since:
                    rows = [r for r in rows if r["date"] >= since.isoformat()]
                rows.reverse()
                return {"horizons": list(counterfactual.HORIZONS), "rows": rows,
                        "summary": counterfactual.summarize(rows)}

            self._results[key] = await asyncio.to_thread(run)
        return self._results[key]

    async def event(self, day: date) -> dict | None:
        panel = await self.panel()
        dates, idx = await self.realized()
        if panel is None or len(idx) < 60:
            return None

        def run():
            r = idx[1:] / idx[:-1] - 1
            rd = dates[1:]
            pos = {d: i for i, d in enumerate(panel.dates)}
            keys = ["mkt", "tech", "semis", "usdcad"]
            cols = [panel.factor_keys.index(k) for k in keys]
            X = np.array([panel.F[pos[d], cols] if d in pos else [np.nan] * len(cols) for d in rd])
            # Sessions the factor calendar lacks (TSX-only days) drop out.
            ok = np.isfinite(r) & np.isfinite(X).all(axis=1)
            r, X, rd = r[ok], X[ok], [d for d, k in zip(rd, ok) if k]
            ev = next((i for i, d in enumerate(rd) if d >= day), None)
            if ev is None:
                return None
            res = counterfactual.bsts_event(r, X, ev)
            placebo = counterfactual.bsts_event(r, X, ev - 40, post=30) if ev - 40 > 60 else None
            if res is None:
                return None
            post_dates = [d.isoformat() for d in rd[ev:ev + res["post"]]]
            return {"date": rd[ev].isoformat(), "dates": post_dates, **res, "regressors": keys,
                    "placebo": {"car": placebo["car"][-1], "p_value": placebo["p_value"]} if placebo else None}

        return await asyncio.to_thread(run)
