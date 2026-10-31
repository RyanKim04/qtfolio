// Feature: performance page shell. Every chart mode renders without the crash card, the Value-only
// baseline toggle shows and hides, and the sections below the chart appear in source order.
// Run on a seeded instance: qtv browse perf-tabs .claude/skills/verify-qtfolio/journeys/perf-tabs.mjs
import { ensureSynthetic } from "./lib/seed.mjs";

const MODES = ["Value", "Compare", "Structure", "Drawdown", "Rolling"];
const SECTIONS = ["Risk-adjusted performance", "P&L by position", "Monthly returns", "Trade decisions"];

export default async ({ page, base, step, note }) => {
  await step("seed", async () => note(`imported now: ${await ensureSynthetic(page, base)}`));

  await step("open", async () => {
    await page.goto(base + "/performance");
    await page.getByRole("heading", { name: "P&L by position" }).waitFor({ timeout: 90_000 });
  });

  for (const m of MODES) {
    await step(`mode-${m.toLowerCase()}`, async () => {
      await page.getByRole("button", { name: m, exact: true }).click();
      await page.getByRole("button", { name: m, exact: true, pressed: true }).waitFor();
      const lifetime = page.getByRole("button", { name: "Lifetime P&L" });
      if ((m === "Value") !== (await lifetime.isVisible())) throw new Error(`baseline toggle visibility wrong in ${m}`);
      if (await page.getByText("Something crashed while rendering this page.").count()) throw new Error(`${m} crashed`);
    });
  }

  await step("section-order", async () => {
    for (const s of SECTIONS) await page.getByRole("heading", { name: s, exact: true }).waitFor({ timeout: 240_000 });
    const ys = [];
    for (const s of SECTIONS) ys.push((await page.getByRole("heading", { name: s, exact: true }).boundingBox()).y);
    if (ys.some((y, i) => i && y <= ys[i - 1])) throw new Error(`sections out of order: ${ys}`);
    note(`section y: ${ys.join(", ")}`);
  });
};
