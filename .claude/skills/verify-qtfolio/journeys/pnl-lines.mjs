// Feature: performance P&L numbers. Fresh instance → upload the cash-lines fixture (FX conversions,
// a USD buy, dividend, withholding tax, fee, rebate, interest) → Performance opens on Value · ALL ·
// Lifetime → every table identity holds to the cent at ALL and 1Y → Compare offers XEQT.TO.
// Live prices move the values but never the identities, so those are asserted exactly.
// Run: qtv browse pnl-lines .claude/skills/verify-qtfolio/journeys/pnl-lines.mjs
import { fileURLToPath } from "node:url";

const FIXTURE = fileURLToPath(new URL("../fixtures/ws-activities-cash-lines.csv", import.meta.url));

const cents = (text) => {
  const t = text.replace(/−/g, "-").trim();
  return t === "—" || t === "" ? 0 : Math.round(Number(t.replace(/[^0-9.-]/g, "")) * 100);
};

// Reads the table and throws on the first identity that fails. Returns the parsed lines.
async function checkTable(table, note, label) {
  const heads = await table.locator("thead th").allTextContents();
  const hasStart = heads.some((h) => /^Value on /i.test(h));
  const groups = {};
  const outside = {};
  let total = null;
  let group = null;
  for (const row of await table.locator("tbody tr").all()) {
    const c = await row.locator("td").allTextContents();
    if (c.length === 1) {
      if (!c[0].startsWith("Of which")) group = c[0];
      continue;
    }
    if (c.length === 3) {
      if (c[0] === "Total") total = cents(c[2]);
      else outside[c[0]] = cents(c[2]);
      continue;
    }
    const [name, ...nums] = c;
    const [start, bought, sold, now, pnl] = (hasStart ? nums : [0, ...nums]).map((v) => (typeof v === "number" ? v : cents(v)));
    if (name.endsWith(" total")) {
      const g = groups[group];
      for (const [k, v] of Object.entries({ start, bought, sold, now, pnl }))
        if (g[k] !== v) throw new Error(`${label} ${name}: ${k} ${v} ≠ row sum ${g[k]}`);
      continue;
    }
    if (pnl !== now + sold - bought - start)
      throw new Error(`${label} ${name}: P&L ${pnl} ≠ now ${now} + sold ${sold} − bought ${bought} − start ${start}`);
    const g = (groups[group] ??= { start: 0, bought: 0, sold: 0, now: 0, pnl: 0 });
    for (const [k, v] of Object.entries({ start, bought, sold, now, pnl })) g[k] += v;
  }
  const positions = Object.values(groups).reduce((t, g) => t + g.pnl, 0);
  const lines = Object.values(outside).reduce((t, v) => t + v, 0);
  if (total === null || positions + lines !== total)
    throw new Error(`${label}: positions ${positions} + outside lines ${lines} ≠ Total ${total}`);
  note(`${label}: start column ${hasStart}; groups ${JSON.stringify(groups)}; outside ${JSON.stringify(outside)}; total ${total}`);
  return { hasStart, groups, outside, total };
}

export default async ({ page, base, step, note }) => {
  const card = () => page.locator("section", { has: page.getByRole("heading", { name: "Import Wealthsimple activities" }) });
  const table = () => page.locator("section", { has: page.getByRole("heading", { name: "P&L by position" }) });
  const pressed = (name) => page.getByRole("button", { name, exact: true, pressed: true });
  // Identities can't catch a mispriced line, because "Fees and adjustments" absorbs whatever is
  // left over. The fixture has no snapshots, so that leftover may only be rounding.
  const remainder = async (range) => {
    const other = (await (await page.request.get(`${base}/api/performance/attribution?range=${range}`)).json()).other;
    note(`${range}: unexplained remainder ${other}`);
    if (Math.abs(other) >= 1) throw new Error(`${range}: unexplained remainder $${other} ≥ $1; check FX coverage (fx_rates)`);
  };

  await step("import", async () => {
    await page.goto(base + "/settings");
    await card().locator('input[type="file"]').setInputFiles(FIXTURE);
    await card().getByText(/Wealthsimple export · 12 rows · 2026-06-01 → 2026-09-30 · accounts VERIFY2/).waitFor();
    await card().getByRole("button", { name: "Import 12 rows" }).click();
    const done = card().getByText(/Imported 12 new rows \(0 duplicates skipped\)\. History recomputed: \d+ days\./);
    await done.waitFor({ timeout: 120_000 });
    note(`commit: ${await done.textContent()}`);
  });

  await step("performance-defaults", async () => {
    await page.getByRole("link", { name: "Performance" }).click();
    for (const b of ["Value", "ALL", "Lifetime P&L"]) await pressed(b).waitFor();
    await table().getByText(/^2026-06-01 →/).waitFor();
    if (await page.getByText(/^of which$/).count()) throw new Error("chart header still shows the of-which split");
  });

  await step("table-all", async () => {
    await table().getByRole("cell", { name: "Total", exact: true }).waitFor();
    const t = await checkTable(table(), note, "ALL");
    if (t.hasStart) throw new Error("ALL starts at inception; the start column should be hidden");
    for (const l of ["Dividends", "Interest", "Currency conversion", "Fees and adjustments"])
      if (!(l in t.outside)) throw new Error(`ALL: missing line ${l}`);
    if (t.outside.Interest !== 123) throw new Error(`Interest ${t.outside.Interest} ≠ 123 (fixture $1.23)`);
    await remainder("all");
    await table().getByRole("row").filter({ hasText: "Total" }).last().scrollIntoViewIfNeeded();
  });

  await step("table-1m", async () => {
    // 1M starts mid-history (after the fixture's last activity), so the start column shows.
    await page.getByRole("button", { name: "1M", exact: true }).click();
    await table().locator("thead th", { hasText: /^Value on / }).waitFor();  // header cells have role "cell" here
    await checkTable(table(), note, "1M");
    await remainder("1m");
  });

  await step("compare-xeqt", async () => {
    await page.getByRole("button", { name: "Compare", exact: true }).click();
    await page.getByRole("button", { name: "XEQT.TO", exact: true }).waitFor();
  });
};
