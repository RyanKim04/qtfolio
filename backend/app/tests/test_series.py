"""§7 math: TWR chain-linking (incl. day one and a mid-period deposit), seam
rescaling against synthetic snapshots, valuation forward-fill, range stats."""

from datetime import date

from app.services.series import apply_ground_truth, range_stats, twr_index, value_series

D = [date(2024, 1, i) for i in range(1, 12)]


def test_twr_day_one_deposit_is_well_defined():
    # Empty account, $10k deposited on day 1 — the deposit sits in the denominator.
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
    # $100 at start, deposit $100 halfway, $220 at end — MWR should be positive and modest.
    dates = [date(2024, 1, 1), date(2024, 7, 1), date(2024, 12, 31)]
    values = {dates[0]: 100_00, dates[1]: 205_00, dates[2]: 220_00}
    idx = {dates[0]: 100.0, dates[1]: 102.0, dates[2]: 108.0}
    flows = {dates[1]: 100_00}
    stats = range_stats(dates, values, idx, flows)
    assert stats["mwr"] is not None and 0 < stats["mwr"] < 0.30
