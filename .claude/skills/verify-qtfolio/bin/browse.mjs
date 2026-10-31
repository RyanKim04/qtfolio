// Playwright journey runner for qtv, invoked as `qtv browse <journey> <script.mjs>`.
//
// The journey script default-exports `async ({ page, base, step, shot, aria, note }) => {}`:
//   page            Playwright Page (system Google Chrome, headless, 1440×900)
//   base            frontend origin, e.g. http://127.0.0.1:5273
//   step(name, fn)  run fn, then save NN-<name>.png + NN-<name>.aria.yml (action + resulting state)
//   shot(name)      screenshot only · aria(name) ARIA snapshot only · note(text) append to notes.txt
// Evidence lands in <evidence>/<journey>/; console errors and failed requests go to console.log.
// A thrown error saves FAILED.png/.aria.yml and exits 1.
import { createRequire } from "node:module";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [base, outDir, scriptPath] = process.argv.slice(2);
const require = createRequire(join(process.env.QTV_PW, "package.json"));
const { chromium } = require("playwright-core");

mkdirSync(outDir, { recursive: true });
const consoleLog = join(outDir, "console.log");
writeFileSync(consoleLog, "");

const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => ["error", "warning"].includes(m.type()) && appendFileSync(consoleLog, `[${m.type()}] ${m.text()}\n`));
page.on("pageerror", (e) => appendFileSync(consoleLog, `[pageerror] ${e.message}\n`));
page.on("requestfailed", (r) => appendFileSync(consoleLog, `[requestfailed] ${r.method()} ${r.url()} ${r.failure()?.errorText}\n`));
page.on("response", (r) => r.status() >= 400 && appendFileSync(consoleLog, `[http ${r.status()}] ${r.request().method()} ${r.url()}\n`));

let n = 0;
const shot = async (name) => {
  const p = join(outDir, `${name}.png`);
  await page.screenshot({ path: p, fullPage: true });
  console.log(`  saved ${p}`);
};
const aria = async (name) => {
  const p = join(outDir, `${name}.aria.yml`);
  writeFileSync(p, await page.locator("body").ariaSnapshot());
  console.log(`  saved ${p}`);
};
const note = (text) => appendFileSync(join(outDir, "notes.txt"), `${text}\n`);
const step = async (name, fn) => {
  const tag = `${String(++n).padStart(2, "0")}-${name}`;
  console.log(`step ${tag}`);
  await fn();
  await shot(tag);
  await aria(tag);
};

let failed = false;
try {
  const journey = (await import(pathToFileURL(scriptPath).href)).default;
  await journey({ page, base, step, shot, aria, note });
  console.log(`JOURNEY OK → ${outDir}`);
} catch (e) {
  failed = true;
  console.error(`JOURNEY FAILED: ${e.stack || e}`);
  note(`FAILED: ${e.message}`);
  await shot("FAILED").catch(() => {});
  await aria("FAILED").catch(() => {});
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
