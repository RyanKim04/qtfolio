# qtfolio

Local, single-user, read-only Questrade portfolio terminal. **The authoritative design doc is `SPEC.md` (Build Spec v2)** — follow it in milestone order (§15) and re-read §16 (edge cases) before starting each milestone. Where the spec decides something, don't relitigate it; genuinely ambiguous points get the simpler option plus a `// DECISION:` comment.

## Status

- **M1 (Foundation + live dashboard): built and accepted.**
- **M2 (History + Performance page): built.** Bootstrap has been run against the real account:
  regime = back_adjusted, activities cover the account's full life (born 2024-05-20), CSV import
  exists but wasn't needed. Gotcha discovered in production: Questrade's API returns some trade
  rows with EMPTY symbol AND symbolId — `history.repair_missing_tickers` resolves them from the
  description (registry key match + shrinking-prefix symbol search); runs in bootstrap and
  activities_sync. Unresolvable rows surface as reconciliation warnings, never silently dropped.
- **M3 (stock detail charts): built.** Owner-adjusted scope: NO support/resistance
  (deferred to §17 — owner plans a sentiment+quant low/high detection model later).
  lightweight-charts v5 candles+volume, EMA/BB/VWAP/AVWAP overlays, RSI/MACD panes,
  avg-cost line + trade markers, RS mini-chart, Position & Levels panels; indicators
  pandas-verified. Any ticker works read-only via symbol search.
- **M4 REPLACED (2026-08-13, owner decision): the entire news/sentiment stack was deleted**
  (News page, Finnhub/AV/yfinance news adapters, FinBERT/torch, attention spikes, news_items/
  reddit_posts tables). In its place: **AI Analyst on the Dashboard** — Claude Fable 5
  (`claude-fable-5`, Anthropic API, web_search_20260209 server tool, SSE streaming, Opus 4.8
  refusal fallback) in `backend/app/ai/` + `api/ai.py` + `frontend/src/components/AiAnalyst.tsx`.
  Run-analysis briefing (researches earnings/Fed/macro dates live) + grounded chat, both fed the
  live portfolio context (`ai/context.py`) and the owner's investor profile (settings key
  `investor_profile`, editable in Settings; default: aggressive tech, small-seed philosophy,
  sustainable aggression). NO advisory disclaimers — owner's explicit rule, enforced in the
  system prompt. Needs ANTHROPIC_API_KEY in .env. Last analysis persisted in settings
  (`ai_last_analysis`). Owner plans a Performance-tab agent later; prediction modeling still last.
- M5 (analyst/polish): not started.
- Design: Stripe-style light theme (SPEC.md §14 v2.1) — white cards, Inter, blurple accent.

## Layout

- `backend/` — Python 3.12 (managed by `uv`), FastAPI + SQLAlchemy 2 (async, SQLite) + Alembic + APScheduler.
- `frontend/` — Vite + React 18 + TypeScript + Tailwind v4 + TanStack Query.
- `data/` — gitignored runtime state: `qtfolio.db`, `qt_token_backup.json`, `backups/`.

## Commands

- `make install` — backend `uv sync` + frontend `npm install`.
- `make dev` — backend on 127.0.0.1:8000 + Vite on 127.0.0.1:5173 (proxies `/api`).
- `make test` — backend pytest (token manager, catch-up framework).
- `make simulate-gap` — rewind `token_keepalive`'s last success by 2 days to test startup catch-up.

## Conventions (from SPEC.md — the non-negotiables)

- Ledger money = INTEGER cents; unit prices / quantities = INTEGER micro-units. Never SQLAlchemy `Numeric` on SQLite. Floats only for chart series and display.
- Timestamps stored UTC; scheduler + rendering in `America/Toronto`.
- Questrade tokens live in the DB (`qt_tokens`), never in env vars; never log token values. All refreshes go through `TokenManager` (single lock, persist-before-use, 30s timeout + one same-token retry).
- **Questrade returns a BARE "400 Bad Request" (no invalid_grant JSON) for a consumed refresh
  token** → any 400 from the exchange = chain lost = broken banner (learned 2026-08-14). The
  startup refresh skips if the chain rotated <30 min ago — uvicorn --reload restarts once burned
  the chain by killing an in-flight exchange during rapid dev edits.
- Server binds 127.0.0.1 only. No advisory disclaimers anywhere in the UI.
- Migrations via Alembic; `alembic upgrade head` runs automatically at backend startup.

## Setup (first run)

1. `make install` (needs Homebrew's `uv`; it provisions Python 3.12 itself).
2. `make dev`, open http://127.0.0.1:5173 — empty DB routes to Settings onboarding.
3. Questrade API Centre → generate a manual refresh token → paste it in Settings.
4. Pick tracked accounts. (Step 3 of onboarding, bootstrap backfill, arrives in M2.)

`.env` (repo root) holds Finnhub/Reddit keys — unused until M4; see `.env.example`.
