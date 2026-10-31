// Shared seed for journeys that need the synthetic book (VERIFY1: 100 XEQT.TO @ $33, 20 SHOP.TO @ $150,
// cash $4,200). A fresh instance redirects to Settings and is imported through the upload card; a seeded
// instance (a re-run, or another journey ran first) is left alone.
import { fileURLToPath } from "node:url";

const FIXTURE = fileURLToPath(new URL("../../fixtures/ws-activities-synthetic.csv", import.meta.url));

export async function ensureSynthetic(page, base) {
  await page.goto(base + "/");
  await page.waitForLoadState("networkidle");
  if (!page.url().endsWith("/settings")) return false;
  const card = page.locator("section", { has: page.getByRole("heading", { name: "Import Wealthsimple activities" }) });
  await card.locator('input[type="file"]').setInputFiles(FIXTURE);
  await card.getByRole("button", { name: "Import 4 rows" }).click();
  await card.getByText(/Imported 4 new rows/).waitFor({ timeout: 90_000 });
  return true;
}
