# Stock detail

A stock page (`/stocks/<TICKER>`) shows candles and volume for any Yahoo symbol, whether or not it is held. It offers interval buttons, overlay chips (EMA 20/50/200, Bollinger, VWAP, AVWAP), RSI and MACD pane chips, and a `Relative strength vs <benchmark>` mini-chart. For held positions it adds the avg-cost line, trade markers, and the `Position` and `Levels` panels. `Go to symbol…` jumps to any ticker.

## Sub-features

- `stock-chart` renders candles and volume, and shows the description and last price in the header.
- `stock-interval` provides interval buttons (labels are the upper-cased interval keys).
- `stock-overlays` provides the chips `EMA 20/50/200` (on by default), `Bollinger`, `VWAP` (intraday only), `AVWAP (52w low)` (daily and weekly only), `RSI` and `MACD` (each adds a pane). Chip state is a CSS class, not `aria-pressed`; assert on the chart legend (`EMA 20`, `Bollinger`, `Avg cost`) instead.
- `stock-rs` shows `Relative strength vs <benchmark>` with benchmark chips `QQQ`, `SMH`, `SPY`. Its heading sits in a `div.card`, not a `<section>`, so scope it by text.
- `stock-position` shows the `Position` panel (avg cost, market value, …) for held tickers, with avg-cost line and (on `1D` only) trade markers on the chart. Not-held tickers show `Not currently held.`
- `stock-levels` shows the `Levels` panel.
- `stock-search` sends `Go to symbol…` + Enter to `/stocks/<UPPERCASED>`.

## How to get to it (user POV)

- Click a holdings row on the Dashboard, or a ticker in Performance `P&L by position` or a day breakdown.
- Type a symbol in `Go to symbol…` on any stock page and press Enter.
- Open `/stocks/<TICKER>` directly.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`, and the synthetic export is imported (`XEQT.TO` and `SHOP.TO` held).

Steps (`qtv browse stock-detail .claude/skills/verify-qtfolio/journeys/stock-detail.mjs` runs them all):

- **Held ticker.** `page.goto(base + "/stocks/XEQT.TO")`. Heading `XEQT.TO` is visible, and the `Position` section shows `Avg cost` = `$33.00`.
- **Overlays.** Click the `Bollinger` chip; legend text `Bollinger` appears. Click `EMA 20/50/200`; legend text `EMA 20` disappears (it starts on). No crash card.
- **Interval.** Click `5M`. `VWAP` becomes enabled and `AVWAP (52w low)` disabled. Click `1W` and AVWAP is enabled again. Click `1D` to return.
- **Panes.** Click `RSI` and `MACD`. No crash card.
- **Search, not held.** Fill `Go to symbol…` with `msft` and press Enter. The URL becomes `/stocks/MSFT`, heading `MSFT` is visible, and `Position` reads `Not currently held.` `Relative strength vs` renders.
- **Unknown symbol.** Open `/stocks/ZZZZQX`. The chart area shows `unknown symbol 'ZZZZQX'`.
- **API cross-check.** Run `qtv api GET "/api/stocks/XEQT.TO/position" xeqt-position`. It shows `owned: true` and avg cost 33.
- **Proof.** Take a `step` after each action.

## Gotchas

- The chart is lightweight-charts canvas, so the ARIA snapshot cannot see candles. Prove rendering with screenshots and the absence of the crash card.
- Not-held symbols need Yahoo to resolve them. An unknown symbol shows the 404 detail `unknown symbol '<X>'` in red, and `Levels` stays on `Loading…`. `No candle data for <X> on <interval>.` appears only for a symbol that resolves but has no bars.
- Intraday bars outside market hours may be sparse.
- The unknown-symbol step logs `[http 404]` for `/api/stocks/ZZZZQX/candles` and `/levels` in `console.log`. Those are the negative case, not a finding.
- Labels come from `frontend/src/pages/StockDetail.tsx` and `components/StockChart.tsx`. Fix this map if a run disagrees.
