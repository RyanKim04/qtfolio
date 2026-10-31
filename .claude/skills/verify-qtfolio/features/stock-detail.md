# Stock detail

A stock page (`/stocks/<TICKER>`) shows candles and volume for any Yahoo symbol, whether or not it is held. It offers interval buttons, overlay chips (EMA 20/50/200, Bollinger, VWAP, AVWAP), RSI and MACD panes, and an RS mini-chart. For held positions it adds the avg-cost line, trade markers, and the `Position` and `Levels` panels. `Go to symbol…` jumps to any ticker.

## Sub-features

- `stock-chart` renders candles and volume, and shows the description and last price in the header.
- `stock-interval` provides interval buttons (labels are the upper-cased interval keys).
- `stock-overlays` provides the chips `EMA 20/50/200`, `Bollinger`, `VWAP` (intraday only), and `AVWAP (52w low)` (daily only).
- `stock-position` shows the `Position` panel (avg cost, market value, …) for held tickers, with avg-cost line and trade markers on the chart.
- `stock-levels` shows the `Levels` panel.
- `stock-search` sends `Go to symbol…` + Enter to `/stocks/<UPPERCASED>`.

## How to get to it (user POV)

- Click a holdings row on the Dashboard.
- Type a symbol in `Go to symbol…` on any stock page and press Enter.
- Open `/stocks/<TICKER>` directly.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`, and the synthetic export is imported (`XEQT.TO` and `SHOP.TO` held).

Steps:

- **Held ticker.** `page.goto(base + "/stocks/XEQT.TO")`. Heading `XEQT.TO` is visible, and the `Position` section shows `Avg cost` = `$33.00`.
- **Overlays.** Click the `Bollinger` chip and then `EMA 20/50/200`. Each toggles its on-state, and the chart re-renders without the crash card.
- **Interval.** Click an intraday interval button. `VWAP` becomes enabled and `AVWAP (52w low)` becomes disabled.
- **Search, not held.** Fill `Go to symbol…` with `msft` and press Enter. The URL becomes `/stocks/MSFT` and heading `MSFT` is visible, with no avg-cost data in the `Position` panel.
- **API cross-check.** Run `qtv api GET "/api/stocks/XEQT.TO/position" xeqt-position`. It shows `owned: true` and avg cost 33.
- **Proof.** Take a `step` after each action.

## Gotchas

- The chart is lightweight-charts canvas, so the ARIA snapshot cannot see candles. Prove rendering with screenshots and the absence of the crash card.
- Not-held symbols need Yahoo to resolve them. An unknown symbol shows an empty chart, which is correct behavior to assert for a negative case.
- Intraday bars outside market hours may be sparse.
- This file has not been driven end to end yet. Labels come from `frontend/src/pages/StockDetail.tsx`. Fix this map if a run disagrees.
