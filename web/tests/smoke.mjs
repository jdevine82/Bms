// Browser smoke test (milestone 2 spec 9): the points page shows live values, the trend page
// draws a chart, and Export CSV downloads a file. Needs the backend serving the built app:
//   BMS_URL=http://127.0.0.1:8000 node tests/smoke.mjs [screenshot-dir]
import { createRequire } from "node:module";
import { signIn } from "./login.mjs";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const url = process.env.BMS_URL ?? "http://127.0.0.1:8000";
const shots = process.argv[2];

const browser = await chromium.launch();
const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "PASS" : "FAIL"} ${message}`);
  if (!ok) failures.push(message);
};

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, acceptDownloads: true });
  await signIn(page, url);
  const power = page.getByTestId("value-site_meter1_power");
  await power.waitFor();
  await page.waitForFunction(
    () => /\d.*kW/.test(document.querySelector('[data-testid="value-site_meter1_power"]')?.textContent ?? ""),
    null,
    { timeout: 15000 },
  );
  check(/\d.*kW/.test(await power.textContent()), `points page shows live power: ${await power.textContent()}`);
  await page.getByText("Live", { exact: true }).waitFor({ timeout: 10000 });
  check(true, "live WebSocket connected");
  if (shots) await page.screenshot({ path: `${shots}/points.png`, fullPage: true });

  await page.locator("tr", { hasText: "site_meter1_power" }).getByRole("button", { name: "Trend" }).click();
  await page.getByTestId("trend-chart").locator("canvas").waitFor({ timeout: 10000 });
  check(true, "trend page draws a chart");
  await page.waitForTimeout(1500);
  if (shots) await page.screenshot({ path: `${shots}/trend.png`, fullPage: true });

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: /Export CSV/ }).first().click(),
  ]);
  const text = readFileSync(await download.path(), "utf8");
  check(text.startsWith("time (Australia/Sydney),site_meter1_power (kW)"), `CSV downloaded: ${download.suggestedFilename()}`);

  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await signIn(phone, url);
  await phone.getByTestId("value-site_meter1_power").waitFor();
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  check(!overflow, "no sideways page scroll at phone width");
  const box = await phone.getByTestId("value-site_meter1_power").boundingBox();
  check(box && box.x + box.width <= 390, "values are visible on a phone without scrolling the table");
  if (shots) await phone.screenshot({ path: `${shots}/phone.png` });
} finally {
  await browser.close();
}
if (failures.length) {
  console.error(`${failures.length} check(s) failed`);
  process.exit(1);
}
