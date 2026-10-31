# Dashboard

The Dashboard (`/`) is the live view of the book: total value and day change in the header, five stat tiles, a sortable holdings table with cash line and 30-day sparklines, an allocation donut, and the TFSA room card. Prices update from Yahoo's streamer, and the page polls every 5 s.

## Sub-features

- `dash-tiles` shows the tiles `Total value`, `Day change`, `Open P&L`, `MWR since <date>`, and `TWR YTD`.
- `dash-holdings` shows one row per open position with qty, avg cost, last, market value, weight, P&L, and sparkline. Column headers sort.
- `dash-cash` shows the Holdings header line `cash $X · $Y USD · Z% of total`.
- `dash-allocation` shows the donut with `Position` / `Currency` tabs and a legend including `Cash`.
- `dash-currency` makes the header `CAD` / `USD` toggle re-denominate every money value. The choice persists in localStorage.
- `dash-row-nav` opens `/stocks/<ticker>` when the user clicks a holdings row.

## How to get to it (user POV)

- Open the app root, or click **Dashboard** in the left rail.
- Click a holdings row to reach the stock page.

## Driving it with qtv

Preconditions:

- `DOCTOR OK`, and the synthetic export is imported (see import-activities).

Steps:

- **Open.** In a journey, `page.goto(base + "/")`, then wait for `holdings.getByRole("row").filter({ hasText: "XEQT.TO" })`, where `holdings` is the section with heading `Holdings`. Both `XEQT.TO` (qty `100`, avg `$33.00`) and `SHOP.TO` (qty `20`, avg `$150.00`) are present.
- **Cash.** The Holdings header contains `cash $4,200.00`.
- **Allocation.** The section with heading `Allocation` lists `XEQT.TO`, `SHOP.TO`, and `Cash`, and its `Cash` amount is `$4,200.00`.
- **Sort.** Click the `Ticker` column header. The first data row becomes `SHOP.TO` (ascending). Click again and it becomes `XEQT.TO`.
- **Currency.** Click `USD` in the header. The tile values change and the `Avg cost` cells still show CAD for `.TO` rows, because avg cost stays in native currency. Click `CAD` to restore.
- **Row nav.** Click the `XEQT.TO` row. The URL becomes `/stocks/XEQT.TO` and heading `XEQT.TO` is visible.
- **Proof.** Take one `step` per action above. Save the tile values with `note()`, but do not assert them.

## Gotchas

- `Last`, every P&L figure, weights, and totals move with live prices. Assert only qty, avg cost, cash, and ticker presence.
- The page polls every 5 s and re-renders. Re-query locators after waits instead of holding element handles.
- The CAD/USD choice persists in localStorage. A new journey gets a fresh browser context, so it starts in CAD.
- Outside 04:00–20:00 ET on weekdays, the price line reads `market closed, pre-market opens …`. That is not an error.
