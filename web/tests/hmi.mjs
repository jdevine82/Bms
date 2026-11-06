// HMI browser tests (milestone 3 spec 6). Needs the backend serving the built app and the simulator:
//   BMS_URL=http://127.0.0.1:8000 node tests/hmi.mjs [screenshot-dir]
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { signIn } from "./login.mjs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const url = process.env.BMS_URL ?? "http://127.0.0.1:8000";
const shots = process.argv[2];
const here = dirname(fileURLToPath(import.meta.url));
const waitingPage = pathToFileURL(resolve(here, "../../hmi/waiting.html")).href;

const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "PASS" : "FAIL"} ${message}`);
  if (!ok) failures.push(message);
};

const browser = await chromium.launch();
try {
  for (const [width, height] of [
    [1024, 600],
    [1280, 720],
  ]) {
    const size = `${width}x${height}`;
    const context = await browser.newContext({ viewport: { width, height }, hasTouch: true });
    const page = await context.newPage();

    // Lets the test cut the live connection, as if the server went away.
    let blocked = false;
    let current = null;
    await page.routeWebSocket("**/api/live", (ws) => {
      if (blocked) {
        ws.close();
        return;
      }
      current = ws;
      ws.connectToServer();
    });

    await signIn(page, url); // milestone 6A: a login (or a listed kiosk address) is needed
    await page.goto(`${url}/hmi`);
    await page.waitForFunction(
      () => /\d.*kW/.test(document.querySelector('[data-testid="hmi-value-site_meter1_power"]')?.textContent ?? ""),
      null,
      { timeout: 15000 },
    );
    check(true, `${size}: Overview shows live power`);
    check(
      (await page.evaluate(() => document.documentElement.dataset.theme)) === "dark",
      `${size}: dark theme`,
    );
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    check(!overflow, `${size}: no sideways scroll`);
    if (shots) await page.screenshot({ path: `${shots}/hmi-overview-${size}.png` });

    const small = await page.evaluate(() =>
      [...document.querySelectorAll("button")]
        .filter((b) => b.offsetParent !== null)
        .map((b) => ({ text: b.textContent, h: b.getBoundingClientRect().height }))
        .filter((b) => b.h < 44),
    );
    check(small.length === 0, `${size}: every button is at least 44 px high ${JSON.stringify(small)}`);

    const tabBar = await page.locator(".hmi-tabs").boundingBox();
    check(tabBar && tabBar.y + tabBar.height <= height + 1, `${size}: bottom tabs are on screen`);
    check(tabBar && tabBar.height <= 70, `${size}: bottom tab bar is compact (${tabBar?.height} px)`);
    const activeTab = await page.locator(".hmi-tabs button.active").evaluate((b) => {
      const s = getComputedStyle(b);
      return { color: s.color, background: s.backgroundColor, text: b.textContent };
    });
    check(
      activeTab.text === "Overview" && activeTab.color !== activeTab.background,
      `${size}: active tab label is readable ${JSON.stringify(activeTab)}`,
    );
    const mainBox = await page.locator(".hmi-main").boundingBox();
    check(mainBox && mainBox.width >= width - 2, `${size}: content uses the full width (${mainBox?.width} px)`);

    await page.getByRole("button", { name: "Graphics" }).tap();
    await page.getByRole("tab", { name: "Site" }).waitFor({ timeout: 10000 });
    check(await page.locator("svg.graphic").isVisible(), `${size}: Graphics shows the Site graphic`);

    await page.getByRole("button", { name: "Trends" }).tap();
    await page.locator(".hmi-trend-chart canvas").waitFor({ timeout: 10000 });
    check(true, `${size}: Trends draws a chart`);
    if (shots) await page.screenshot({ path: `${shots}/hmi-trends-${size}.png` });

    await page.getByRole("button", { name: "Overview" }).tap();
    blocked = true;
    current?.close();
    await page.getByRole("alert").waitFor({ timeout: 10000 });
    check(true, `${size}: banner when the server cannot be reached`);
    if (shots) await page.screenshot({ path: `${shots}/hmi-offline-${size}.png` });
    blocked = false;
    await page.getByRole("alert").waitFor({ state: "detached", timeout: 20000 });
    check(true, `${size}: banner clears when the server returns`);

    await context.close();
  }

  // Waiting page: waits while the server is down, then opens <server>/hmi.
  const port = 8011;
  const root = mkdtempSync(join(tmpdir(), "bms-waiting-"));
  mkdirSync(join(root, "api"));
  writeFileSync(join(root, "api", "health"), "{}");
  mkdirSync(join(root, "hmi")); // served as a page (a plain file would be sent as a download)
  writeFileSync(join(root, "hmi", "index.html"), "<!doctype html><title>HMI</title><p>hmi loaded</p>");
  const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
  await page.goto(`${waitingPage}?server=http://127.0.0.1:${port}`);
  await page.getByText(/No answer yet/).waitFor({ timeout: 10000 });
  check(true, "waiting page waits while the server is down");
  if (shots) await page.screenshot({ path: `${shots}/hmi-waiting.png` });
  const server = spawn("python3", ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", root], {
    stdio: "ignore",
  });
  try {
    await page.waitForURL(new RegExp(`^http://127\\.0\\.0\\.1:${port}/hmi/?$`), { timeout: 15000 });
    check(true, "waiting page opens /hmi when the server answers");
  } finally {
    server.kill();
  }

  const bad = await browser.newPage();
  await bad.goto(waitingPage);
  check(await bad.getByText("No site server address is set.").isVisible(), "waiting page explains a missing address");
} finally {
  await browser.close();
}

if (failures.length) {
  console.error(`${failures.length} check(s) failed`);
  process.exit(1);
}
