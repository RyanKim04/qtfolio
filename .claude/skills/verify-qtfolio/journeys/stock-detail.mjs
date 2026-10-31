// Feature: stock detail. Held XEQT.TO → Position avg cost, legend, overlay chips, interval gating of
// VWAP/AVWAP, RSI/MACD panes, symbol search to a not-held ticker with RS, unknown-symbol error.
// Run: qtv browse stock-detail .claude/skills/verify-qtfolio/journeys/stock-detail.mjs
import { ensureSynthetic } from "./lib/seed.mjs";

export default async ({ page, base, step, note }) => {
  const section = (name) => page.locator("section", { has: page.getByRole("heading", { name, exact: true }) });
  const noCrash = () => page.getByText("Something crashed while rendering this page.").waitFor({ state: "detached" });
  const chip = (name) => page.getByRole("button", { name, exact: true });

  await step("seed", async () => note(`imported now: ${await ensureSynthetic(page, base)}`));

  await step("held-ticker", async () => {
    await page.goto(base + "/stocks/XEQT.TO");
    await page.getByRole("heading", { name: "XEQT.TO" }).waitFor();
    await section("Position").getByText("$33.00").first().waitFor({ timeout: 60_000 });
    await page.getByText("EMA 20", { exact: true }).waitFor({ timeout: 60_000 });
    await page.getByText("Avg cost", { exact: true }).first().waitFor();
  });

  await step("overlays", async () => {
    await chip("Bollinger").click();
    await page.getByText("Bollinger", { exact: true }).nth(1).waitFor();
    await chip("EMA 20/50/200").click();
    await page.getByText("EMA 20", { exact: true }).waitFor({ state: "detached" });
    await noCrash();
  });

  await step("interval-gating", async () => {
    await chip("5M").click();
    if (await chip("VWAP").isDisabled()) throw new Error("VWAP disabled on 5M");
    if (!(await chip("AVWAP (52w low)").isDisabled())) throw new Error("AVWAP enabled on 5M");
    await chip("1W").click();
    if (await chip("AVWAP (52w low)").isDisabled()) throw new Error("AVWAP disabled on 1W");
    await chip("1D").click();
  });

  await step("panes", async () => {
    await chip("RSI").click();
    await chip("MACD").click();
    await noCrash();
  });

  await step("search-not-held", async () => {
    await page.getByPlaceholder("Go to symbol…").fill("msft");
    await page.getByPlaceholder("Go to symbol…").press("Enter");
    await page.waitForURL("**/stocks/MSFT");
    await page.getByRole("heading", { name: "MSFT" }).waitFor();
    await section("Position").getByText("Not currently held.").waitFor({ timeout: 60_000 });
    await page.getByText(/Relative strength vs/).waitFor();
  });

  await step("unknown-symbol", async () => {
    await page.goto(base + "/stocks/ZZZZQX");
    await page.getByText("unknown symbol 'ZZZZQX'").waitFor({ timeout: 60_000 });
  });
};
