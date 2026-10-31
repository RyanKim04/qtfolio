// Feature: risk analytics: import the synthetic export, then the Dashboard risk row and
// holdings Risk/Beta columns, every Risk tab section (covariance toggles, correlation
// stress view, factors, VaR + backtest, stress tabs with a shock intervention, reverse
// stress, Monte Carlo, causal graph + impulse response), and the Performance additions
// (risk-adjusted panel, Drawdown/Rolling modes, monthly returns, trade decisions).
// Run: qtv browse risk .claude/skills/verify-qtfolio/journeys/risk.mjs
import { ensureSynthetic } from "./lib/seed.mjs";

// First run fetches factor history back to 2008 from Yahoo.
const SLOW = { timeout: 240_000 };

export default async ({ page, base, step, note }) => {
  const section = (name) => page.locator("section", { has: page.getByRole("heading", { name }) });

  await step("import", async () => {
    note(`imported now: ${await ensureSynthetic(page, base)}`);
  });

  await step("dashboard-risk-row", async () => {
    await page.getByRole("link", { name: "Dashboard" }).click();
    await page.getByText("1-day VaR 95%").waitFor(SLOW);
    await page.getByText("Effective bets").waitFor();
    const holdings = section("Holdings");
    await holdings.locator("th", { hasText: /^Risk/ }).waitFor();
    note(`dashboard risk row: ${(await page.locator(".grid-cols-6").first().innerText()).replace(/\n/g, " | ")}`);
    note(`holdings: ${(await holdings.getByRole("row").allTextContents()).join(" | ")}`);
  });

  await step("risk-tab", async () => {
    await page.getByRole("link", { name: "Risk", exact: true }).click();
    await page.waitForURL("**/risk");
    await section("Where the risk comes from").getByText("Diversification ratio").waitFor(SLOW);
    note(`decomposition: ${(await section("Where the risk comes from").innerText()).replace(/\n/g, " | ").slice(0, 900)}`);
  });

  await step("covariance-factor-2y", async () => {
    const s = section("Where the risk comes from");
    await s.getByRole("button", { name: "Factor" }).click();
    await s.getByRole("button", { name: "2Y" }).click();
    await s.getByText(/13-factor ETF model/).waitFor();
    await s.getByText("Effective bets (torsion)").waitFor(SLOW);
  });

  await step("correlation-stress", async () => {
    const s = section("Correlation");
    await s.getByRole("button", { name: "Stress days" }).click();
    await s.getByText(/worst 10% of days/).waitFor();
  });

  await step("factors", async () => {
    const s = section("Factor exposures");
    await s.getByText("Risk share").waitFor(SLOW);
    note(`factors: ${(await s.innerText()).replace(/\n/g, " | ").slice(0, 700)}`);
  });

  await step("var", async () => {
    const s = section("Value at Risk and expected shortfall");
    await s.getByText("Filtered historical", { exact: true }).waitFor(SLOW);
    await s.getByRole("button", { name: "99%" }).click();
    await s.getByRole("button", { name: "10 days" }).click();
    await s.getByText("By holding (Euler)").waitFor(SLOW);
    note(`var 99/10d: ${(await s.innerText()).replace(/\n/g, " | ").slice(0, 800)}`);
  });

  await step("stress-historical", async () => {
    const s = section("Stress tests");
    await s.getByText("2008 financial crisis").waitFor(SLOW);
    await s.getByText("2020 COVID crash").click();
    note(`stress: ${(await s.innerText()).replace(/\n/g, " | ").slice(0, 1200)}`);
  });

  await step("stress-shock", async () => {
    const s = section("Stress tests");
    await s.getByRole("button", { name: "Shock" }).click();
    await s.getByText("Causal effect").waitFor(SLOW);
    await s.getByRole("button", { name: "Risk-off" }).click();
    // The previous result stays on screen, dimmed, until the new one lands.
    await s.locator('[aria-busy="false"]').getByText(/Channels \(path-specific effects\)/).waitFor(SLOW);
    await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'));
    note(`shock risk-off: ${(await s.innerText()).replace(/\n/g, " | ").slice(0, 1200)}`);
  });

  await step("stress-reverse", async () => {
    const s = section("Stress tests");
    await s.getByRole("button", { name: "Reverse" }).click();
    await s.getByText("Macro drivers").waitFor(SLOW);
    note(`reverse: ${(await s.innerText()).replace(/\n/g, " | ").slice(0, 900)}`);
  });

  await step("stress-monte-carlo", async () => {
    const s = section("Stress tests");
    await s.getByRole("button", { name: "Monte Carlo" }).click();
    await s.getByText("Chance of losing 20%+").waitFor();
  });

  await step("causal", async () => {
    const s = section("Causal view of macro risk");
    await s.getByText("What the data say about the graph").waitFor(SLOW);
    await s.getByRole("img", { name: /Causal graph/ }).waitFor();
    await s.getByText(/Cumulative portfolio response/).waitFor(SLOW);
    await s.getByRole("button", { name: "US 10y yield" }).click();
    await s.getByText(/structural shock to US 10y yield/).waitFor(SLOW);
    note(`causal: ${(await s.innerText()).replace(/\n/g, " | ").slice(0, 1500)}`);
  });

  await step("performance-metrics", async () => {
    await page.getByRole("link", { name: "Performance" }).click();
    const s = section("Risk-adjusted performance");
    await s.getByText("Probabilistic Sharpe").waitFor(SLOW);
    note(`metrics: ${(await s.innerText()).replace(/\n/g, " | ")}`);
  });

  await step("performance-drawdown", async () => {
    await page.getByRole("button", { name: "Drawdown" }).click();
    await page.locator(".recharts-area").first().waitFor();
  });

  await step("performance-rolling", async () => {
    await page.getByRole("button", { name: "Rolling" }).click();
    await page.getByText(/Sharpe · 63-session window/).waitFor();
  });

  await step("monthly-and-decisions", async () => {
    await section("Monthly returns").waitFor();
    const d = section("Trade decisions");
    await d.getByText(/Value added by/).waitFor(SLOW);
    await d.getByRole("row").filter({ hasText: "2026-06-02" }).click();
    await d.getByText(/needs 40\+ sessions|Abnormal return after/).waitFor(SLOW);
    note(`decisions: ${(await d.innerText()).replace(/\n/g, " | ").slice(0, 800)}`);
  });
};
