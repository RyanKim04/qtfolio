// Feature: import-activities (upload path): fresh instance → onboarding redirect → upload the
// synthetic export → preview → commit → Dashboard shows the reconstructed holdings.
// Run: qtv browse import-upload .claude/skills/verify-qtfolio/journeys/import-upload.mjs
import { fileURLToPath } from "node:url";

const FIXTURE = fileURLToPath(new URL("../fixtures/ws-activities-synthetic.csv", import.meta.url));

export default async ({ page, base, step, note }) => {
  const card = () => page.locator("section", { has: page.getByRole("heading", { name: "Import Wealthsimple activities" }) });

  await step("onboarding-redirect", async () => {
    await page.goto(base + "/");
    await page.waitForURL("**/settings");
    await card().getByText("No Wealthsimple import yet.").waitFor();
  });

  await step("preview", async () => {
    await card().locator('input[type="file"]').setInputFiles(FIXTURE);
    await card().getByText("Wealthsimple export · 4 rows · 2026-06-01 → 2026-08-03 · accounts VERIFY1").waitFor();
    await card().getByText("trades: 2").waitFor();
    await card().getByText("deposits: 2").waitFor();
  });

  await step("commit", async () => {
    await card().getByRole("button", { name: "Import 4 rows" }).click();
    // Commit resolves symbols on Yahoo + fetches bars + recomputes history: allow a minute.
    const done = card().getByText(/Imported 4 new rows \(0 duplicates skipped\)\. History recomputed: \d+ days\./);
    await done.waitFor({ timeout: 90_000 });
    note(`commit: ${await done.textContent()}`);
  });

  await step("dashboard-holdings", async () => {
    await page.getByRole("link", { name: "Dashboard" }).click();
    await page.waitForURL(base + "/");
    const holdings = page.locator("section", { has: page.getByRole("heading", { name: "Holdings" }) });
    for (const t of ["XEQT.TO", "SHOP.TO"]) await holdings.getByRole("row").filter({ hasText: t }).waitFor();
    note(`holdings rows: ${(await holdings.getByRole("row").allTextContents()).join(" | ")}`);
  });
};
