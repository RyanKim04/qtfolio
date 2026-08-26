# qtfolio — Personal Portfolio Terminal (Build Spec v2)

Working name `qtfolio` (rename freely). A local, single-user, read-only portfolio terminal for one Questrade account holder. It pulls account data from the Questrade API, reconstructs portfolio history, charts performance the methodologically correct way (deposits separated from market growth), and layers on news, Reddit/news sentiment, analyst consensus, and a proper per-stock charting page.

This spec is written for Claude Code. Follow it in milestone order (§15). Where the spec makes a decision, don't relitigate it; where something is genuinely ambiguous, pick the simpler option and leave a `// DECISION:` comment.

v2 incorporates a feasibility review. Changelog below; §16 is still the pre-milestone re-read.

---

## 0. Changelog v1 → v2

- **Reconstruction reversed** (§6): now anchors at today's live positions/balances and walks activities backward. Forward-from-zero silently produced wrong history whenever the account predates the activities window. CSV import promoted from "fallback" to load-bearing (§4.3).
- **TWR formula corrected** (§7) to a consistent start-of-day flow convention; fixes the day-one divide-by-zero.
- **Split handling made conditional** (§6.0) on an empirical check of whether Questrade daily candles are back-adjusted, with a guard against double-applying broker-posted split journal rows.
- **Activity dedup is multiset-aware** (§4.2): rows have no stable IDs and identical same-day fills are legal — occurrence-indexed hashes, count-based cross-sync dedup.
- **Ground truth rescales instead of overriding** (§6.6): no step discontinuity at the reconstruction/snapshot seam, so no phantom TWR return.
- **Token manager hardened** (§4.1): unconditional refresh on every process start; defined timeout + single-retry rule for the exchange endpoint.
- **Catch-up-on-startup is now the primary job execution path** (§13); new weekly `db_backup` job.
- **Ledger money = INTEGER cents** (§2, §5): SQLAlchemy `Numeric` on SQLite silently stores floats.
- **Reddit collection switched to uniform `new()` scans** (§10); `search()` banned for measurement. News/sentiment **ticker ignore list** (default DLR, DLR.U) and per-ticker news flood control (§8.3).
- **Google News URLs treated as opaque**; title-first dedup (§3, §8.3).
- **Concurrency rule** (§2): yfinance / PRAW / transformers always run in executors.
- **Cut to §17 (v1.1 parking lot)**: volume profile, click-to-anchor AVWAP, Reddit comment/daily-thread scanning, always-on deployment, offsite streaming backups, mobile layout, ETF sector look-through.
- **New non-goals** (§1): local-only process, desktop-only layout.

---

## 1. Goals and non-goals

**Goals**
- Live view of current portfolio structure (holdings, allocation, P&L).
- Historical portfolio value chart with deposits/withdrawals clearly separated from market performance, and an indexed comparison overlay vs. individual holdings and benchmarks.
- News feed scoped to owned tickers + a second tab for general tech/market news.
- Per-stock detail page: candlesticks, curated indicator set, auto-detected support/resistance, analyst consensus + price targets, and sentiment from news + Reddit.
- Runs locally, starts with one command, survives restarts (SQLite persistence).

**Non-goals (v1)**
- No trade execution (Questrade's API doesn't allow it for retail accounts anyway).
- No multi-user, no auth beyond localhost binding, no notifications.
- **No deployment/hosting work.** v1 is a local process on the owner's machine, bound to 127.0.0.1, started manually (or via an OS auto-start service — owner's choice, out of scope). Nothing in the design may assume the process runs continuously; see startup semantics (§13). Always-on deployment is §17.
- **Desktop-only layout.** No responsive breakpoints, no mobile app.
- No per-bank analyst report scraping (see §11 for why).
- No advisory disclaimers anywhere in the UI. This is the owner's own tool; never render "not financial advice", "do your own research", or similar copy.

---

## 2. Stack (decided)

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2 | |
| DB | SQLite + SQLAlchemy 2.0 + Alembic | Single user; zero ops. SQLAlchemy keeps a Postgres swap trivial later. |
| HTTP client | httpx (async) | One shared client per external service, with throttling. |
| Scheduler | APScheduler (in-process, AsyncIOScheduler) | Jobs + startup catch-up in §13. |
| Sentiment | HuggingFace `transformers` + ProsusAI/finbert (CPU) | VADER as a fallback behind the same interface if torch install is a problem. |
| Frontend | React 18 + Vite + TypeScript, Tailwind | SPA served by Vite dev server in dev; `vite build` output served by FastAPI static in prod mode. |
| Charts | TradingView `lightweight-charts` v5 (candles, overlays, panes, markers, price lines) | Use Recharts only for the allocation donut and small sparklines. S/R bands are a custom v5 primitive — budget real time (§8.4). |
| Data fetching (FE) | TanStack Query | |
| Money math | Python `decimal` in application code; **persist ledger money as INTEGER cents** (micro-units where sub-cent precision matters, e.g. unit prices) | SQLAlchemy `Numeric` on SQLite silently round-trips through REAL floats, defeating `decimal`. float64 is fine for chart series. |
| Concurrency | httpx clients are the only truly async I/O. yfinance, PRAW, and `transformers` are synchronous: **every call runs in an executor** (`asyncio.to_thread`; `ProcessPoolExecutor` for FinBERT batches) | Nothing synchronous ever touches the event loop. |
| XIRR | `pyxirr` | |
| Repo layout | `/backend`, `/frontend`, `Makefile` with `make dev` (runs both), `make seed`, `docker-compose.yml` optional | |

Timezone: `America/Toronto` everywhere. Base display currency: CAD, with a global CAD/USD toggle in the header.

`.env` (backend):
```
FINNHUB_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=qtfolio/0.1 by <reddit_username>
```
Questrade tokens are NOT env vars — they rotate (§4) and live in the DB.

---

## 3. External data sources

| Source | Used for | Cost/limits | Notes |
|---|---|---|---|
| Questrade API | Accounts, balances, positions, activities, candles, quotes, symbol search | Free; honor rate-limit response headers, back off on 429 | Auth in §4. `api_server` base URL comes from the token response — never hardcode it. |
| yfinance | Analyst price targets & recommendation summary, upgrades/downgrades, earnings dates, splits, benchmark candles (auto-adjusted) | Free, unofficial | Wrap every call: retry ×2, timeout, cache to DB, degrade gracefully (panel shows "source unavailable" state, app never crashes on yfinance breakage). Pin the version; treat upgrades as a deliberate act. |
| Finnhub (free tier) | Company news per ticker, general/market news, recommendation trends | 60 calls/min free | Price targets are premium on Finnhub — do NOT use that endpoint; targets come from yfinance. |
| Google News RSS | Supplementary per-ticker news (`https://news.google.com/rss/search?q=<query>`) and extra feeds for the general tab | Free | Item links are encoded `news.google.com/rss/articles/...` redirects; public decoders break regularly. **Treat URLs as opaque**: dedup primarily on normalized title (§8.3); keep the redirect as the click-through link. |
| Reddit API (PRAW, script app) | Posts mentioning owned tickers | Free personal tier | Collection = uniform `subreddit.new()` scans with local ticker matching. **`search()` is banned for measurement** (§10) — its recall varies and poisons the mention-count baseline. Default subs: wallstreetbets, stocks, investing, StockMarket. Configurable in settings. Ignore list applies (default DLR, DLR.U). |
| Bank of Canada Valet API | Daily USDCAD (`FXUSDCAD` series) | Free | Needed to value USD positions in CAD historically. Forward-fill weekends/holidays. |

Adapter pattern: news, analyst, and sentiment sources each sit behind a small interface (`NewsSource`, `AnalystSource`) so more sources can be added later without touching pipelines.

---

## 4. Questrade integration

### 4.1 Auth (this is the #1 way these apps break — get it right)

- Initial setup: user generates a manual refresh token in Questrade's API Centre and pastes it into the Settings page. (Manual tokens expire 7 days after generation if unused.)
- Token exchange: `POST https://login.questrade.com/oauth2/token?grant_type=refresh_token&refresh_token=...` returns `access_token` (expires in 1800s), a NEW `refresh_token`, and `api_server`.
- **Refresh tokens are single-use and expire in 3 days.** Rules:
  1. Persist the new refresh token to the DB **atomically, before** the new access token is used for anything.
  2. Serialize all refreshes behind an asyncio lock — two concurrent refreshes will burn the chain.
  3. Also mirror the latest refresh token to `data/qt_token_backup.json` as a belt-and-suspenders copy.
  4. Scheduled `token_keepalive` job refreshes once daily as backup — but see rule 6.
  5. If the chain is lost (invalid_grant), set token status = `broken`, show a persistent banner linking to Settings with the regeneration steps. All Questrade-dependent jobs pause; everything else keeps working from cached data.
  6. **Refresh on every process start, unconditionally, before any schedule begins.** Local-first means the process is usually not alive at 06:00; startup refresh is the primary keepalive and the daily job is the bonus. Accepted residual failure mode: any gap > 3 days still burns the chain → banner + re-paste. This is the cost of local-only and it is fine; reconstruction self-heals the missed days.
  7. The token-exchange call gets a **long timeout (30 s) and exactly one retry with the same refresh token** on timeout/transport error. If the retry returns `invalid_grant`, the first attempt almost certainly succeeded server-side and the response carrying the new token was lost in transit — unrecoverable; go to broken state. Never issue two exchanges concurrently (the lock covers this). Never log tokens.
- Auto-refresh access token when <60s remain or on a 401, then retry the request once.

### 4.2 Endpoints used

| Endpoint | Purpose |
|---|---|
| `GET v1/accounts` | Enumerate accounts (user picks which to track in Settings; default: all). |
| `GET v1/accounts/:id/positions` | Current positions (qty, avgCost via `averageEntryPrice`, `currentMarketValue`, `openPnl`, `dayPnl`). Also the anchor state for reconstruction (§6). |
| `GET v1/accounts/:id/balances` | Per-currency + combined balances, `totalEquity`, `cash`, `marketValue`. Anchor cash state for §6. |
| `GET v1/accounts/:id/activities?startTime&endTime` | **Max 31-day window per request.** Paginate backwards in 31-day steps until the API returns empty/errors. Treat the total lookback depth as unknown until probed — never assume it covers the account's life. Store raw JSON + typed columns. **Dedup: rows carry no stable IDs, and two identical same-day fills are legal and produce identical field tuples.** Hash = typed-field tuple + an occurrence index within each identical-tuple group per (account, day); cross-sync dedup compares **multisets** (counts per tuple), never set membership. |
| `GET v1/symbols/search?prefix=` / `GET v1/symbols/:id` | Resolve ticker → `symbolId`, currency, listing exchange, description. Cache in `symbols`. |
| `GET v1/markets/candles/:symbolId?startTime&endTime&interval` | OHLCV. Intervals used: `OneDay`, `OneHour`, `FifteenMinutes`, `FiveMinutes`. Daily backfilled deep; intraday best-effort (history is limited — fetch what's available, don't fight it). **Paginate backfill in bounded chunks** (assume ≲2,000 bars per request; probe the real cap once, chunk conservatively). |
| `GET v1/markets/quotes?ids=` | Snapshot quotes for dashboard refresh (cache 60s while a page is open). |

Client behaviors: honor `X-RateLimit-*` headers; exponential backoff on 429/5xx; all timestamps stored UTC, rendered in America/Toronto.

### 4.3 CSV import (load-bearing, not a fallback)

Questrade's web app can export activity history further back than the API returns. If the account predates the API's activity window — **assume it does until proven otherwise** — everything the reconstruction (§6) knows about the pre-window era comes from this import. Settings page accepts the export (xlsx/csv), maps rows into the same `activities` table (same multiset dedup), with a preview + row-mapping report before commit.

---

## 5. Data model (SQLite)

```
settings(key, value_json)                      -- incl. candle_adjustment_regime, news_ignore_tickers
qt_tokens(id=1, refresh_token, access_token, api_server, access_expires_at, status, updated_at)
accounts(qt_account_id PK, type, status, tracked bool)
symbols(ticker PK, qt_symbol_id, currency, exchange, description, sector NULL, is_benchmark bool)
activities(id PK, account_id, hash UNIQUE, occurrence_idx, trade_date, transaction_date, settlement_date,
           type, action, ticker NULL, qty, price_cents, gross_amount_cents, commission_cents,
           net_amount_cents, currency, description, raw_json)
position_snapshots(date, account_id, ticker, qty, price_cents, market_value_cents, currency)  -- PK(date,account,ticker)
balance_snapshots(date, account_id, currency, cash_cents, market_value_cents, total_equity_cents) -- PK(date,account,currency)
fx_rates(date PK, usdcad)
candles(ticker, interval, ts, o, h, l, c, v)                                          -- PK(ticker,interval,ts)
holdings_ledger(date, ticker, qty)             -- derived, rebuilt by reconstruction   PK(date,ticker)
cash_ledger(date, currency, balance_cents)     -- derived                              PK(date,currency)
series_daily(date PK, portfolio_value_cad, capital_in_cad, pnl_cad, twr_index, flows_cad)
news_items(id PK, source, url UNIQUE, title, title_hash, tickers_json, published_at,
           summary NULL, sentiment_score NULL, sentiment_label NULL, tab)   -- tab: holdings|general
reddit_posts(id PK, sub, permalink UNIQUE, ticker, title, selftext_trunc, score,
             num_comments, created_at, sentiment_score, sentiment_label)
analyst_snapshots(ticker, date, target_low, target_mean, target_high, n_analysts,
                  rec_mean, rec_counts_json, source)                                   -- PK(ticker,date,source)
analyst_actions(id PK, ticker, date, firm, action, from_grade, to_grade, pt_old NULL, pt_new NULL)
job_runs(job_name, started_at, finished_at, status, detail)
```

All money columns are INTEGER cents in the row's native currency (price columns may use micro-units where sub-cent precision matters). Do NOT use SQLAlchemy `Numeric` on SQLite. `series_daily.capital_in_cad` replaces v1's `cum_contributions_cad` — semantics in §7.

---

## 6. Portfolio history reconstruction (core algorithm — backward from ground truth)

There is **no balance-history endpoint** in the Questrade API. v1 of this spec built the ledger forward from the earliest activity, which silently assumes the activity data covers the account's entire life; it usually doesn't. v2 anchors at today's live state and walks backward, so the reconstruction is correct for exactly the window the data covers, and the residual state at the window edge **is** the opening balance — no seed table, no guessing.

0. **Adjustment-regime check (one-time, before anything else).** Fetch daily candles for a held ticker with a known split (SOXL or TQQQ both qualify) spanning the split date. If the close series shows no ~N× gap at the split, Questrade candles are **back-adjusted**; if it gaps, they are **unadjusted**. Persist the result to `settings.candle_adjustment_regime`, log it to `job_runs`, and display it in Settings diagnostics. All quantity handling below branches on this.
1. **Backfill activities** via the API (31-day pages, §4.2) and extend earlier via CSV import (§4.3).
2. **Anchor state**: live positions (qty per ticker) + live per-currency cash balances = S(today). This is ground truth by definition.
3. **Walk activities newest → oldest**, reversing each:
   - Buy reversed: qty −= q, cash += net_amount (trade currency). Sell mirrored.
   - Dividends, interest, fees, rebates: reversed on the cash ledger. DRIP rows with symbol+quantity also reverse holdings.
   - Deposits / Withdrawals: reversed on cash AND recorded — at their own dates, with un-reversed sign — into the **external flows** series (`flows_cad`, converted at that day's USDCAD if the flow was USD). These are the only rows that count as contributions.
   - FX conversions: reversed across currency ledgers; net external flow = 0.
   - Journals / transfers (incl. Norbert's Gambit legs, DLR ↔ DLR.U): reversed quantity between tickers with **zero** contribution effect. Match interlisted pairs by equal quantity with a **±2 business-day tolerance** — the legs do not always post same-day.
   - **Splits, by regime**: if candles are *back-adjusted*, keep all quantities in today's (post-split) units for the entire walk — and detect-and-skip any split journal rows Questrade posted (same-ticker out/in at a yfinance-known split date ±2bd in the split ratio), or they double-convert. If candles are *unadjusted*, quantities must be date-native: when the walk crosses a yfinance split date and no journal rows already did the conversion, divide qty by the factor going back.
   - Unknown `type` → log to `job_runs.detail` and skip cash effects rather than guessing.
4. **Residuals are the opening state.** Whatever qty/cash remains after reversing everything is, by construction, the holding at the window start. Invariant: **no quantity may go negative at any date during the walk.** A violation means missing older history or an unhandled corporate action → surface a warning card in Settings with ticker, date, and delta. Never silently fudge.
5. **Valuation**: for each date, `portfolio_value = Σ(qty × daily close in listing currency → CAD via fx_rates) + Σ cash balances (→ CAD)`. Forward-fill closes per **listing exchange's** holiday calendar (TSX and NYSE differ). Quantity units and close adjustment must be regime-consistent (step 0).
6. **Ground truth beats reconstruction — without seams.** Wherever a real `balance_snapshots.total_equity` exists, it is the value for that date. Reconstructed values *before the first snapshot* are multiplied by the single constant factor that makes the series meet the first snapshot exactly. Reconstructed values *between* two snapshots (gap days) are linearly rescaled to match both endpoints. A step discontinuity would print a phantom return into TWR; rescaling forbids it.
7. Persist to `series_daily`. Recompute is idempotent and cheap; rerun after every activities sync or CSV import.
8. **Going forward**: the EOD job (§13) snapshots real balances/positions daily; snapshots accumulate as ground-truth points and the reconstruction fills only the gaps between them.

---

## 7. Performance math (this answers the "deposits aren't growth" requirement)

- **Capital-in line**: `capital_in_cad(t) = V(window_start) + Σ flows in (window_start, t]`. The opening value counts as capital-in, so the shaded band between this line and portfolio value correctly reads "P&L since ⟨window start⟩". The hover tooltip discloses the opening-value component. Deposit/withdrawal events also render as markers on the x-axis with amount tooltips.
- **TWR (time-weighted return)** — used for ALL comparison overlays, because raw portfolio value is distorted by deposits. Start-of-day flow convention, consistently:
  `r_t = (V_t − V_{t−1} − F_t) / (V_{t−1} + F_t)` where `F_t` = net external flow that day. If the denominator ≤ 0 (empty account), `r_t = 0` and log it. Chain-link: `twr_index_t = twr_index_{t−1} × (1 + r_t)`, rebased to 100 at the selected range start. (This convention also makes day one well-defined: the first deposit sits in the denominator.)
- **Comparison series** for individual stocks/benchmarks: yfinance auto-adjusted daily closes, indexed to 100 at range start. Apples-to-apples with the TWR index (both are total-return-flavored: the portfolio keeps dividends as cash; adjusted closes fold them in).
- **MWR**: XIRR via `pyxirr` with an initial pseudo-flow of −V(window_start) at the window start, then deposits negative, withdrawals positive, terminal value positive. Label it **"MWR since ⟨date⟩"**, never "since inception", unless the CSV import demonstrably covers the account's full life. If XIRR fails to converge, show "n/a" — never crash.
- Stats per selected range: TWR, MWR, max drawdown (on TWR index), annualized vol (stdev of daily TWR × √252), best/worst day.
- **Unit tests required** (non-negotiable — this is the one part of the app where tests are mandatory): backward-ledger construction from a synthetic activities fixture covering buys/sells/deposit/withdrawal/FX conversion/journal pair posting 2 days apart/identical same-day fills/a split under **both** candle regimes; TWR chain-linking with a mid-period deposit including day one; seam rescaling against a synthetic snapshot; multiset dedup across an overlapping sync.

---

## 8. Pages

Routing: `/` (Dashboard), `/performance`, `/news`, `/stocks/:ticker`, `/settings`. Persistent left rail nav + header (total value, day P&L, CAD/USD toggle, sync status dot).

### 8.1 Dashboard
- Header stats: total value, day change ($, %), total open P&L, MWR since data start, TWR YTD.
- Allocation donut with toggle: by position / by currency / by sector. Sector from yfinance, cached on `symbols`. **ETFs get a single "ETF" bucket — no look-through (§17).** With an ETF-heavy book the sector view is mostly one slice; default the toggle to by-position.
- Holdings table: ticker, qty, avg cost, last, market value, weight %, day P&L, open P&L ($ and %), 30-day sparkline. Sortable. Row click → `/stocks/:ticker`. Values from live positions + 60s-cached quotes.

### 8.2 Performance
- **Main chart (the app's signature view)**: portfolio value line + capital-in line, with the gap shaded (green above, red below). Deposit/withdrawal markers on the axis. Range selector: 1M / 3M / 6M / 1Y / All. Hover shows value, capital-in-to-date, and P&L.
- **Compare mode** (toggle): switches to indexed-to-100 view. Portfolio TWR index + multi-select overlays: any owned ticker, plus benchmarks (default QQQ, SMH, SPY — editable in Settings). Log-scale toggle.
- **Structure-over-time view** (third toggle): stacked area of per-position market value (CAD), which shows how the portfolio's composition evolved, not just its total.
- Stats strip below the chart for the selected range (§7 stats).

### 8.3 News
- Tab **My holdings**: merged Finnhub + Google News RSS items for owned tickers (minus the ignore list). Ticker filter chips, sentiment badge per item (label + score), source, relative time. **Dedup is title-first**: `title_hash` = normalized-lowercase hash, plus rapidfuzz ≥ 92 collapse within a 48h window; canonical-URL match is secondary (Google URLs are opaque, §3). **Flood control: keep ≤ ~10 items per ticker per day**, ranked by source tier (Finnhub company news > RSS) then recency — mega-cap holdings otherwise drown the tab. Infinite scroll, newest first.
- Tab **Tech & market**: Finnhub `general` news + configurable RSS list (defaults: CNBC Technology, Reuters Technology). Same card layout, no ticker chips.
- Manual refresh button; background sync hourly (§13).

### 8.4 Stock detail (`/stocks/:ticker`) — owned tickers get full treatment; any ticker works read-only
Layout: chart occupies the top ~60%; three panels below (Position & Levels, Analyst, Sentiment).

**Chart** (lightweight-charts v5):
- Candles + volume pane. Interval switch: 5m / 15m / 1h / 1D / 1W (weekly aggregated from daily). Intraday limited to whatever Questrade history exists.
- Indicator toggles (server-computed arrays, §9). Defaults ON: EMA 20/50/200, auto S/R zones, avg-cost price line. Default OFF: Bollinger (20,2), VWAP (intraday only), Anchored VWAP (anchors: last earnings date / 52w low — click-to-anchor is §17).
- Sub-panes (toggle): RSI(14) with 30/70 bands, MACD(12,26,9) histogram.
- Markers: EMA50/EMA200 golden/death crosses; earnings dates (from yfinance calendar).
- **Owner overlays**: horizontal price line at average cost (labeled with qty and open P&L), and small buy/sell markers from `activities` on the daily chart.
- **S/R rendering**: translucent horizontal bands via a lightweight-charts v5 **custom primitive**; acceptable fallback = paired price lines at zone edges. Budget real time here — bands and any custom drawing are plugin/canvas work, not configuration.
- RS ratio mini-chart under the main chart: close(ticker) / close(benchmark), benchmark selectable (default SMH for semis tickers, QQQ otherwise), indexed to 100.

**Position & Levels panel**: qty, avg cost, market value, weight, open/day P&L, 52w high/low with % distance, ATR(14) readout, distance to nearest support and resistance.

**Analyst panel** (§11): price gauge rendering low—mean—high target band with current price pin; number of analysts; recommendation distribution bar (strongBuy/buy/hold/sell/strongSell); 30-day change in mean target; recent upgrades/downgrades table (date, firm, action, grade change, PT change).

**Sentiment panel** (§10): 7d/30d news-sentiment trend line; Reddit mention count/day bars with a highlighted marker when the 30-day z-score > 2; upvote-weighted average sentiment; top 5 recent posts (score-sorted, external links, per-sub tag). Copy presents mention volume as the primary signal and polarity as secondary context — never oversell polarity.

### 8.5 Settings
- Questrade: token paste field, connection status (access expiry countdown, chain age, last refresh), tracked-accounts checklist, "Test connection".
- Sync: per-job cards (last run, status, duration, manual "Run now") for every job in §13; reconciliation warnings and the detected candle-adjustment regime surface here.
- Data sources: Finnhub/Reddit key status checks; benchmark list editor; Reddit sub list editor; general-news RSS editor; **news/sentiment ticker ignore list** (default: DLR, DLR.U — journal vehicles; "DLR" on Reddit is Digital Realty Trust).
- Backups: last `db_backup` time, "Back up now", note that offsite copies are manual (§13).
- Import: activities CSV/xlsx upload (§4.3) with a preview + row-mapping report before commit.

---

## 9. Indicators & support/resistance (server-side, pure numpy/pandas — no TA-Lib C dependency)

Implement by hand (each is <20 lines): SMA, EMA, RSI (Wilder smoothing), MACD(12,26,9), Bollinger(20, 2σ), ATR(14, Wilder), session VWAP (intraday, resets daily), Anchored VWAP (from an arbitrary anchor timestamp), rolling 52w high/low. Volume profile is **cut from v1** (§17).

**Auto support/resistance**:
1. Detect swing points on the active interval with a fractal window k=3 (a high greater than the 3 bars on each side = swing high; mirror for lows). On daily, use the last ~400 bars.
2. Collect swing prices; cluster with single-linkage where cluster tolerance = max(0.5 × ATR(14) at detection time, 0.4% of price).
3. Score each cluster = touch count × recency decay (half-life 90 bars). Keep top 6.
4. Return zones as `{price_low, price_high, score, touches, kind}` where kind = support if below current price else resistance. Render per §8.4 with touch count on hover.
Recompute on candle sync; cache per (ticker, interval).

API returns indicator arrays aligned to candle timestamps so the frontend stays dumb.

---

## 10. Sentiment pipeline

> **Owner amendment (2026-08-09):** Reddit is removed from v1 entirely (no PRAW, no
> mention scanning). The attention signal is **news volume**: articles/day per ticker
> across Finnhub + Alpha Vantage NEWS_SENTIMENT + yfinance ticker news, z-scored vs a
> trailing 30d baseline (std floor 1), spikes classified by that day's mean FinBERT
> tone and annotated with day/+5d price moves. Google News RSS was dropped (restamped
> old articles, SEO junk). FinBERT remains the single scoring authority with a
> high-precision options-jargon correction layer ("reveals fresh puts" → bearish).
> The remainder of this section describes the original Reddit design for reference.

- Model: ProsusAI/finbert via `transformers` pipeline, CPU, batched at sync time in a **process-pool executor** — never inline in a request, never on the event loop. Map to score ∈ [−1, 1] = P(pos) − P(neg); label = argmax. Store on the item; keep raw text so items can be re-scored if the model changes. (~400 MB model download; ~2 GB RAM resident with torch.)
- News: score `title + " " + summary` (truncate 512 tokens).
- Reddit: score `title + " " + selftext[:1000]`.
- **Reddit collection — measurement vs display are separate concerns:**
  - *Measurement stream*: `subreddit.new()` scan every 2h per configured sub, matched locally: cashtag `$TICKER` or word-boundary ticker in title/selftext; **cashtag required for tickers ≤ 2 chars**; ignore list applied (§8.5); drop posts with score < 5. `search()` is not used anywhere — its recall varies day to day and poisons the mention-count baseline with collection noise.
  - *Display stream*: the top-5 posts shown on the stock page = highest-score among matched posts. Display may look popular; measurement must stay uniform.
- Aggregations (per ticker, daily): mention_count; upvote-weighted mean sentiment with weight = log1p(score); mention z-score vs trailing 30d mean/std. The **z-score spike is the primary signal surfaced** — with the explicit caveat that v1 scans posts only, and most WSB volume lives in daily-thread comments (§17), so this is a weak signal until comments are scanned. Polarity from WSB is sarcasm-contaminated and FinBERT only partly compensates; the UI presents polarity as secondary context.

---

## 11. Analyst data

- Primary: yfinance — `analyst_price_targets` (low/mean/high/current), `recommendations_summary` (strongBuy…strongSell counts by month), `upgrades_downgrades` (firm-level actions). Snapshot daily into `analyst_snapshots` / `analyst_actions` so 30-day target drift can be computed locally.
- Sector via yfinance `.info` is the single most breakage-prone call in the app: fetch **once per symbol**, cache on `symbols` indefinitely, manual override in Settings.
- Secondary: Finnhub `/stock/recommendation` (free) as a second consensus source; show source attribution per panel.
- Explicitly out of scope: scraping TipRanks / MarketBeat / Zacks / bank research portals. They are Cloudflare-protected, ToS-hostile, and brittle; Yahoo/Finnhub already aggregate the same sell-side consensus. The `AnalystSource` interface exists so a paid API (e.g., Benzinga) can be added later if ever wanted.
- Leveraged ETFs (SOXL, TQQQ) have no analyst coverage — the panel must render a clean "no analyst coverage (leveraged ETF)" state, and for ETFs show the underlying index RS chart more prominently instead.

---

## 12. Backend API surface

```
GET  /api/portfolio/summary                     # header stats
GET  /api/portfolio/holdings                    # table rows + sparkline data
GET  /api/portfolio/allocation?by=position|currency|sector
GET  /api/performance/series?range=1m|3m|6m|1y|all&mode=value|indexed|stacked
GET  /api/performance/compare?symbols=NVDA,QQQ&range=...
GET  /api/news?tab=holdings|general&tickers=&cursor=
GET  /api/stocks/{ticker}/candles?interval=&from=&to=      # includes requested indicator arrays via ?indicators=
GET  /api/stocks/{ticker}/levels                # S/R zones + 52w + ATR + distances
GET  /api/stocks/{ticker}/analyst
GET  /api/stocks/{ticker}/sentiment?days=30
GET  /api/stocks/{ticker}/position              # owner qty/avg cost/markers
POST /api/sync/{job_name}                       # manual trigger
POST /api/backup                                # timestamped .db copy to data/backups/
GET  /api/settings / PUT /api/settings
POST /api/settings/qt-token                     # paste new refresh token
POST /api/import/activities                     # CSV/xlsx upload
GET  /api/health                                # token status, job statuses, source health, adjustment regime
```

---

## 13. Scheduled jobs (APScheduler; every run logged to `job_runs`)

**Startup sequence — runs before any schedule starts.** APScheduler only fires while the process is alive; on a laptop that is the exception, not the rule. On boot: (1) refresh the token chain unconditionally (§4.1.6); (2) **catch-up pass** — for each job, compare last success in `job_runs` against its cadence and run overdue jobs now (`eod_snapshot` only if a market close has occurred since its last success); (3) start the schedules. Catch-up is the primary execution path; cron firing is the bonus.

| Job | Schedule | Work |
|---|---|---|
| `token_keepalive` | daily 06:00 | Refresh Questrade token chain (§4.1). Backup to the startup refresh. |
| `eod_snapshot` | weekdays 17:30 ET | Snapshot balances + positions; fetch daily candles for holdings + benchmarks; fetch FX; recompute `series_daily`. |
| `activities_sync` | daily 18:00 ET | Pull last 35 days of activities (overlap window, multiset dedup); recompute if new rows. |
| `news_sync` | hourly | Finnhub + RSS for owned tickers and general tab; FinBERT scoring for new items. |
| `reddit_sync` | every 2h | PRAW `new()` scans + scoring + daily aggregates. |
| `analyst_sync` | daily 07:00 | yfinance + Finnhub snapshots, upgrades/downgrades diff. |
| `db_backup` | weekly Sun 12:00 | `sqlite3 .backup`-style snapshot to `data/backups/qtfolio-YYYYMMDD.db`, keep last 8. Offsite copies are the owner's job (drag one to Drive occasionally). After M2, the daily snapshots are the only non-rederivable data — everything else rebuilds from activities + CSVs. |
| `bootstrap` | manual (Settings) | Full backfill: adjustment-regime check, activities pagination, candle backfill (chunked), FX backfill, backward reconstruction, invariant checks. Shows progress in Settings. |

On startup with an empty DB: the UI routes to Settings with a 3-step onboarding (paste token → pick accounts → run bootstrap).

---

## 14. Design direction (v2.1 — owner-amended during M1; supersedes the dark-terminal direction)

Subject: a private market dashboard for one person, in the visual language of a modern fintech SaaS (Stripe lineage): light, white, hairline-precise, quietly confident. Not a dark terminal (v2's original direction — replaced at the owner's request), not a neobank app.

- **Desktop-only.** No responsive breakpoints; assume ≥ ~1280 px. Revisit only after deployment (§17).
- **Tokens** — page background `#F6F8FB`, card `#FFFFFF`, hairline border `#E3E8EE`, text `#1A1F36`, muted `#6A7383`, accent (interactive/focus) blurple `#635BFF` (hover `#5147E8`, selected tint `#F0F1FF`), up `#17803D`, down `#DF1B41`, capital-in line `#8792A2`. Green/red are reserved exclusively for P&L semantics — never decorative. Charts/donuts draw from blues, purples, and grays only.
- **Surfaces** — white cards with 1px hairline borders, 8px radius, soft shadow (`0 1px 2px rgba(16,24,40,.05)`) on the light-gray page. Primary buttons are filled blurple with white text; secondary are white with hairline borders. Density still wins over whitespace inside cards: compact tables, 13–14px data text.
- **Type** — Inter for everything, with `font-variant-numeric: tabular-nums` on every number, price, and table cell. Display moments (page titles, the big total-value figure) use Inter semibold at tight tracking; no second typeface.
- **Signature element** — the Performance chart's shaded band between portfolio value and capital-in: the band *is* the P&L, breathing green/red. Spend the visual boldness there; keep everything else quiet.
- Restrained motion: number ticks and chart crosshairs only; respect `prefers-reduced-motion`.
- Copy: plain, specific, sentence case, active voice ("Run bootstrap", "Paste refresh token"). Errors say what happened and the next action. No mascots, no exclamation marks, no advisory disclaimers (§1).

---

## 15. Milestones (each independently runnable; stop and verify acceptance before continuing)

**M1 — Foundation + live dashboard**
Scaffold repo, DB, migrations; token manager with rotation lock + backup file + **startup refresh** + timeout/one-retry rule; **startup catch-up framework** (job_runs comparison, overdue execution); throttled Questrade client; Settings (token paste, test connection, account selection); Dashboard with live holdings/allocation/summary.
*Accept:* fresh manual token pasted → dashboard shows real positions and balances; killing and restarting the server triggers an immediate token refresh and runs overdue jobs; two parallel requests never double-refresh; a simulated ≥1-day gap is recovered by the startup sequence.

**M2 — History + Performance page**
Activities backfill + CSV import; **adjustment-regime check**; **backward reconstruction** + invariants + seam rescaling; FX + chunked daily candle backfill; `series_daily`; `db_backup` job; Performance page with value + capital-in + markers, compare mode (TWR indexed), stacked structure view; unit tests from §7.
*Accept:* no negative quantity at any date in the walk (violations surface as explicit warning cards, not silent fudges); the synthetic fixture suite passes (identical same-day fills, lagged journal pair, split under both candle regimes, mid-period-deposit TWR incl. day one, seam rescaling, multiset dedup); 2–3 historical month-end values spot-checked against Questrade statements within 0.5%.

**M3 — Stock detail charts**
Candle cache + intervals; indicator engine + S/R; chart page with toggles, avg-cost line, trade markers, RS mini-chart, Position & Levels panel; S/R bands via custom primitive (or price-line fallback).
*Accept:* NVDA-style liquid name and SOXL both render correctly on 1D and 15m; S/R zones look sane on daily (spot-check touch counts); indicators verified against a reference implementation on one fixture series.

**M4 — News + sentiment**
News adapters + title-first dedupe + flood control + FinBERT pipeline (process pool); News page (both tabs); Reddit `new()`-scan pipeline + aggregates; stock-page Sentiment panel.
*Accept:* owned-ticker feed populates, deduplicates across sources, and respects the ignore list (DLR excluded); per-ticker daily caps hold under a mega-cap flood; sentiment scores stored and badges render; mention z-score spike marker fires on a synthetic fixture.

**M5 — Analyst + polish**
Analyst snapshots/actions + panels incl. ETF empty state; Settings ignore-list editor, backups card, regime display; design pass to §14 (empty states, error states, loading skeletons); README with setup (Questrade API centre steps, Reddit app creation, Finnhub key); `make dev` one-command start.
*Accept:* target gauge + rec distribution render for covered names, ETF state for SOXL/TQQQ; app boots from an empty DB into onboarding.

---

## 16. Edge cases & gotchas checklist (Claude Code: re-read this before each milestone)

- Refresh token: single-use, 3-day expiry, serialize refreshes, persist-before-use, backup file, **refresh on every startup**, exchange endpoint = 30s timeout + exactly one same-token retry (retry `invalid_grant` after a timeout = chain lost in transit → banner), `invalid_grant` → broken-state banner. Never log tokens.
- `api_server` comes from the token response and can change between refreshes.
- Activities: 31-day max request window; overlap syncs by a few days; **no stable row IDs — identical same-day fills are legal**, so dedup is multiset-aware (occurrence-indexed hashes, count comparison); unknown activity types logged, not guessed.
- **Never assume the activities window covers the account's life.** Backward reconstruction and the CSV import exist precisely because it usually doesn't. "Since inception" labels are earned only by demonstrated coverage.
- USD everything: USD cash, USD dividends (with possible withholding lines), USD deposits — all CAD-normalized via dated FX for flows and valuation; store native currency alongside.
- Norbert's Gambit: DLR/DLR.U journal legs are transfers, not P&L, not contributions — and they post up to ~2 business days apart, so pair-match with tolerance. Exclude both tickers from news/sentiment ("DLR" on Reddit is Digital Realty).
- Splits/reverse splits (SOXL and TQQQ have both): run the **adjustment-regime check first**; broker-posted split journal rows must not be double-applied on top of yfinance factors.
- Market holidays: forward-fill closes per listing exchange; TSX and NYSE calendars differ.
- Interval mixing: never mix Questrade candles and yfinance candles on the same chart; Questrade for the stock page, yfinance (auto-adjusted) for comparison/benchmark series.
- yfinance is unofficial and breaks occasionally: pin the version, defensive wrappers, cached last-good data, visible "stale as of ⟨time⟩" badges instead of crashes. `.info` (sector) is the most fragile call — fetch once, cache forever.
- Google News RSS links are encoded redirects — treat as opaque; dedup on titles first.
- Reddit: respect free-tier rate limits; `new()` scans only for measurement; cashtag required for tickers ≤2 chars; ignore list applies.
- SQLite: WAL mode; single writer discipline (jobs queue writes); **money as INTEGER cents** — SQLAlchemy `Numeric` on SQLite is a silent float.
- Concurrency: yfinance/PRAW/transformers are sync — executors always; FinBERT in a process pool (~2 GB RAM resident; forget Raspberry Pi unless running the VADER fallback).
- Candle backfill is chunked; probe the per-request cap once, then chunk conservatively.
- Jobs: **catch-up-on-startup is the primary execution path** on a laptop; cron firing is the bonus.
- Bind server to 127.0.0.1 by default.

---

## 17. Deferred to v1.1 (do not build any of this in v1)

- **SEC EDGAR event feed** — owner-parked for the model phase: 8-K/6-K material-event
  flags per held single stock (submissions API, ticker→CIK map) as features for the
  low/high detection model. Not a v1 news-UI source (ETF-heavy book → filings are noise).
- **Reddit (any form)** — fully removed 2026-08-09; revisit only if the comment-level
  scanning idea returns for the model.
- **Auto support/resistance (§9 clustering + §8.4 band rendering)** — owner-deferred during M3.
  Superseded by the owner's longer-term plan: a **low/high point detection model**, ~50% driven by
  multi-source sentiment analysis (with recency decay) and ~50% by price/candle features using
  current quantitative methods. Design implication for M4+: sentiment items must be stored raw,
  timestamped, and per-source so they can later feed model training; indicator arrays stay
  server-computed and API-accessible.

- **Volume profile** (custom canvas primitive; real work, low value until the rest is trusted).
- **Click-to-anchor AVWAP** (preset anchors — last earnings, 52w low — ship in v1).
- **Reddit comment / WSB daily-thread scanning** — where most mention volume actually lives; upgrades the z-score from weak signal to usable one.
- **ETF sector look-through** for the allocation donut.
- **Always-on deployment**: mini-PC or VPS + systemd + Tailscale Serve (or Cloudflare Tunnel + Access). Tripwire to revisit: the token has been re-pasted ~3 times, or the app is being opened most days.
- **Offsite streaming backups** (Litestream → B2 free tier) — replaces the manual-copy stance once deployed.
- **Mobile/responsive layout** — only meaningful post-deployment.
- **Paid analyst source** behind `AnalystSource` (e.g., Benzinga).
