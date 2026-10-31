# qtfolio

Local, single-user, read-only portfolio terminal (Wealthsimple CSV + frozen Questrade history). **The original design doc is `SPEC.md` (Build Spec v2; its Questrade-API sections are historical)** — follow it in milestone order (§15) and re-read §16 (edge cases) before starting each milestone. Where the spec decides something, don't relitigate it; genuinely ambiguous points get the simpler option plus a `// DECISION:` comment.

## Status

- **M1 (Foundation + live dashboard): built and accepted.**
- **M2 (History + Performance page): built.** Questrade-era activities were bootstrapped from the
  API (and repaired/deduped there); that data is now frozen in the DB.
- **M3 (stock detail charts): built.** Owner-adjusted scope: NO support/resistance
  (deferred to §17 — owner plans a sentiment+quant low/high detection model later).
  lightweight-charts v5 candles+volume, EMA/BB/VWAP/AVWAP overlays, RSI/MACD panes,
  avg-cost line + trade markers, RS mini-chart, Position & Levels panels; indicators
  pandas-verified. Any ticker works read-only via symbol search.
- **M4 REPLACED (2026-08-13, owner decision): the entire news/sentiment stack was deleted**
  (News page, Finnhub/AV/yfinance news adapters, FinBERT/torch, attention spikes, news_items/
  reddit_posts tables). The AI Analyst that replaced it was **itself removed (2026-10-29, owner
  decision)**: no `ai/` package, `/api/ai/*`, Dashboard panel, investor-profile editor or `anthropic`
  dependency (stale settings rows dropped 2026-10-30). Prediction modeling is still last.
- **BROKER MOVE (2026-10-27): owner moved the TFSA from Questrade to Wealthsimple (in-kind, 10/19).**
  **Questrade API code removed (2026-10-30, owner decision)** — client, token manager/keepalive, QT
  sync/bootstrap/snapshot jobs, QT CSV import, regime probe, token UI, `qt_tokens` table. Its history
  stays in every series as frozen data: `accounts.broker = 'questrade'` + activities + balance/position
  snapshots (closing zero snapshot 10/27) anchor its part of `history.recompute`. Wealthsimple has no API — owner chose the **official activities CSV** over unofficial GraphQL
  clients (no trading-capable token stored). `services/wealthsimple.py` parses it (auto-detected by
  `/import/activities`; **`services/ws_watch.py` auto-imports any WS export that lands in `~/Downloads`** — header-detected,
  content-digest seen-list, files never touched; unofficial `ws-api` was tried and rolled back 2026-10-30, owner decision); WS accounts are `accounts.broker = 'wealthsimple'` **ledger accounts** whose
  state is derived from their full-life activities (`reconstruction.LedgerState`), composed with QT
  snapshot accounts in `history.recompute`. Broker-to-broker transfer legs are matched and made
  internal (`match_transfers`: zero flow, same day). Dashboard/stock position come from the
  reconstructed book (`services/book.py`, average cost across both brokers) + **yfinance** for all
  market data (quotes, OHLC under the legacy interval names, symbol resolution). WS export quirks: CAD symbols get QT's `.TO` suffix (WS `GOOG`/CAD = the CDR = `GOOG.TO`);
  `SecurityTransfer.net_cash_amount` is book value, not cash; `Dividend.quantity` repeats the cash amount.
  Live quotes include **extended hours** (`YfService.quotes`: newest post/pre-market trade after the
  regular close wins; TSX has none), and the Performance charts' **today point is live** (dashboard
  total, `performance._series_maps`) — stored `series_daily` stays close-based.
  Live prices come from Yahoo's **streamer WebSocket** (`YfService.stream_forever`, ~1–3 s lag;
  the polled quoteSummary lags 1–2 min and can freeze on a symbol for 20+ min — learned 2026-10-30).
  Polled quotes (15 s inside 04:00–20:00 ET weekdays, 5 min otherwise) remain the fallback and the
  prev-close baseline; the newest of tick vs poll wins. Dashboard polls at 5 s, Performance at 30 s.
- **TFSA room tracker (2026-10-29)**: `services/tfsa.py` (pure engine) + `api/tfsa.py` + Dashboard
  `components/TfsaRoom.tsx` + Settings panel. Hand-entered `tfsa_transactions` across every institution
  (type `transfer` = TFSA→TFSA, no room effect; NOT derived from broker activities). Room walks forward
  from the CRA-confirmed Jan 1 anchor (settings `tfsa_anchor` — kept in the DB only, not in code/git);
  limits in `tfsa_annual_limits` (null = not announced → projection borrows the last known limit,
  flagged). Recording previews warnings first (over-contribution, same-year re-contribution);
  March+ reminder to re-anchor on CRA's new figure. `test_tfsa.py` covers the engine on a synthetic
  history; the owner's real history (reproducing CRA's confirmed room) is in the gitignored
  `test_tfsa_private.py`.
- M5 (analyst/polish): not started (its empty `analyst_*` tables were dropped 2026-10-30).
- Design: Stripe-style light theme (SPEC.md §14 v2.1) — white cards, Inter, blurple accent.

## Layout

- `backend/` — Python 3.12 (managed by `uv`), FastAPI + SQLAlchemy 2 (async, SQLite) + Alembic + APScheduler.
- `frontend/` — Vite + React 18 + TypeScript + Tailwind v4 + TanStack Query.
- `data/` — gitignored runtime state: `qtfolio.db`, `backups/`.

## Commands

- `make install` — backend `uv sync` + frontend `npm install`.
- `make dev` — backend on 127.0.0.1:8000 + Vite on 127.0.0.1:5173 (proxies `/api`).
- `make test` — backend pytest.

## Conventions (from SPEC.md — the non-negotiables)

- Ledger money = INTEGER cents; unit prices / quantities = INTEGER micro-units. Never SQLAlchemy `Numeric` on SQLite. Floats only for chart series and display.
- Timestamps stored UTC; scheduler + rendering in `America/Toronto`.
- Activity hashes (`activities.tuple_key` + occurrence index) are what every stored row — QT era
  included — was deduped on; changing the tuple breaks dedup against existing rows.
- **The GitHub repo is PUBLIC.** Never commit real financial data — balances, contribution/trade
  history, CRA room, account ids, exports. Tests use synthetic numbers; anything real goes in a
  gitignored `*_private.*` file or `private/` dir (e.g. `test_tfsa_private.py`), the DB, or `misc/`.
- Server binds 127.0.0.1 only. No advisory disclaimers anywhere in the UI.
- Migrations via Alembic; `alembic upgrade head` runs automatically at backend startup.

## Setup (first run)

1. `make install` (needs Homebrew's `uv`; it provisions Python 3.12 itself).
2. `make dev`, open http://127.0.0.1:5173 — no history routes to Settings.
3. Wealthsimple → Activity → Download activities → CSV — it auto-imports from `~/Downloads` (or drop it on the import card).

No `.env` or API keys are needed.
