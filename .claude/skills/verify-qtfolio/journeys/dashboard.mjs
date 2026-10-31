// Feature: dashboard. Synthetic book → holdings qty/avg cost, cash line, allocation cash, ticker sort
// both ways, USD toggle keeps .TO avg cost in CAD, row → stock page, Full risk analysis → /risk.
// Run: qtv browse dashboard .claude/skills/verify-qtfolio/journeys/dashboard.mjs
import { ensureSynthetic } from "./lib/seed.mjs";

export default async ({ page, base, step, note }) => {
  const section = (name) => page.locator("section", { has: page.getByRole("heading", { name, exact: true }) });
  const holdings = () => section("Holdings");
  const firstTicker = () => holdings().locator("tbody tr").first().locator("td span").first().innerText();

  await step("seed", async () => note(`imported now: ${await ensureSynthetic(page, base)}`));

  await step("holdings", async () => {
    await page.goto(base + "/");
    for (const [t, qty, avg] of [["XEQT.TO", "100", "$33.00"], ["SHOP.TO", "20", "$150.00"]]) {
      const row = holdings().getByRole("row").filter({ hasText: t });
      await row.waitFor();
      const cells = await row.locator("td").allInnerTexts();
      if (cells[1] !== qty || cells[2] !== avg) throw new Error(`${t}: qty ${cells[1]} avg ${cells[2]}`);
    }
    await holdings().getByText(/cash \$4,200\.00/).waitFor();
    note(`header: ${await page.locator(".grid-cols-5").first().innerText()}`);
  });

  await step("allocation", async () => {
    const alloc = section("Allocation");
    await alloc.getByText("Cash").waitFor();
    await alloc.getByText("$4,200.00").waitFor();
  });

  await step("sort-ticker", async () => {
    await holdings().locator("th", { hasText: /^Ticker/ }).click();
    if ((await firstTicker()) !== "SHOP.TO") throw new Error(`ascending first row ${await firstTicker()}`);
    await holdings().locator("th", { hasText: /^Ticker/ }).click();
    if ((await firstTicker()) !== "XEQT.TO") throw new Error(`descending first row ${await firstTicker()}`);
  });

  await step("usd-toggle", async () => {
    await page.getByRole("button", { name: "USD", exact: true }).click();
    const row = holdings().getByRole("row").filter({ hasText: "XEQT.TO" });
    await row.getByText("$33.00").waitFor();
    note(`USD tiles: ${await page.locator(".grid-cols-5").first().innerText()}`);
    await page.getByRole("button", { name: "CAD", exact: true }).click();
  });

  await step("row-nav", async () => {
    await holdings().getByRole("row").filter({ hasText: "XEQT.TO" }).click();
    await page.waitForURL("**/stocks/XEQT.TO");
    await page.getByRole("heading", { name: "XEQT.TO" }).waitFor();
  });

  await step("risk-link", async () => {
    await page.goto(base + "/");
    await page.getByRole("link", { name: "Full risk analysis" }).click({ timeout: 240_000 });
    await page.waitForURL("**/risk");
  });
};
