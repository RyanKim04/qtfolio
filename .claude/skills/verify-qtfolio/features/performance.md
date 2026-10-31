# Performance

The Performance page (`/performance`) shows the portfolio's history: a value chart, a benchmark comparison, and a structure (contributions vs gains) view over a selectable range. It also shows stats tiles (TWR, MWR, max drawdown, volatility, best and worst day) and a P&L-by-position table. Today's point is live.

## Sub-features

- `perf-tabs` switches the chart between the `Value`, `Compare`, and `Structure` tabs.
- `perf-range` offers `1M`, `3M`, `6M`, `1Y`, and `ALL`.
- `perf-stats` shows `TWR`, `MWR since …`, `Max drawdown`, `Volatility (ann.)`, `Best day · <date>`, and `Worst day · <date>`.
- `perf-pnl-table` shows the `P&L by position` table: `Holding` and `Closed` groups (columns `Bought`, `Sold`, `Value now`, `P&L`, plus `Value on <start>` only when the range starts mid-history), each with a `… total` row, then an `Outside positions` group with `Dividends`, `Interest`, `USD cash FX`, `Currency conversion`, `Fees and adjustments` (each only when nonzero), and `Total`.
- The page opens on `Value` · `ALL` · `Lifetime P&L` every visit (not remembered). Selected segment buttons carry `aria-pressed=true`.
- `perf-compare` chips start with the benchmarks (default `QQQ`, `SMH`, `SPY`, `XEQT.TO`), then owned tickers.

## How to get to it (user POV)

- Click **Performance** in the left rail.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`, and the synthetic export is imported (history 2026-06-01 → today).

Steps:

- **Open.** Click the link `Performance`, then wait for the heading `P&L by position`.
- **Range.** `ALL` is already selected on open. The stats label reads `MWR since 2026-06-01`. Click `1M` and the chart redraws, with best and worst day dates inside the last month.
- **Tabs.** Click `Compare`, then `Structure`. Each tab renders a chart with no `Something crashed while rendering this page.` card.
- **P&L table.** In the `P&L by position` section, rows `XEQT.TO`, `SHOP.TO`, and `Total` exist. The `XEQT.TO` row is under `Holding` and its `Bought` is $3,300.00.
- **P&L numbers.** On a fresh run, `qtv browse pnl-lines .claude/skills/verify-qtfolio/journeys/pnl-lines.mjs` imports `fixtures/ws-activities-cash-lines.csv` (TFSA `VERIFY2`) and asserts every table identity to the cent at `ALL` and `1M`. Then check the cash-only lines by hand:
  - FX coverage first: `qtv sql "SELECT min(date) FROM fx_rates"` must be on or before 2026-06-01. If it isn't, every USD amount is valued at the wrong rate. Fail the run and report it; don't just note it.
  - With `r(d)` = the newest `fx_rates` row on or before `d` (from `qtv api GET '/api/performance/attribution?range=all'`): `conversion_cost` = (2000 × r(06-03) − 2800) + (136 − 100 × r(09-01)), `dividends` = 3.32 × r(07-10), `fees` = −25 − 0.50 × r(07-10), `rebates` = 30, `interest` = 1.23. Expect conversion cost < 0 with real June–September rates (the fixture converts at 1.40 / 1.36). Cents can differ by one from floor rounding.
- **Side effect cross-check.** Run `qtv sql "SELECT count(*), min(date), max(date) FROM series_daily"`. It returns a count of about 150 and `min=2026-06-01`.
- **Proof.** Take one `step` per tab and range. `console.log` shows only the favicon 404.

## Gotchas

- The page polls every 30 s, and today's point changes with live prices. Assert labels, rows, and dates, not values.
- Charts are canvas or SVG. Their ARIA snapshot holds no numbers, so use screenshots plus the stats tiles as the proof.
- Benchmarks come from Settings (`benchmarks`). On a fresh DB they use the defaults, and Compare needs Yahoo bars for them.
- `Fees and adjustments` holds an unexplained remainder (`other` in the API), so don't hand-check it. Its tooltip itemizes fees, rebates and the remainder. Only the identities bind it.
- Labels come from `frontend/src/pages/Performance.tsx`. Fix this map if a run disagrees.
