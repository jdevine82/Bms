// Browser test of alarms (milestone 6B): set up delivery with a stand-in ntfy server and send a
// test message, add a high limit alarm to a point, see it raised in the header and on the
// Alarms page, acknowledge it with a comment, see the notification in the log, see it on the HMI
// overview, then remove the alarm and see it close.
//   BMS_URL=http://127.0.0.1:8000 node tests/alarms.mjs [screenshot-dir]
// Uses the point site_meter1_power (config/site.example.json or site.example-setup.json).
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { signIn } from "./login.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const url = process.env.BMS_URL ?? "http://127.0.0.1:8000";
const shots = process.argv[2];
const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "PASS" : "FAIL"} ${message}`);
  if (!ok) failures.push(message);
};
const shot = async (page, name) => shots && (await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }));

// A stand-in ntfy server: records what it is sent.
const pushes = [];
const ntfy = createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    pushes.push(JSON.parse(body || "{}"));
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("{}");
  });
});
await new Promise((r) => ntfy.listen(0, "127.0.0.1", r));
const ntfyUrl = `http://127.0.0.1:${ntfy.address().port}`;

const browser = await chromium.launch();
let page;
try {
  page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => d.accept());
  await signIn(page, url);

  // ---------- delivery settings ----------
  await page.goto(`${url}/#/setup/alarm-delivery`);
  await page.getByRole("heading", { name: "Alarm delivery" }).waitFor();
  await page.getByLabel("ntfy server").fill(ntfyUrl);
  await page.getByLabel("Warning by Email").uncheck();
  await page.getByRole("button", { name: "Add recipient" }).click();
  await page.getByLabel("Recipient name").last().fill("Jason");
  await page.getByLabel("ntfy topic of Jason").fill("devine-browser-test");
  await page.getByRole("button", { name: "Save alarm delivery settings" }).click();
  await page.getByText("Saved.", { exact: true }).waitFor();
  check(true, "delivery settings saved");
  const row = page.getByRole("table", { name: "Recipients" }).locator("tbody tr").last();
  await row.getByRole("button", { name: "ntfy push" }).click();
  await row.getByText("ntfy push: delivered").waitFor({ timeout: 10000 });
  check(pushes.some((p) => p.topic === "devine-browser-test" && p.title.includes("[Test]")), "a test ntfy push is delivered");
  await shot(page, "alarm-delivery");

  // ---------- an alarm on a point ----------
  await page.goto(`${url}/#/setup/alarm-definitions`);
  await page.getByLabel("Filter points").fill("site_meter1_power");
  await page.getByRole("button", { name: /site_meter1_power/ }).click();
  await page.getByRole("button", { name: "Add high limit" }).click();
  await page.getByLabel("Limit").fill("1");
  await page.getByLabel("Alarm message").fill("Main power high");
  await page.getByRole("button", { name: "Save alarms" }).click();
  await page.getByText("Saved; the alarms are live.").waitFor();
  check(true, "a high limit alarm is added to a point");

  const button = page.getByRole("button", { name: /^Alarms: 1 not acknowledged/ });
  await button.waitFor({ timeout: 15000 });
  check(true, "the header shows the unacknowledged alarm");
  await button.click();
  const alarmRow = page.locator('tr[data-alarm="Main power high"]');
  await alarmRow.waitFor();
  check((await alarmRow.textContent()).includes("Warning"), "the Alarms page lists it with its level as text");
  await shot(page, "alarms-active");
  await alarmRow.getByRole("checkbox").check();
  await page.getByLabel("Comment", { exact: true }).fill("checked on site");
  await page.getByRole("button", { name: "Acknowledge", exact: true }).click();
  await page.getByText("Acknowledged 1.").waitFor();
  await alarmRow.getByText("Active, acknowledged").waitFor();
  check(true, "acknowledged with a comment");
  await alarmRow.click();
  await page.getByText("checked on site").waitFor();
  check(true, "the history shows the comment");
  check(pushes.some((p) => p.title?.includes("Main power high") && p.priority === 4), "the warning was pushed by ntfy");
  await page.getByRole("tab", { name: "Notifications sent" }).click();
  await page.getByRole("table", { name: "Notifications" }).getByText("Sent").first().waitFor();
  check(true, "the notification is in the delivery log");

  // ---------- HMI ----------
  const hmi = await browser.newPage({ viewport: { width: 1024, height: 600 } });
  await signIn(hmi, url);
  await hmi.goto(`${url}/hmi`);
  await hmi.getByRole("region", { name: "Active alarms" }).getByText("Main power high").waitFor({ timeout: 15000 });
  check(true, "the HMI overview shows the active alarm");
  await shot(hmi, "hmi-alarms");

  // ---------- remove the alarm: it returns to normal and closes ----------
  await page.goto(`${url}/#/setup/alarm-definitions`);
  await page.getByLabel("Filter points").fill("site_meter1_power");
  await page.getByRole("button", { name: /site_meter1_power/ }).click();
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await page.getByRole("button", { name: "Save alarms" }).click();
  await page.getByText("Saved; the alarms are live.").waitFor();
  await page.goto(`${url}/#/alarms`);
  await page.getByText("No active alarms.").waitFor({ timeout: 15000 });
  check(true, "removed and acknowledged: the alarm closes");
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  failures.push(String(e));
  console.log(`FAIL ${e}`);
  if (shots && page) await page.screenshot({ path: `${shots}/failure.png`, fullPage: true });
} finally {
  await browser.close();
  ntfy.close();
}
if (failures.length) {
  console.log(`${failures.length} failed`);
  process.exit(1);
}
console.log("all passed");
