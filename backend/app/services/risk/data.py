"""The return panel every risk analysis runs on.

Calendar = NYSE sessions (SPY's closes). Other listings are forward-filled onto
it (TSX holidays carry the last close, so the move lands on the next shared
session). Holding returns are in CAD, the owner's base currency:
(1 + r_native)(1 + r_usdcad) − 1 for USD listings. USD cash is a pseudo-asset
whose CAD return is the USD/CAD move; CAD cash has none.

Units: returns are decimals, except two drivers: the 10y yield is a change in
basis points and the VIX a change in index points.
"""

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

USD_CASH = "USD cash"
FX_SYMBOL = "CAD=X"  # Yahoo's USD/CAD (CAD per USD)
HISTORY_START = date(2008, 1, 2)  # covers the oldest stress scenario (GFC)
MAX_FFILL_DAYS = 7  # a listing silent longer than this is missing, not flat

# (key, label, long leg, short leg). Spreads keep the regressors from sharing
# the market's variance, so a holding's exposures read as separate bets.
# DECISION: ETF proxies on Yahoo rather than Ken French's library: same source
# and calendar as every price here, and no publication lag.
# Credit is duration-hedged: HYG's duration is about 0.45× IEF's, so HYG − IEF
# would rise mechanically with yields and pass a rate shock off as credit.
CREDIT_HEDGE = 0.45
SHORT_RATIO = {"credit": CREDIT_HEDGE}
FACTORS: list[tuple[str, str, str, str | None]] = [
    ("mkt", "US market", "SPY", None),
    ("tech", "Tech vs market", "QQQ", "SPY"),
    ("semis", "Semis vs tech", "SMH", "QQQ"),
    ("size", "Small vs large", "IWM", "SPY"),
    ("value", "Value vs growth", "IWD", "IWF"),
    ("mom", "Momentum", "MTUM", "SPY"),
    ("lowvol", "Low volatility", "USMV", "SPY"),
    ("canada", "Canada vs US", "XIU.TO", "SPY"),
    ("bonds", "Treasuries", "IEF", None),
    ("credit", "Credit spread", "HYG", "IEF"),
    ("oil", "Oil", "USO", None),
    ("gold", "Gold", "GLD", None),
    ("usdcad", "USD/CAD", FX_SYMBOL, None),
]

# Macro risk drivers of the causal model (services/risk/causal.py).
# (key, label, symbol(s), unit). Order is the prior's causal order.
DRIVERS: list[tuple[str, str, tuple[str, ...], str]] = [
    ("rates", "US 10y yield", ("^TNX",), "bp"),
    ("oil", "Oil", ("USO",), "pct"),
    ("usd", "US dollar", ("UUP",), "pct"),
    ("credit", "Credit", ("HYG", "IEF"), "pct"),
    ("vix", "VIX", ("^VIX",), "pts"),
    ("equity", "US stocks", ("SPY",), "pct"),
    ("usdcad", "USD/CAD", (FX_SYMBOL,), "pct"),
]


def market_symbols() -> list[str]:
    syms = {FX_SYMBOL}
    for _, _, long, short in FACTORS:
        syms.add(long)
        if short:
            syms.add(short)
    for _, _, ss, _ in DRIVERS:
        syms.update(ss)
    return sorted(syms)


@dataclass
class Holding:
    ticker: str
    yf_symbol: str
    currency: str
    weight: float  # CAD market value / total CAD value (cash included)
    value_cad: float


@dataclass
class Panel:
    dates: list[date]  # return dates (session t, return from t−1 to t)
    assets: list[str]
    R: np.ndarray  # T×N CAD returns, NaN where the asset had no price
    weights: np.ndarray  # N
    factor_keys: list[str]
    factor_labels: list[str]
    F: np.ndarray  # T×K factor returns
    driver_keys: list[str]
    driver_labels: list[str]
    driver_units: list[str]
    D: np.ndarray  # T×M driver changes
    total_cad: float
    currencies: dict[str, str] = field(default_factory=dict)
    yf_symbols: dict[str, str] = field(default_factory=dict)
    closes: dict[str, dict[date, float]] = field(default_factory=dict)  # raw, for counterfactuals

    def tail(self, n: int) -> "Panel":
        """The last n sessions (all of them when n ≥ T)."""
        s = slice(max(0, len(self.dates) - n), None)
        return Panel(self.dates[s], self.assets, self.R[s], self.weights, self.factor_keys, self.factor_labels,
                     self.F[s], self.driver_keys, self.driver_labels, self.driver_units, self.D[s], self.total_cad,
                     self.currencies, self.yf_symbols, self.closes)

    def between(self, start: date, end: date) -> "Panel":
        idx = [i for i, d in enumerate(self.dates) if start < d <= end]
        s = slice(idx[0], idx[-1] + 1) if idx else slice(0, 0)
        return Panel(self.dates[s], self.assets, self.R[s], self.weights, self.factor_keys, self.factor_labels,
                     self.F[s], self.driver_keys, self.driver_labels, self.driver_units, self.D[s], self.total_cad,
                     self.currencies, self.yf_symbols, self.closes)

    def portfolio_returns(self) -> np.ndarray:
        """Constant-weight (current book) daily returns. Missing asset returns
        count as 0 here; callers that need full history fill them first."""
        return np.nan_to_num(self.R) @ self.weights


def align(closes: dict[date, float], calendar: list[date]) -> np.ndarray:
    """Closes on the calendar: forward-filled up to MAX_FFILL_DAYS, NaN before the
    first observation or after a longer silence."""
    out = np.full(len(calendar), np.nan)
    if not closes:
        return out
    days = sorted(closes)
    j, last_d, last_c = 0, None, math.nan
    for i, d in enumerate(calendar):
        while j < len(days) and days[j] <= d:
            last_d, last_c = days[j], closes[days[j]]
            j += 1
        if last_d is not None and (d - last_d).days <= MAX_FFILL_DAYS:
            out[i] = last_c
    return out


def simple_returns(levels: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        r = levels[1:] / levels[:-1] - 1
    r[~np.isfinite(r)] = np.nan
    return r


def yield_scale(levels: np.ndarray) -> np.ndarray:
    """Yahoo has quoted ^TNX both as percent (4.25) and as 10× percent (42.5)."""
    med = np.nanmedian(levels) if np.isfinite(levels).any() else 0
    return levels / 10 if med > 20 else levels


def build_panel(closes: dict[str, dict[date, float]], holdings: list[Holding], usd_cash_weight: float,
                total_cad: float, usd_cash_cad: float = 0.0) -> Panel:
    calendar = sorted(closes.get("SPY", {}))
    if len(calendar) < 3:
        raise ValueError("no SPY history to build the calendar from")
    lv = {sym: align(c, calendar) for sym, c in closes.items()}
    ret = {sym: simple_returns(v) for sym, v in lv.items()}
    nan = np.full(len(calendar) - 1, np.nan)
    fx = ret.get(FX_SYMBOL, nan)

    assets, cols, weights, currencies, yf_syms = [], [], [], {}, {}
    for h in holdings:
        r = ret.get(h.yf_symbol, nan)
        if h.currency == "USD":
            r = (1 + r) * (1 + fx) - 1
        assets.append(h.ticker)
        cols.append(r)
        weights.append(h.weight)
        currencies[h.ticker] = h.currency
        yf_syms[h.ticker] = h.yf_symbol
    if usd_cash_weight > 0.005:
        assets.append(USD_CASH)
        cols.append(fx)
        weights.append(usd_cash_weight)
        currencies[USD_CASH] = "USD"
    R = np.column_stack(cols) if cols else np.empty((len(calendar) - 1, 0))

    F = np.column_stack([
        ret.get(long, nan) - (SHORT_RATIO.get(key, 1.0) * ret.get(short, nan) if short else 0)
        for key, _, long, short in FACTORS
    ])
    dcols = []
    for key, _, syms, _ in DRIVERS:
        if key == "rates":
            dcols.append(np.diff(yield_scale(lv.get("^TNX", np.full(len(calendar), np.nan)))) * 100)
        elif key == "vix":
            dcols.append(np.diff(lv.get("^VIX", np.full(len(calendar), np.nan))))
        elif len(syms) == 2:
            dcols.append(ret.get(syms[0], nan) - SHORT_RATIO.get(key, 1.0) * ret.get(syms[1], nan))
        else:
            dcols.append(ret.get(syms[0], nan))
    D = np.column_stack(dcols)
    return Panel(
        dates=calendar[1:], assets=assets, R=R, weights=np.array(weights, dtype=float),
        factor_keys=[k for k, *_ in FACTORS], factor_labels=[lbl for _, lbl, *_ in FACTORS], F=F,
        driver_keys=[k for k, *_ in DRIVERS], driver_labels=[lbl for _, lbl, *_ in DRIVERS],
        driver_units=[u for *_, u in DRIVERS], D=D, total_cad=total_cad,
        currencies=currencies, yf_symbols=yf_syms, closes=closes,
    )


def complete_rows(*arrays: np.ndarray) -> np.ndarray:
    """Row mask where every column of every array is finite."""
    mask = np.ones(arrays[0].shape[0], dtype=bool)
    for a in arrays:
        a2 = a.reshape(a.shape[0], -1)
        mask &= np.isfinite(a2).all(axis=1)
    return mask


def fill_missing(R: np.ndarray, F: np.ndarray, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Proxy-backfill each asset's missing days from its factor model: fitted
    factor return plus a resampled own residual, so a short-history listing keeps
    its variance and co-movement instead of reading as flat. Returns the filled
    matrix and the proxied share per asset (shown next to its numbers).
    Days with missing factors stay missing."""
    rng = np.random.default_rng(seed)
    out = R.copy()
    share = np.zeros(R.shape[1])
    fok = np.isfinite(F).all(axis=1)
    for j in range(R.shape[1]):
        y = R[:, j]
        have = np.isfinite(y) & fok
        miss = ~np.isfinite(y) & fok
        if not miss.any():
            continue
        share[j] = miss.sum() / max(1, (fok).sum())
        if have.sum() < 30:
            continue  # too little to fit; stays missing
        X = np.column_stack([np.ones(have.sum()), F[have]])
        beta, *_ = np.linalg.lstsq(X, y[have], rcond=None)
        resid = y[have] - X @ beta
        Xm = np.column_stack([np.ones(miss.sum()), F[miss]])
        out[miss, j] = Xm @ beta + rng.choice(resid, size=miss.sum(), replace=True)
    return out, share
