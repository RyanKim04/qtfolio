# Performance

The Performance page (`/performance`) shows the portfolio's history: a value chart, a benchmark comparison, and a structure (contributions vs gains) view over a selectable range. It also shows stats tiles (TWR, MWR, max drawdown, volatility, best and worst day) and a P&L-by-position table with Window or Lifetime P&L. Today's point is live.

## Sub-features

- `perf-tabs` switches the chart between the `Value`, `Compare`, and `Structure` tabs.
- `perf-range` offers `1M`, `3M`, `6M`, `1Y`, and `ALL`.
- `perf-stats` shows `TWR`, `MWR since …`, `Max drawdown`, `Volatility (ann.)`, `Best day · <date>`, and `Worst day · <date>`.
- `perf-pnl-table` shows the `P&L by position` table with rows per ticker, `Interest & fees`, and `Total`. A `Window P&L` / `Lifetime P&L` toggle switches the basis.

## How to get to it (user POV)

- Click **Performance** in the left rail.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`, and the synthetic export is imported (history 2026-06-01 → today).

Steps:

- **Open.** Click the link `Performance`, then wait for the heading `P&L by position`.
- **Range.** Click `ALL`. The stats label reads `MWR since 2026-06-01`. Click `1M` and the chart redraws, with best and worst day dates inside the last month.
- **Tabs.** Click `Compare`, then `Structure`. Each tab renders a chart with no `Something crashed while rendering this page.` card.
- **P&L table.** In the `P&L by position` section, rows `XEQT.TO`, `SHOP.TO`, and `Total` exist. Click `Lifetime P&L`. The `XEQT.TO` row's `Net invested` reflects the $3,300 buy.
- **Side effect cross-check.** Run `qtv sql "SELECT count(*), min(date), max(date) FROM series_daily"`. It returns a count of about 150 and `min=2026-06-01`.
- **Proof.** Take one `step` per tab and range. `console.log` shows only the favicon 404.

## Gotchas

- The page polls every 30 s, and today's point changes with live prices. Assert labels, rows, and dates, not values.
- Charts are canvas or SVG. Their ARIA snapshot holds no numbers, so use screenshots plus the stats tiles as the proof.
- Benchmarks come from Settings (`benchmarks`). On a fresh DB they use the defaults, and Compare needs Yahoo bars for them.
- This file has not been driven end to end yet. Labels come from `frontend/src/pages/Performance.tsx`. Fix this map if a run disagrees.
