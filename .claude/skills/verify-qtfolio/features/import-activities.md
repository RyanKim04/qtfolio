# Import activities

The user brings Wealthsimple history into qtfolio in one of two ways: they pick or drop the official activities CSV on the Settings import card, which shows a preview and then imports on confirmation, or they just download the export and let it land in `~/Downloads`, where it imports itself within seconds. Re-importing overlapping data adds nothing. A fresh install with no history sends every page to Settings until an import succeeds.

## Sub-features

- `import-onboarding` redirects any route to `/settings` while there is no history, with the "Set up qtfolio" banner.
- `import-preview` shows row count, date range, accounts, and per-type chips without writing anything.
- `import-commit` imports the rows, creates the ledger account, recomputes history, and fills the Dashboard.
- `import-dedup` re-imports the same export as `0 new rows (N duplicates skipped)`.
- `import-watch` auto-imports a WS export that appears in `~/Downloads`, matched by header rather than filename.
- `import-reject` refuses a non-WS CSV with a 400 error that the card shows.

## How to get to it (user POV)

- Open Settings → **Wealthsimple — import activities** → **Choose File** (or drop a file on the card) → **Import N rows**.
- Download the export in Wealthsimple so that it lands in `~/Downloads`.
- (API) `POST /api/import/activities` as multipart `file`, with `commit=true` to commit.

## Driving it with qtv

Preconditions:

- A fresh `qtv up` run (empty DB) with `DOCTOR OK`.

Steps:

- **Upload path, end to end (proven).** Run `qtv browse import-upload .claude/skills/verify-qtfolio/journeys/import-upload.mjs`. Its steps are `onboarding-redirect` (`/` lands on `/settings`, card says `No Wealthsimple import yet.`), `preview` (`Wealthsimple export · 4 rows · 2026-06-01 → 2026-08-03 · accounts VERIFY1`, chips `deposits: 2` and `trades: 2`), `commit` (`Imported 4 new rows (0 duplicates skipped). History recomputed: N days.`), and `dashboard-holdings` (rows `XEQT.TO` and `SHOP.TO`).
- **Side effects.** Run `qtv sql "SELECT account_id,type,ticker,qty_micro,net_amount_cents FROM activities ORDER BY transaction_date"`, which should return 4 rows (deposits 1000000 and 50000 cents; trades SHOP.TO 20000000 and XEQT.TO 100000000 micro). Run `qtv sql "SELECT qt_account_id,broker,type FROM accounts"` → `VERIFY1 wealthsimple TFSA`. Run `qtv api GET /api/health after-import` and confirm `wealthsimple.last_import.imported` is 4 and `.file` is `ws-activities-synthetic.csv`.
- **Watcher path.** On a fresh run, use `qtv drop-export`, then poll `qtv api GET /api/health` until `wealthsimple.last_import.file` starts with `activities-export-`. The backend log (`$TMPDIR/qtfolio-verify/<run>/backend.log`, or `logs/` after `down`) shows `auto-imported activities-export-YYYY-MM-DD.csv: 4 new, 0 duplicates`. Then reload `/` and assert the same two holdings rows.
- **Dedup.** After either path, import the same fixture through the other path. The card or log shows `0 new` and `4 duplicates`, and `activities` still has 4 rows.
- **Reject + dedup (proven).** After the fixture is imported once (either path), run `qtv browse import-reject .claude/skills/verify-qtfolio/journeys/import-reject.mjs`. `reject-non-ws` uploads `fixtures/not-ws.csv` and the card shows the server's own reason `not a Wealthsimple activities export (CSV with effective_date/activity_type columns)` (not a bare `HTTP 400`); `dedup-commit` re-imports the fixture as `Imported 0 new rows (4 duplicates skipped)`; `activities` stays at 4 rows.
- **Watcher retry.** To prove a failed auto-import is retried, hold a write lock on the scratch DB (`python3 -c` with `BEGIN IMMEDIATE` on `<scratch>/qtfolio.db`, ~30 s) and `qtv drop-export`. While locked, `/api/health` `wealthsimple.watch.error` reads `activities-export-…csv: (sqlite3.OperationalError) database is locked` and stays; after release the next scan logs `auto-imported … 4 new` and the error clears. The backend log has exactly one `auto-import of … failed` line.
- **Cross-origin refusal.** The backend refuses a non-local `Origin` (403 `cross-origin request refused`) or `Host` (400 `Invalid host header`). Probe with `curl -H "Origin: http://evil.example" -F file=@<valid export> …/api/import/activities` against both the backend and the Vite port, then `qtv sql` that no rows landed.

## Gotchas

- Headless Chrome blocks a foreign page's `fetch` to 127.0.0.1 by itself (`Failed to fetch`, nothing reaches the backend log), so a browser journey cannot prove the server-side Origin guard. Use curl, which acts like a browser without that block (Safari, Firefox).

- Commit is slow (it resolves symbols on Yahoo, fetches bars, and recomputes ~150 days). Allow 90 s. A Yahoo outage leaves `could not resolve on Yahoo: …` in the result.
- An upload does not mark the file "seen" for the watcher. Dropping the same content afterwards still triggers an import, which reports 4 duplicates. That is expected, not a dedup failure.
- `/api/health` `wealthsimple.watch.dir` shows `~/Downloads` even under the scratch HOME, because the status line abbreviates the home prefix. Isolation is proven by `qtv doctor`'s HOME check, not by this field.
- `console.log` always holds one `404` for `/favicon.ico` (the frontend ships no favicon). Anything else in it is a finding.
- CAD symbols import with `.TO` (WS `SHOP`/CAD → `SHOP.TO`). Assert the suffixed ticker.
