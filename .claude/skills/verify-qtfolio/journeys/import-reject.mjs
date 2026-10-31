// Feature: import-activities (reject + dedup, upload path) — a non-WS CSV shows the server's
// 400 reason on the card; the WS fixture again previews and commits as all duplicates.
// Needs an instance that already imported the fixture once (upload or watcher).
// Run: qtv browse import-reject .claude/skills/verify-qtfolio/journeys/import-reject.mjs
import { fileURLToPath } from "node:url";

const FIXTURE = fileURLToPath(new URL("../fixtures/ws-activities-synthetic.csv", import.meta.url));
const NOT_WS = fileURLToPath(new URL("../fixtures/not-ws.csv", import.meta.url));

export default async ({ page, base, step, note }) => {
  const card = () => page.locator("section", { has: page.getByRole("heading", { name: "Wealthsimple — import activities" }) });

  await step("reject-non-ws", async () => {
    await page.goto(base + "/settings");
    await card().locator('input[type="file"]').setInputFiles(NOT_WS);
    const err = card().getByText(/^not a Wealthsimple activities export/);
    await err.waitFor();
    note(`reject: ${await err.textContent()}`);
  });

  await step("dedup-commit", async () => {
    await card().locator('input[type="file"]').setInputFiles(FIXTURE);
    await card().getByRole("button", { name: "Import 4 rows" }).click();
    const done = card().getByText(/Imported 0 new rows \(4 duplicates skipped\)/);
    await done.waitFor({ timeout: 90_000 });
    note(`dedup: ${await done.textContent()}`);
  });

  await step("dashboard-holdings", async () => {
    await page.getByRole("link", { name: "Dashboard" }).click();
    await page.waitForURL(base + "/");
    const holdings = page.locator("section", { has: page.getByRole("heading", { name: "Holdings" }) });
    for (const t of ["XEQT.TO", "SHOP.TO"]) await holdings.getByRole("row").filter({ hasText: t }).waitFor();
  });
};
