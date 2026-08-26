# qtfolio

A local, single-user, read-only portfolio terminal for a Questrade account. It pulls live account data from the Questrade API, reconstructs full portfolio history from activities, charts performance the methodologically correct way (deposits separated from market growth), and layers on per-stock technical charts and an AI analyst grounded in the live portfolio.

Everything runs on your machine, bound to `127.0.0.1`. No hosting, no auth, no trade execution — the only things that leave your computer are calls to the Questrade, Yahoo Finance, and Anthropic APIs.

## Features

- **Dashboard** — live holdings, allocation donut, P&L, sparklines, and an **AI Analyst**: Claude (with web search) writes a briefing on your actual positions — upcoming earnings, Fed/macro dates, risks — and answers follow-up questions in a grounded chat, tuned by an editable investor profile.
- **Performance** — historical portfolio value reconstructed backward from today's live positions through every recorded activity, with time-weighted returns and indexed comparison overlays vs. your holdings and benchmarks. Deposits/withdrawals are never counted as gains.
- **Stock detail** — TradingView `lightweight-charts` candles + volume for any ticker (not just holdings), with EMA / Bollinger Bands / VWAP / anchored VWAP overlays, RSI and MACD panes, your average-cost line, buy/sell trade markers, and a relative-strength mini-chart.
- **Resilient syncing** — APScheduler jobs with a catch-up pass on startup, so the app survives being closed for days; weekly DB backups; Questrade refresh-token chain managed and persisted in SQLite with careful single-flight rotation.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async) + SQLite, Alembic, APScheduler |
| Frontend | React 18 + Vite + TypeScript, Tailwind v4, TanStack Query |
| Charts | `lightweight-charts` v5 (candles/indicators), Recharts (donut, sparklines) |
| AI | Anthropic API — Claude Fable 5 with the web search server tool, SSE streaming |

Ledger money is stored as integer cents (micro-units for unit prices), never floats. Timestamps are stored in UTC and rendered in `America/Toronto`.

## Getting started

Prereqs: [`uv`](https://docs.astral.sh/uv/) (provisions Python 3.12 itself), Node 18+, and a Questrade account with API access.

```sh
make install   # backend uv sync + frontend npm install
make dev       # backend on 127.0.0.1:8000, Vite on 127.0.0.1:5173
```

Open http://127.0.0.1:5173 — an empty database routes you to Settings onboarding:

1. In Questrade's **API Centre**, generate a manual refresh token and paste it into Settings. Tokens live in the database and rotate automatically from then on; they are never read from env vars.
2. Pick the account(s) to track.
3. Run the bootstrap backfill to pull the account's full activity history. If Questrade's activities API can't cover the account's whole life, import the missing range from a Questrade CSV/XLSX activities export on the same page.

For the AI Analyst, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (get one at [console.anthropic.com](https://console.anthropic.com); usage-billed). Everything else works without it.

## Development

```sh
make test          # backend pytest suite
make simulate-gap  # rewind the keepalive job 2 days to exercise startup catch-up
```

- `backend/` — FastAPI app: `app/questrade/` (API client + token manager), `app/services/` (reconstruction, TWR, indicators, activities), `app/jobs/` (scheduler), `app/ai/` (analyst).
- `frontend/` — SPA: `src/pages/` (Dashboard, Performance, StockDetail, Settings), `src/components/`.
- `data/` — gitignored runtime state: SQLite DB, token backup, weekly backups.

Migrations run automatically at backend startup (`alembic upgrade head`). The authoritative design doc is [`SPEC.md`](SPEC.md).
