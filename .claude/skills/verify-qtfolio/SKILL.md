---
name: verify-qtfolio
description: Launch an isolated qtfolio instance (FastAPI backend + Vite React UI, scratch SQLite DB, fake $HOME) and drive the real web UI in headless Chrome to prove a change works. Covers importing Wealthsimple exports, Dashboard holdings, Performance, stock charts, TFSA room, and risk analytics (Risk tab, Dashboard risk row, Performance risk metrics). Use whenever you need evidence that a qtfolio feature behaves correctly end to end, not just that pytest passes.
---

# Verify qtfolio

qtfolio is a local, single-user portfolio terminal. The user touches a **web UI** (React at the Vite port), which calls a FastAPI backend under `/api`. Secondary surfaces: the JSON API itself and the **Downloads watcher** (a Wealthsimple CSV landing in `~/Downloads` imports itself).

Everything goes through one helper, `.claude/skills/verify-qtfolio/bin/qtv` (run it from the repo root; it finds the repo itself). Below, `qtv` means that path.

**Never drive the owner's instance.** `make dev` runs on 8000/5173 against `data/qtfolio.db`, which holds REAL financial data, and its watcher reads the real `~/Downloads`. `qtv` never touches those. It starts its own pair on free ports (first free from 8100 and 5273) with:

- `DB_PATH=<scratch>/qtfolio.db`. Migrations, data, and `db_backup` (it writes `backups/` beside the DB) all stay in scratch.
- `HOME=<scratch>/home`. The watcher's `~/Downloads` and yfinance's cache resolve inside scratch.
- `QTFOLIO_API_PORT=<backend port>` for Vite, so `/api` proxies to *this* backend, not to 8000.

Two `qtv up` runs can coexist. Select one with `export QTV_RUN=<run id>`; otherwise every subcommand uses the most recent `up`.

## Launch

Prereqs: `make install` once (`backend/.venv`, `frontend/node_modules`), Google Chrome in `/Applications`, and **network access**: Yahoo resolves symbols and supplies bars and quotes, and FX comes from the network too.

```bash
.claude/skills/verify-qtfolio/bin/qtv up
```

`up` returns only when the instance is usable: Alembic migrations ran on the empty scratch DB, the startup catch-up finished (backend log line `startup sequence complete; scheduler running`), and `/api/health` answers through the Vite proxy. It prints `READY run=<id>` plus the frontend URL, backend URL, evidence dir, and scratch dir. A fresh instance has **no data**: `onboarding.has_history=false`, and every UI route redirects to `/settings`. Seed it by driving the import feature (see `features/import-activities.md`); there is no seed SQL, because importing IS the real user path.

Teardown is `qtv down` (see Cleanup).

## Doctor

Run this first, and again whenever anything looks off:

```bash
.claude/skills/verify-qtfolio/bin/qtv doctor
```

It is read-only. It checks that both pids are alive, that each port's listener is *our* pid, that the backend has the scratch DB open and NOT `data/qtfolio.db`, that the backend's `HOME` is the scratch home, that Vite's `QTFOLIO_API_PORT` points at our backend, and that `/api/health` returns 200 both directly and through the proxy. It also prints the git rev (`+dirty` if the tree has changes), onboarding state, and the last run of each job. Do not drive the instance unless the output ends in `DOCTOR OK`.

## Drive

**Browser (primary).** Write a journey module and run it:

```bash
.claude/skills/verify-qtfolio/bin/qtv browse <journey-name> <path/to/journey.mjs>
```

`bin/browse.mjs` launches system Google Chrome headless at 1440×900 through `playwright-core` (installed once into `~/.cache/verify-qtfolio`). It imports your module's default export, `async ({ page, base, step, shot, aria, note }) => {}`:

- `step(name, fn)` runs one user action, then saves `NN-name.png` and `NN-name.aria.yml`. Use one step per action so the evidence shows each action and the state it produced.
- `note(text)` appends to `notes.txt`. Use it for the extracted values you assert on.
- A thrown error saves `FAILED.png` and `FAILED.aria.yml` and exits 1. Use Playwright `waitFor` on the expected text or role as the assertion. Never sleep a fixed time.

Ready-made journeys live in `journeys/`. Copy one as the template for a new journey. Stable handles in this UI:

- Section headings are real `<h2>` elements: `Holdings`, `Allocation`, `TFSA room` (`TFSA room · <year>` once configured), `P&L by position`, `Risk-adjusted performance`, `Monthly returns`, `Trade decisions`, `Position`, `Levels`, the Risk tab's `Where the risk comes from`, `Correlation`, `Factor exposures`, `Value at Risk and expected shortfall`, `Stress tests`, `Causal view of macro risk`, plus Settings panels such as `Import Wealthsimple activities`, `Tracked accounts`, `Sync`, `TFSA contribution room`, `Backups`, and `Diagnostics`. Scope with `page.locator("section", { has: page.getByRole("heading", { name: "…" }) })`.
- Left-rail nav is links named `Dashboard`, `Performance`, `Risk`, `Settings`. Use `exact: true` for `Risk`: the Dashboard's `Full risk analysis` link and the holdings `Risk` header also match. Stock pages live at `/stocks/<TICKER>`; clicking a holdings row also opens one.
- Buttons are named by visible text, such as `Import N rows`, `Run now`, `Record`, `Save`, `CAD`, `USD`, range buttons `1M` `3M` `6M` `1Y` `ALL`, and Performance modes `Value` `Compare` `Structure` `Drawdown` `Rolling`. Two TFSA buttons are named `Record` (tab and submit); see tfsa-room.
- Inputs have no labels. Use their placeholders (`Go to symbol…`, `Room (CAD)`, `Amount (CAD)`, `Institution`) or the single `input[type="file"]` in the import card. Inputs without a placeholder: the TFSA date (`input[type=date]`), type (`select`) and Settings anchor year; the Performance benchmark `select`.
- `confirm()` dialogs (TFSA delete) are dismissed by Playwright unless the journey registers `page.once("dialog", (d) => d.accept())` first.

**API (secondary).** Use the API to check side effects, or when the feature has no UI:

```bash
qtv api GET /api/health [evidence-name]                 # body → <evidence>/api/<name>.json, prints HTTP code
qtv api POST /api/import/activities imp -F file=@x.csv -F commit=true
qtv sql "SELECT account_id, type, ticker, qty_micro, net_amount_cents FROM activities"   # read-only (mode=ro)
```

**Downloads watcher.** Drop an export into the scratch `~/Downloads`. The watcher polls every 3 s:

```bash
qtv drop-export [path/to/export.csv]   # default: fixtures/ws-activities-synthetic.csv
```

Seed data is `fixtures/ws-activities-synthetic.csv` (and, for P&L numbers, `fixtures/ws-activities-cash-lines.csv`, see `features/performance.md`). The default is a synthetic TFSA `VERIFY1` with deposits of $10,000 and $500 CAD and buys of 100 XEQT @ $33.00 and 20 SHOP @ $150.00 (CAD, so they import as `XEQT.TO` and `SHOP.TO`). Cash after import is exactly **$4,200.00 CAD**. **Never put real exports, balances, or CRA room in fixtures, journeys, or this skill**, because the repo is public.

## Evidence

Evidence for each run goes to `data/verify-evidence/<run id>/` (gitignored, since `data/` is): `<journey>/NN-*.png|.aria.yml`, `notes.txt`, `console.log` (console errors and warnings, page errors, HTTP ≥400, failed requests), `api/*.json`, and after `down`, `logs/backend.log` and `logs/frontend.log`.

Proof standards:

- Exercise the real user path: the UI upload card, the Downloads drop, and visible buttons. Do not call internal functions or set state directly. Use `qtv sql`/`qtv api` to *observe*, not to set up what the feature should have produced.
- Capture the action and the resulting state (one `step` per action), not just the final screen.
- Verify side effects as well as the screen: rows in `activities`/`accounts`/`series_daily` (`qtv sql`), `wealthsimple.last_import` and job runs in `/api/health`, and lines like `auto-imported … N new` in the backend log.
- Live prices change. Assert on deterministic facts (tickers, qty, avg cost, cash $4,200, row counts, date ranges), never on `Last` or an absolute P&L or total.
- Numbers are still checkable. Identities hold whatever the price (lines sum to their total, row P&L = value now + sold − bought), so assert them to the cent. Cash-only lines (interest, fees, conversion cost, dividends) are fixed by the fixture and the instance's `fx_rates`, so recompute them by hand and compare. A screen that renders but does not add up is a failed run.
- No mocks. Yahoo is a real production dependency here. If Yahoo is down, the run fails; say so instead of faking it.
- Record which entry point you drove. Proving the upload path does not prove the watcher path.

## Cleanup

```bash
.claude/skills/verify-qtfolio/bin/qtv down
```

`down` kills only the two pids that `up` recorded (TERM, then KILL), copies the logs into the evidence dir, deletes the scratch dir (`$TMPDIR/qtfolio-verify/<run id>`: DB, fake HOME, backups), and warns if a port is still listening. It never kills by process name and never deletes `data/verify-evidence/`. Run it after every attempt, including failed ones. Remove old evidence dirs by hand when you no longer need them.

## Helpers

| File | Invocation |
| --- | --- |
| `bin/qtv` | `qtv up \| doctor \| api M PATH [name] [curl…] \| sql "…" \| drop-export [csv] \| browse JOURNEY SCRIPT \| down`. Run with no arguments for usage. |
| `bin/browse.mjs` | Runs only through `qtv browse`. Its contract is in the file's header comment. |
| `journeys/*.mjs` | `qtv browse <name> .claude/skills/verify-qtfolio/journeys/<name>.mjs` |
| `fixtures/ws-activities-synthetic.csv` | Synthetic WS export, used by `drop-export` and `import-upload.mjs` |

Feature recipes are in [`features/README.md`](features/README.md).
