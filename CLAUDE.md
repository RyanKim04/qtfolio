# qtfolio

Local, single-user, read-only portfolio terminal (Wealthsimple CSV plus frozen Questrade history). The original build spec (`SPEC.md`) was retired 2026-10-30. This file and the module docstrings are the source of truth; the spec survives in git history at `58e127c`. Owner decisions recorded here aren't relitigated. Ambiguous points get the simpler option plus a `// DECISION:` comment.

## Status

- **M1 (foundation, live dashboard).** Built and accepted.
- **M2 (history, Performance page).** Built. Questrade-era activities were bootstrapped from the API
  (and repaired and deduped there). That data is now frozen in the DB.
- **M3 (stock detail charts).** Built. The owner cut support/resistance from scope; it waits on a
  sentiment+quant low/high detection model planned for later. Ships lightweight-charts v5
  candles+volume, EMA/BB/VWAP/AVWAP overlays, RSI/MACD panes, avg-cost line and trade markers, an RS
  mini-chart, and Position and Levels panels. Indicators are verified against pandas. Any ticker works
  read-only through symbol search.
- **M4 replaced (2026-08-13, owner decision).** The whole news/sentiment stack was deleted: News page,
  Finnhub/AV/yfinance news adapters, FinBERT/torch, attention spikes, and the news_items/reddit_posts
  tables. The AI Analyst that replaced it was also removed (2026-10-29, owner decision). There is no
  `ai/` package, `/api/ai/*`, Dashboard panel, investor-profile editor or `anthropic` dependency, and
  the stale settings rows were dropped 2026-10-30. Prediction modeling is still last.
- **Broker move (2026-10-27).** The owner moved the TFSA from Questrade to Wealthsimple (in-kind, 10/19).
  - Questrade API code was removed (2026-10-30, owner decision): client, token manager/keepalive, QT
    sync/bootstrap/snapshot jobs, QT CSV import, regime probe, token UI and the `qt_tokens` table.
    Questrade history stays in every series as frozen data. `accounts.broker = 'questrade'`, its
    activities, and its balance/position snapshots (closing zero snapshot 10/27) anchor its part of
    `history.recompute`.
  - Wealthsimple has no API. The owner chose the official activities CSV over unofficial GraphQL
    clients, so no trading-capable token is stored. `services/wealthsimple.py` parses it, and
    `/import/activities` auto-detects it. `services/ws_watch.py` auto-imports any WS export that lands
    in `~/Downloads`: it detects the header, keeps a seen-list of content digests, and never touches the
    files. The unofficial `ws-api` was tried and rolled back 2026-10-30 (owner decision).
  - WS accounts are `accounts.broker = 'wealthsimple'` ledger accounts. Their state comes from their
    full-life activities (`reconstruction.LedgerState`) and is combined with the QT snapshot accounts in
    `history.recompute`. Broker-to-broker transfer legs are matched and made internal
    (`match_transfers`: zero flow, same day).
  - The Dashboard and stock position come from the reconstructed book (`services/book.py`, average cost
    across both brokers). yfinance supplies all market data: quotes, OHLC under the legacy interval
    names, and symbol resolution.
  - WS export quirks: CAD symbols get QT's `.TO` suffix (WS `GOOG`/CAD is the CDR, `GOOG.TO`).
    `SecurityTransfer.net_cash_amount` is book value, not cash. `Dividend.quantity` repeats the cash amount.
  - Live quotes include extended hours (`YfService.quotes`: the newest post/pre-market trade after the
    regular close wins; TSX has none). The Performance charts' today point is live (the dashboard total,
    `performance._series_maps`); stored `series_daily` stays close-based.
  - Live prices come from Yahoo's streamer WebSocket (`YfService.stream_forever`, about 1–3 s lag). The
    polled quoteSummary lags 1–2 min and can freeze on a symbol for 20+ min (learned 2026-10-30). Polled
    quotes (every 15 s inside 04:00–20:00 ET on weekdays, 5 min otherwise) remain the fallback and the
    prev-close baseline; the newer of tick and poll wins. The Dashboard polls every 5 s, Performance every 30 s.
- **TFSA room tracker (2026-10-29).** `services/tfsa.py` (pure engine), `api/tfsa.py`, the Dashboard's
  `components/TfsaRoom.tsx` and a Settings panel. `tfsa_transactions` are hand-entered across every
  institution and are not derived from broker activities; type `transfer` is TFSA→TFSA with no room
  effect. Room walks forward from the CRA-confirmed Jan 1 anchor (settings `tfsa_anchor`, kept only in
  the DB, never in code or git). Limits live in `tfsa_annual_limits`; null means not announced, and the
  projection borrows the last known limit and flags it. Recording previews warnings first
  (over-contribution, same-year re-contribution). From March on, the card asks to re-anchor on CRA's new
  figure. `test_tfsa.py` covers the engine on a synthetic history. The owner's real history (which
  reproduces CRA's confirmed room) is in the gitignored `test_tfsa_private.py`.
- M5 (analyst, polish): not started. Its empty `analyst_*` tables were dropped 2026-10-30, and the
  disabled Sector allocation tab that pointed at it was removed.
- Design: Stripe-style light theme, see Design below.

## Layout

- `backend/`: Python 3.12 (managed by `uv`), FastAPI, SQLAlchemy 2 (async, SQLite), Alembic, APScheduler.
- `frontend/`: Vite, React 18, TypeScript, Tailwind v4, TanStack Query.
- `data/`: gitignored runtime state, `qtfolio.db` and `backups/`.

## Commands

- `make install`: backend `uv sync` and frontend `npm install`.
- `make dev`: backend on 127.0.0.1:8000 and Vite on 127.0.0.1:5173 (proxies `/api`).
- `make test`: backend pytest.

## Conventions (the non-negotiables)

- Ledger money is INTEGER cents; unit prices and quantities are INTEGER micro-units. Never use SQLAlchemy `Numeric` on SQLite. Floats only for chart series and display.
- Timestamps are stored in UTC; the scheduler and rendering use `America/Toronto`.
- Every stored row, QT era included, was deduped on activity hashes (`activities.tuple_key` plus
  occurrence index). Changing the tuple breaks dedup against existing rows.
- **The GitHub repo is public.** Never commit real financial data: balances, contribution or trade
  history, CRA room, account ids, exports. Tests use synthetic numbers. Anything real goes in a
  gitignored `*_private.*` file or `private/` dir (e.g. `test_tfsa_private.py`), the DB, or `misc/`.
- The server binds 127.0.0.1 only and refuses non-local `Host`/`Origin` headers (`main.py`), because
  binding alone doesn't stop a website POSTing to it or DNS rebinding. No advisory disclaimers anywhere in the UI.
- Migrations use Alembic; `alembic upgrade head` runs at backend startup.

## Edge cases (still live)

- Activities have no stable row ids, and identical same-day fills are legal, so dedup is multiset-aware
  (occurrence-indexed hashes). Unknown activity types are logged, never guessed.
- Never assume the activity history covers an account's life. "Since inception" needs demonstrated
  coverage, which is why reconstruction walks backward from ground truth.
- USD cash, dividends and deposits are converted to CAD with dated FX for flows and valuation; the native currency is stored too.
- Norbert's Gambit: DLR/DLR.U journal legs are transfers (not P&L, not contributions). They post up to
  about 2 business days apart and are pair-matched with tolerance.
- Splits: broker-posted split journals must not be applied again on top of yfinance split factors.
- Market holidays: forward-fill closes per listing exchange (TSX and NYSE calendars differ).
- One data source per chart. Never mix sources on the same series.
- yfinance is unofficial: use executors, retries, cached last-good data and visible staleness, and never crash.
- SQLite runs in WAL mode; sync libraries always run in executors.
- Jobs: catch-up on startup is the main execution path on a laptop; cron firing is a bonus.

## Design

Private market dashboard in a Stripe-style fintech look: light, hairline borders, desktop-only (≥ ~1280 px).
Tokens live in `frontend/src/styles/index.css`: page `#F6F8FB`, card `#FFFFFF`, border `#E3E8EE`, text `#1A1F36`,
muted `#6A7383`, accent blurple `#635BFF`, up `#17803D`, down `#DF1B41`, capital-in `#8792A2`. Green and red are
reserved for P&L; charts use blues, purples and grays. Inter everywhere with tabular numerals, and dense cards
(13–14 px data). The key visual is the Performance band between value and capital-in. Motion only on number
ticks and crosshairs. Copy is plain and in sentence case, with no em dashes and no milestone names (M5, etc.).
Errors say what happened and what to do next.

## Deferred (don't build unprompted)

- Auto support/resistance, superseded by the owner's planned low/high detection model (about half sentiment
  with recency decay, half price/candle features). SEC EDGAR 8-K/6-K event flags as model features.
- Volume profile, click-to-anchor AVWAP (the 52w-low preset ships), sector allocation and ETF sector look-through.
- Always-on deployment (mini-PC/VPS + Tailscale), offsite streaming backups (Litestream), responsive layout.

## Setup (first run)

1. `make install` (needs Homebrew's `uv`, which installs Python 3.12 itself).
2. `make dev`, then open http://127.0.0.1:5173. With no history it opens Settings.
3. In Wealthsimple, go to Activity → Download activities → CSV. The file auto-imports from `~/Downloads`, or you can drop it on the import card.

No `.env` or API keys are needed.
