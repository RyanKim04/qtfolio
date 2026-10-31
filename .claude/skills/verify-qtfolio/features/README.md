# qtfolio verification map

This directory is the maintained source for verifying qtfolio's user-facing behavior. Read this index before you drive the app, then follow the matching feature file as the recipe. All commands use `qtv` = `.claude/skills/verify-qtfolio/bin/qtv`, run from the repo root.

## Baseline preconditions

- `qtv up` printed `READY`, and `qtv doctor` ends in `DOCTOR OK` for that run. Never drive `make dev` on 8000/5173: it serves the owner's real data.
- A fresh run is empty. Every feature except `import-activities` needs the synthetic export imported first (`import-upload` journey, or `qtv drop-export` and then wait for `wealthsimple.last_import`).
- The synthetic book is TFSA `VERIFY1`: 100 `XEQT.TO` at avg cost $33.00, 20 `SHOP.TO` at avg cost $150.00, cash $4,200.00 CAD, history from 2026-06-01.
- The network is up (Yahoo and FX). Without it, imports fail to resolve symbols and charts are empty. That is a failed run, not a skip.

## Driving conventions

- Use one `step()` per user action. Scope locators to a section by its `<h2>` heading. Name buttons and links by visible text, and inputs by placeholder.
- Wait on expected text or roles, never on fixed sleeps. Imports and recomputes can take up to ~90 s.
- Assert deterministic facts (tickers, qty, avg cost, cash, counts, dates), never live prices or absolute P&L. Do assert P&L identities (lines sum to totals) to the cent; see Performance.
- Confirm side effects with `qtv sql` / `qtv api`, read-only.

## Proof and skip reporting

- Evidence lands in `data/verify-evidence/<run id>/` and survives `qtv down`.
- UI proof is the step screenshots plus ARIA snapshots, together with `notes.txt` holding the asserted values.
- Mutation proof adds a second read-only view: a `qtv sql` row, `/api/health`, or a backend log line.
- Name the entry point you drove. Report an unreachable entry point with the command you tried and the unmet precondition. Never count it as verified through another path.

## Feature entry contract

Each feature file starts with an H1 and a one-paragraph description, then has exactly four H2s in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with qtv`, `Gotchas`.

## Features

- [Import activities](./import-activities.md) covers upload preview and commit, the Downloads auto-import, dedup on re-import, and onboarding redirect. Proven by `journeys/import-upload.mjs` and `journeys/import-reject.mjs` (reject + dedup); watcher retry and cross-origin refusal have recipes in the file.
- [Dashboard](./dashboard.md) covers the stat tiles, holdings table, cash line, allocation donut, CAD/USD toggle, and row → stock page. Proven by `journeys/dashboard.mjs`. The risk row and Risk/Beta columns are in Risk analytics.
- [Performance](./performance.md) covers the page shell: the five chart modes, the range selector, stats, and P&L by position. Proven by `journeys/pnl-lines.mjs` (fresh instance) and `journeys/perf-tabs.mjs`. The risk-adjusted panel, Drawdown/Rolling content, monthly returns and trade decisions are in Risk analytics. P&L numbers are proven by `journeys/pnl-lines.mjs` (table identities to the cent, defaults, XEQT.TO in Compare) plus the hand-checked cash lines and FX coverage check in the file.
- [Risk analytics](./risk.md) covers the Dashboard risk row, the Risk tab (decomposition, correlation, factors, VaR and backtests, stress tests, causal model), and the Performance risk additions. Proven by `journeys/risk.mjs` plus the API identities and hand-checked trade value-add in the file.
- [Stock detail](./stock-detail.md) covers candles, intervals, overlays and indicator panes, the Position and Levels panels, the RS mini-chart, and symbol search. Proven by `journeys/stock-detail.mjs`.
- [TFSA room](./tfsa-room.md) covers the Settings anchor and limits, the Dashboard room card, recording with warnings, and delete. Proven by `journeys/tfsa.mjs`.
