// Feature: TFSA room (synthetic numbers only). Unconfigured card → Settings anchor 20000 → record a
// 1000 contribution → over-room warning, Cancel → delete through the confirm dialog.
// Run on an instance with no TFSA anchor yet:
//   qtv browse tfsa .claude/skills/verify-qtfolio/journeys/tfsa.mjs
import { ensureSynthetic } from "./lib/seed.mjs";

export default async ({ page, base, step, note }) => {
  const card = () => page.locator("section", { has: page.getByRole("heading", { name: /^TFSA room/ }) });

  await step("seed", async () => note(`imported now: ${await ensureSynthetic(page, base)}`));

  await step("unconfigured", async () => {
    await page.goto(base + "/");
    await card().getByText("Enter CRA's Jan 1 contribution room in").waitFor();
  });

  await step("anchor", async () => {
    await page.goto(base + "/settings");
    const panel = page.locator("section", { has: page.getByRole("heading", { name: "TFSA contribution room" }) });
    await panel.getByPlaceholder("Room (CAD)").fill("20000");
    await Promise.all([
      page.waitForResponse((r) => r.url().endsWith("/api/tfsa/config") && r.request().method() === "PUT" && r.ok()),
      panel.getByRole("button", { name: "Save", exact: true }).click(),
    ]);
  });

  await step("record", async () => {
    await page.goto(base + "/");
    await card().getByRole("button", { name: "Record", exact: true }).first().click();
    await card().getByPlaceholder("Amount (CAD)").fill("1000");
    await card().getByRole("button", { name: "Record", exact: true }).last().click();
    await card().getByText("Recorded.").waitFor();
    await card().getByText("$19,000.00").first().waitFor();
    await card().getByRole("button", { name: "History (1)" }).waitFor();
  });

  await step("warning-cancel", async () => {
    await card().getByPlaceholder("Amount (CAD)").fill("50000");
    await card().getByRole("button", { name: "Record", exact: true }).last().click();
    await card().getByText(/exceeds your remaining/).waitFor();
    await card().getByRole("button", { name: "Record anyway" }).waitFor();
    await card().getByRole("button", { name: "Cancel" }).click();
    await card().getByRole("button", { name: "History (1)" }).waitFor();
  });

  await step("delete", async () => {
    page.once("dialog", (d) => d.accept());
    await card().getByRole("button", { name: "History (1)" }).click();
    await card().getByTitle("Delete").click();
    await card().getByRole("button", { name: "History (0)" }).waitFor();
  });
};
