"""Performance math: TWR chain-linking (incl. day one and a mid-period deposit), seam
rescaling against synthetic snapshots, valuation forward-fill, range stats."""

import statistics
from datetime import date, timedelta

from app.services.series import (
    apply_ground_truth,
    conversion_cost_series,
    fx_translation_series,
    range_stats,
    twr_index,
    value_series,
)

D = [date(2024, 1, i) for i in range(1, 12)]
M = 1_000_000


def test_fx_translation_gain_on_usd_holding():
    # Hold 10 USD shares @ $100 (=$1000 USD). Rate goes 1.30 -> 1.40 over the window.
    # FX gain = 1000_00 USD cents × (1.40 − 1.30) = 100_00 CAD cents ($100).
    daily_qty = {D[0]: {"XYZ": 10 * M}, D[1]: {"XYZ": 10 * M}}
    daily_cash: dict = {D[0]: {}, D[1]: {}}
    closes = {"XYZ": {D[0]: 100_00, D[1]: 100_00}}
    fx = {D[0]: 1_300_000, D[1]: 1_400_000}
    out = fx_translation_series(daily_qty, daily_cash, closes, fx, {"XYZ": "USD"})
    assert out[D[0]] == 0
    assert out[D[1]] == 100_00  # $100 of CAD P&L came purely from USD/CAD moving


def test_fx_translation_zero_for_cad_only_portfolio():
    daily_qty = {D[0]: {"TD.TO": 10 * M}, D[1]: {"TD.TO": 10 * M}}
    closes = {"TD.TO": {D[0]: 80_00, D[1]: 90_00}}
    fx = {D[0]: 1_300_000, D[1]: 1_400_000}
    out = fx_translation_series(daily_qty, {}, closes, fx, {"TD.TO": "CAD"})
    assert out[D[0]] == 0 and out[D[1]] == 0  # CAD holdings carry no FX effect


def test_fx_translation_counts_usd_cash():
    daily_qty = {D[0]: {}, D[1]: {}}
    daily_cash = {D[0]: {"USD": 500_00}, D[1]: {"USD": 500_00}}
    fx = {D[0]: 1_400_000, D[1]: 1_350_000}  # USD/CAD falls 1.40 -> 1.35
    out = fx_translation_series(daily_qty, daily_cash, {}, fx, {})
    assert out[D[1]] == 500_00 * (1_350_000 - 1_400_000) // M  # negative FX (−$25.00)


def _fx_leg(d, cur, cents):
    return {"transaction_date": d, "type": "fx conversion", "action": "fxt", "currency": cur, "net_amount_cents": cents}


def test_conversion_cost_is_spread_vs_market_rate():
    # Market 1.38. Buy US$1000 for C$1400 (broker rate 1.40): cost = 1000×1.38 − 1400 = −$20.
    # Later sell US$500 for C$680 (broker 1.36 vs market 1.38): another −$10.
    fx = {D[0]: 1_380_000}
    acts = [
        _fx_leg(D[1], "CAD", -1400_00), _fx_leg(D[1], "USD", 1000_00),
        _fx_leg(D[3], "USD", -500_00), _fx_leg(D[3], "CAD", 680_00),
        {"transaction_date": D[2], "type": "trades", "action": "buy", "currency": "USD", "net_amount_cents": -900_00},
    ]
    out = conversion_cost_series(acts, fx, D[:5])
    assert out[D[0]] == 0 and out[D[1]] == -20_00 and out[D[2]] == -20_00  # trades don't count
    assert out[D[3]] == out[D[4]] == -30_00


def test_conversion_cost_window_starts_after_first_date():
    # A conversion on the first series date predates the window; cum starts at 0 there.
    acts = [_fx_leg(D[0], "CAD", -1400_00), _fx_leg(D[0], "USD", 1000_00)]
    out = conversion_cost_series(acts, {D[0]: 1_380_000}, D[:2])
    assert out == {D[0]: 0, D[1]: 0}


def test_twr_day_one_deposit_is_well_defined():
    # Empty account, $10k deposited on day 1. The deposit sits in the denominator.
    values = {D[0]: 0, D[1]: 10_000_00, D[2]: 11_000_00}
    flows = {D[1]: 10_000_00}
    idx, warnings = twr_index(values, flows)
    assert idx[D[1]] == 100.0            # deposit itself is not a return
    assert abs(idx[D[2]] - 110.0) < 1e-9
    assert warnings == []


def test_twr_mid_period_deposit_not_counted_as_growth():
    # $100 → $210 where $100 of it was a deposit: the day's return is 5%, not 110%.
    values = {D[0]: 100_00, D[1]: 210_00}
    flows = {D[1]: 100_00}
    idx, _ = twr_index(values, flows)
    assert abs(idx[D[1]] - 105.0) < 1e-9


def test_twr_zero_denominator_forces_zero_and_logs():
    values = {D[0]: 0, D[1]: 0, D[2]: 5_000_00}
    flows = {D[2]: 5_000_00}
    idx, warnings = twr_index(values, flows)
    assert idx[D[1]] == 100.0
    assert len(warnings) == 1 and "denominator" in warnings[0]["message"]


def test_seam_rescale_before_first_snapshot_constant_factor():
    recon = {D[0]: 100_00, D[1]: 110_00, D[2]: 120_00, D[3]: 130_00}
    gt = {D[2]: 240_00}
    out = apply_ground_truth(recon, gt)
    assert out[D[2]] == 240_00                      # snapshot wins exactly
    assert out[D[0]] == 200_00 and out[D[1]] == 220_00  # single constant factor before it
    assert out[D[3]] == 260_00                      # last factor carried forward


def test_seam_rescale_between_snapshots_linear_no_step():
    recon = {D[0]: 100_00, D[1]: 100_00, D[2]: 100_00, D[3]: 100_00, D[4]: 100_00}
    gt = {D[0]: 200_00, D[4]: 300_00}
    out = apply_ground_truth(recon, gt)
    assert out[D[0]] == 200_00 and out[D[4]] == 300_00
    assert out[D[1]] == 225_00 and out[D[2]] == 250_00 and out[D[3]] == 275_00  # linear, seamless


def test_seam_rescale_phantom_return_prevented():
    # A hard override (no rescale) would print a +100% day into TWR at D2.
    recon = {D[0]: 100_00, D[1]: 100_00, D[2]: 100_00}
    gt = {D[2]: 200_00}
    out = apply_ground_truth(recon, gt)
    idx, _ = twr_index(out, {})
    assert abs(idx[D[2]] - idx[D[0]]) < 1e-9  # flat reconstruction stays flat after rescaling


def test_valuation_forward_fills_and_converts_usd():
    daily_qty = {D[0]: {"NVDA": 10_000_000}, D[1]: {"NVDA": 10_000_000}}
    daily_cash = {D[0]: {"CAD": 1_000_00}, D[1]: {"CAD": 1_000_00}}
    closes = {"NVDA": {D[0]: 500_00}}          # no close on D1 → forward-filled
    fx = {D[0]: 1_350_000}                     # 1.35 USDCAD
    values, warnings = value_series(daily_qty, daily_cash, closes, fx, {"NVDA": "USD"})
    assert values[D[0]] == 10 * 500_00 * 1_350_000 // 1_000_000 + 1_000_00
    assert values[D[1]] == values[D[0]]        # ffilled close and fx
    assert warnings == []


def test_range_stats_drawdown_and_best_worst():
    idx = {D[0]: 100.0, D[1]: 110.0, D[2]: 99.0, D[3]: 120.0}
    values = {d: round(idx[d] * 100) for d in idx}
    stats = range_stats(sorted(idx), values, idx, {})
    assert abs(stats["twr"] - 0.20) < 1e-9
    assert abs(stats["max_drawdown"] - (99 / 110 - 1)) < 1e-9
    assert stats["best_day"]["date"] == D[3].isoformat()
    assert stats["worst_day"]["date"] == D[2].isoformat()


def test_xirr_mwr_sane():
    # $100 at start, deposit $100 halfway, $220 at end. MWR should be positive and modest.
    dates = [date(2024, 1, 1), date(2024, 7, 1), date(2024, 12, 31)]
    values = {dates[0]: 100_00, dates[1]: 205_00, dates[2]: 220_00}
    idx = {dates[0]: 100.0, dates[1]: 102.0, dates[2]: 108.0}
    flows = {dates[1]: 100_00}
    stats = range_stats(dates, values, idx, flows)
    assert stats["mwr"] is not None and 0 < stats["mwr"] < 0.30


def test_range_stats_vol_and_days_on_market_sessions():
    # Mon..Mon calendar series; the weekend rows are flat (no market).
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(8)]  # Mon 6/1 .. Mon 6/8
    levels = [100, 102, 99, 101, 100, 100, 100, 95]
    idx = {d: float(v) for d, v in zip(days, levels)}
    values = {d: v * 100 for d, v in zip(days, levels)}
    sessions = {d for d in days if d.weekday() < 5}
    stats = range_stats(days, values, idx, {}, sessions)
    rets = [102 / 100 - 1, 99 / 102 - 1, 101 / 99 - 1, 100 / 101 - 1, 95 / 100 - 1]  # weekend folded into Mon
    assert abs(stats["vol"] - statistics.stdev(rets) * 252**0.5) < 1e-12
    assert stats["worst_day"] == {"date": "2026-06-08", "prev": "2026-06-05", "r": 95 / 100 - 1}
    # calendar-day sampling pads with zeros and understates vol
    assert range_stats(days, values, idx, {})["vol"] < stats["vol"]
