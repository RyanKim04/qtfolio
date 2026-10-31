# qtfolio

A local, single-user, read-only portfolio terminal. It rebuilds portfolio history from brokerage activity exports and charts performance with deposits kept separate from market gains. It also prices holdings live, draws technical charts for any ticker, and tracks TFSA contribution room.

It runs only on your machine, bound to `127.0.0.1`. There is no hosting, no login and no trading. The only network traffic is market data from Yahoo Finance and USD/CAD rates from the Bank of Canada.

## Features

- **Dashboard.** Live holdings from Yahoo's streaming quotes (extended hours included), an allocation donut, day and open P&L, 30-day sparklines, and TFSA room.
- **Performance.** Daily portfolio value rebuilt from every recorded activity. Shows time- and money-weighted returns, drawdown and volatility over market sessions, best and worst days with a per-position breakdown, P&L by position, and benchmark comparisons. Deposits and withdrawals never count as gains.
- **Stock detail.** Candles and volume for any ticker (TradingView `lightweight-charts`), with EMA, Bollinger Bands, VWAP and anchored VWAP overlays, RSI and MACD panes, your average-cost line, buy and sell markers, and a relative-strength chart.
- **Import.** Reads the Wealthsimple activities CSV. Any export saved to `~/Downloads` imports automatically, or you can drop one on the import card to preview it first. Dedup counts identical rows, so overlapping exports never double-count. History from a closed Questrade account is kept as frozen snapshots.
- **Jobs.** APScheduler, plus a catch-up pass at startup that runs anything missed while the laptop was off. The database is backed up weekly.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async) + SQLite, Alembic, APScheduler, yfinance |
| Frontend | React 18 + Vite + TypeScript, Tailwind v4, TanStack Query |
| Charts | `lightweight-charts` v5 (candles, indicators), Recharts (performance, donut) |

Ledger money is stored as integer cents and unit prices as integer micro-units, never floats. Timestamps are stored in UTC and shown in `America/Toronto`.

## Getting started

You need [`uv`](https://docs.astral.sh/uv/) (it installs Python 3.12 itself) and Node 18+. No API keys.

```sh
make install   # backend uv sync + frontend npm install
make dev       # backend on 127.0.0.1:8000, Vite on 127.0.0.1:5173
```

Open http://127.0.0.1:5173. With an empty database the app opens Settings. In Wealthsimple, go to Activity → Download activities, pick the account and choose CSV. The file imports from `~/Downloads` on its own. Download a new export after you trade.

## Development

```sh
make test   # backend pytest suite
```

- `backend/` is the FastAPI app: `app/services/` (reconstruction, series and TWR, book, Wealthsimple parser, TFSA, indicators), `app/api/`, `app/jobs/` (scheduler) and `app/yf.py` (market data).
- `frontend/` is the single-page app: `src/pages/` (Dashboard, Performance, StockDetail, Settings) and `src/components/`.
- `data/` is gitignored runtime state: the SQLite database and weekly backups.

The repo is public. Real financial data (balances, contribution history, account ids) stays in the database or in gitignored `*_private.*` files, and tests use synthetic numbers. Migrations run at backend startup (`alembic upgrade head`).
