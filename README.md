# qtfolio

A local, single-user, read-only portfolio terminal. It reconstructs full portfolio history from brokerage activity exports, charts performance the methodologically correct way (deposits separated from market growth), prices everything live, and layers on per-stock technical charts and a TFSA contribution-room tracker.

Everything runs on your machine, bound to `127.0.0.1`. No hosting, no auth, no trade execution — the only things that leave your computer are market-data requests to Yahoo Finance and the Bank of Canada FX feed.

## Features

- **Dashboard** — live holdings (Yahoo streaming quotes, extended hours included), allocation donut, day/open P&L, sparklines, TFSA room.
- **Performance** — historical value reconstructed from every recorded activity, time- and money-weighted returns, drawdown/volatility on market sessions, best/worst-day breakdowns, P&L by position, and comparison overlays vs. benchmarks. Deposits/withdrawals are never counted as gains.
- **Stock detail** — TradingView `lightweight-charts` candles + volume for any ticker, with EMA / Bollinger Bands / VWAP / anchored VWAP overlays, RSI and MACD panes, your average-cost line, buy/sell trade markers, and a relative-strength mini-chart.
- **Data in** — Wealthsimple activities CSV import (two-phase preview/commit, multiset dedup so overlapping exports are safe). A retired Questrade account's history is kept as frozen snapshots.
- **Resilient jobs** — APScheduler with a catch-up pass on startup, so the app survives being closed for days; weekly DB backups.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async) + SQLite, Alembic, APScheduler, yfinance |
| Frontend | React 18 + Vite + TypeScript, Tailwind v4, TanStack Query |
| Charts | `lightweight-charts` v5 (candles/indicators), Recharts (performance, donut, sparklines) |

Ledger money is stored as integer cents (micro-units for unit prices), never floats. Timestamps are stored in UTC and rendered in `America/Toronto`.

## Getting started

Prereqs: [`uv`](https://docs.astral.sh/uv/) (provisions Python 3.12 itself) and Node 18+. No API keys are needed.

```sh
make install   # backend uv sync + frontend npm install
make dev       # backend on 127.0.0.1:8000, Vite on 127.0.0.1:5173
```

Open http://127.0.0.1:5173 — an empty database routes you to Settings. In Wealthsimple: Activity → Download activities → pick the account → CSV, then drop the file on the import card. Re-import a fresh export after you trade.

## Development

```sh
make test   # backend pytest suite
```

- `backend/` — FastAPI app: `app/services/` (reconstruction, series/TWR, book, Wealthsimple parser, TFSA, indicators), `app/api/`, `app/jobs/` (scheduler), `app/yf.py` (market data).
- `frontend/` — SPA: `src/pages/` (Dashboard, Performance, StockDetail, Settings), `src/components/`.
- `data/` — gitignored runtime state: SQLite DB and weekly backups.

The repo is public: real financial data (balances, contribution history, account ids) stays in gitignored `*_private.*` files or the DB — tests use synthetic figures. Migrations run automatically at backend startup (`alembic upgrade head`).
