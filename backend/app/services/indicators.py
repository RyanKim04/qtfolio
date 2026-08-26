"""Indicator engine — SPEC.md §9. Pure Python over candle lists; every array is
aligned to the candle timestamps with None during warmup, so the frontend stays
dumb. TA-Lib conventions: SMA-seeded EMA, Wilder smoothing for RSI/ATR.
(Support/resistance deliberately absent — owner-deferred, §17.)
"""

import math
from datetime import date


def sma(closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    total = 0.0
    for i, c in enumerate(closes):
        total += c
        if i >= n:
            total -= closes[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(closes[:n]) / n  # SMA seed
    out[n - 1] = prev
    for i in range(n, len(closes)):
        prev = prev + k * (closes[i] - prev)
        out[i] = prev
    return out


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    out[n] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n   # Wilder smoothing
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal_n: int = 9) -> dict:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: list[float | None] = [
        (ef[i] - es[i]) if ef[i] is not None and es[i] is not None else None for i in range(len(closes))
    ]
    valid = [v for v in line if v is not None]
    sig_valid = ema(valid, signal_n) if len(valid) >= signal_n else [None] * len(valid)
    signal: list[float | None] = [None] * len(closes)
    j = 0
    for i, v in enumerate(line):
        if v is not None:
            signal[i] = sig_valid[j]
            j += 1
    hist = [
        (line[i] - signal[i]) if line[i] is not None and signal[i] is not None else None
        for i in range(len(closes))
    ]
    return {"macd": line, "signal": signal, "hist": hist}


def bollinger(closes: list[float], n: int = 20, k: float = 2.0) -> dict:
    mid = sma(closes, n)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        window = closes[i - n + 1 : i + 1]
        m = mid[i]
        std = math.sqrt(sum((c - m) ** 2 for c in window) / n)  # population std
        upper[i] = m + k * std
        lower[i] = m - k * std
    return {"upper": upper, "mid": mid, "lower": lower}


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    prev = sum(trs[1 : n + 1]) / n
    out[n] = prev
    for i in range(n + 1, len(closes)):
        prev = (prev * (n - 1) + trs[i]) / n  # Wilder smoothing
        out[i] = prev
    return out


def _typical(h: float, l: float, c: float) -> float:
    return (h + l + c) / 3


def session_vwap(bars: list[dict], session_of) -> list[float | None]:
    """Intraday VWAP, resetting at each session boundary. `session_of(bar)` maps
    a bar to its trading-day key (ET date)."""
    out: list[float | None] = [None] * len(bars)
    pv = vol = 0.0
    current = None
    for i, b in enumerate(bars):
        s = session_of(b)
        if s != current:
            current, pv, vol = s, 0.0, 0.0
        v = b.get("v") or 0
        pv += _typical(b["h"], b["l"], b["c"]) * v
        vol += v
        out[i] = pv / vol if vol else None
    return out


def anchored_vwap(bars: list[dict], anchor_idx: int) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    pv = vol = 0.0
    for i in range(anchor_idx, len(bars)):
        b = bars[i]
        v = b.get("v") or 0
        pv += _typical(b["h"], b["l"], b["c"]) * v
        vol += v
        out[i] = pv / vol if vol else None
    return out


def rolling_extremes(highs: list[float], lows: list[float], n: int = 252) -> dict:
    """Trailing n-bar high/low (52w on dailies)."""
    hi: list[float | None] = [None] * len(highs)
    lo: list[float | None] = [None] * len(lows)
    for i in range(len(highs)):
        s = max(0, i - n + 1)
        hi[i] = max(highs[s : i + 1])
        lo[i] = min(lows[s : i + 1])
    return {"high": hi, "low": lo}


def ema_crosses(fast: list[float | None], slow: list[float | None]) -> list[dict]:
    """Golden/death crosses between two EMA arrays; returns bar indices."""
    out = []
    for i in range(1, len(fast)):
        if None in (fast[i], slow[i], fast[i - 1], slow[i - 1]):
            continue
        prev = fast[i - 1] - slow[i - 1]
        cur = fast[i] - slow[i]
        if prev <= 0 < cur:
            out.append({"index": i, "kind": "golden"})
        elif prev >= 0 > cur:
            out.append({"index": i, "kind": "death"})
    return out


def aggregate_weekly(bars: list[dict]) -> list[dict]:
    """Weekly bars from dailies (ISO week; t = week's first trading day)."""
    out: list[dict] = []
    week = None
    for b in bars:
        d: date = b["date"]
        key = d.isocalendar()[:2]
        if key != week:
            week = key
            out.append({**b})
        else:
            last = out[-1]
            last["h"] = max(last["h"], b["h"])
            last["l"] = min(last["l"], b["l"])
            last["c"] = b["c"]
            last["v"] = (last["v"] or 0) + (b["v"] or 0)
    return out
