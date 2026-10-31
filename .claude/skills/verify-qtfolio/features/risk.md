# Risk analytics

The Risk tab (`/risk`), the Dashboard's risk row with the holdings `Risk` and `Beta` columns, and the Performance additions: risk-adjusted metrics, the `Drawdown` and `Rolling` modes, monthly returns, and trade decisions. The backend computes everything on the current book from Yahoo adjusted daily closes (17 factor and driver symbols, ETFs plus `^VIX`, `^TNX`, `CAD=X`, back to 2008). It caches each result until the book or the day changes, or for at most 15 minutes. The Sharpe family uses the Bank of Canada 3-month T-bill as the risk-free rate.

## Sub-features

- `risk-row` on the Dashboard shows `1-day VaR 95%`, `Expected shortfall 97.5%`, `Volatility (ex-ante)`, `Beta`, `Effective bets`, and `Drawdown`, plus a `Full risk analysis` link. The holdings table gains `Risk` (Euler share of volatility) and `Beta` columns.
- `risk-decomposition` (`Where the risk comes from`) has covariance methods `Shrunk` / `EWMA` / `Sample` / `Factor` and windows `6M` / `1Y` / `2Y`, with diversification ratio, effective bets (torsion and PCA), and effective positions. Short-history holdings show `N% proxied`. The Risk tab also opens with the same six-tile row as the Dashboard.
- `risk-correlation` is a clustered heatmap with `All days` / `Stress days`, an `average X · stress days Y` readout and a −1 to +1 legend. It follows the decomposition's method and window toggles.
- `risk-factors` (`Factor exposures`) lists 13 ETF-proxy betas per holding with HAC t-stats, R², own vol, and the portfolio's risk share per factor.
- `risk-var` (`Value at Risk and expected shortfall`) shows five methods at `95%` / `97.5%` / `99%` and `1 day` / `10 days`, Euler components, the return histogram, and a Kupiec/Christoffersen backtest. The backtest needs 300+ realized sessions, so the synthetic book shows a message instead.
- `risk-stress` (`Stress tests`) has tabs `Historical` (10 crisis windows, rows expand), `Shock` (driver sliders plus `Equity −10%`, `Risk-off`, `Hawkish`, `Clear` presets), `Reverse` (10/20/30% over 1 day, 1 month, or 3 months), and `Monte Carlo`.
- `risk-causal` (`Causal view of macro risk`) shows the prior DAG with data-checked edges, the impact table (causal, direct, knock-on, 90% CI, correlational), and impulse responses per driver chip.
- `perf-risk` is `Risk-adjusted performance` with a benchmark `select` (`SPY` first and default, then the Settings benchmarks), the `Drawdown` and `Rolling` (Volatility, Sharpe, Beta over 63 sessions) modes, `Monthly returns`, and `Trade decisions` (`20 sessions` / `60 sessions` / `120 sessions`, default 60; unfinished horizons read `not yet`; click a day for an event study).

## How to get to it (user POV)

- Click **Risk** in the left rail, or `Full risk analysis` under the Dashboard risk row. The Performance additions sit on the Performance page.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`, and the network reaches Yahoo and bankofcanada.ca. The first risk request fetches 17 market histories plus one per holding (allow up to 4 minutes). A failed T-bill fetch silently sets the risk-free rate to 0.

Steps:

- `qtv browse risk .claude/skills/verify-qtfolio/journeys/risk.mjs` imports the synthetic export when the instance is fresh (it skips the import on a re-run), then walks every sub-feature above and notes the rendered values.
- **Identities** to assert through `qtv api` (they hold whatever the prices):
  - `/api/risk/var`: Σ `components[].var` = `methods.normal.var`, and Σ `components[].es` = `methods.historical.es`.
  - `/api/risk/covariance`: Σ `rows[].pct` = 1, Σ `rows[].ctr` = `vol`, and Σ `rows[].bet_share` = 1.
  - `POST /api/risk/stress/custom` with body `{"shocks":{"vix":15,"credit":-3}}`: Σ `causal.channels[].contribution` = `causal.portfolio` = `direct + indirect`. The API returns only the top 12 channels, so shocks with more paths (the default `rates: 50` has 16) break the channel sum by design; `direct + indirect` = `portfolio` always holds.
  - `POST /api/risk/stress/reverse`: Σ `assets[].move × weight` = −`loss`.
- **Trade decisions by hand.** On the synthetic book, the 60-session value-add of 2026-06-02 = 3300 × (XEQT.TO close at +60 sessions / close on 06-02 − 1) + 3000 × (the same ratio for SHOP.TO), using the `1d_yf` closes and the SPY calendar. It must match the row to the cent. TSX closes are forward-filled onto the SPY calendar, so on a TSX-only holiday the "+60 session" close is the previous TSX close.

## Gotchas

- Shock, reverse, and impulse-response results keep the previous answer on screen, dimmed, while the new one computes. Wait for no `[aria-busy="true"]` before reading values, or you read the old shock. `Risk-adjusted performance` also keeps old numbers while the range or benchmark changes, without `aria-busy`; wait for its `Vs <SYMBOL> (CAD)` header.
- Match `Filtered historical` with `exact: true`: the backtest note contains "filtered historical VaR" once the book has 300+ sessions.
- The synthetic book is all CAD, so FX effects and `USD cash` rows never appear. Seeing them needs a USD fixture.
- The event study needs 40+ sessions of history before the date, so the synthetic 2026-06-02 decision shows the "needs 40+ sessions" message.
- The favicon 404 in `console.log` predates this feature.
