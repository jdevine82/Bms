// Browser test of maintenance counters (main spec 5.10) against the setup-screens backend
// (config/site.example-setup.json, the RTU simulator with I/O modules at units 2 and 3):
//   BMS_URL=http://127.0.0.1:8000 node tests/counters.mjs [screenshot-dir]
import { createRequire } from "node:module";
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

const browser = await chromium.launch();
let page;
try {
  page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept());
  await signIn(page, url);
  const api = page.request;
  const ok = async (r) => { if (!r.ok()) throw new Error(`${r.url()}: ${await r.text()}`); return r.json(); };

  // A fan whose run input is a coil on an I/O module (coil 0 is on in the simulator).
  await ok(await api.put(`${url}/api/setup/devices/io2`, { data: {
    name: "io2", dis: "I/O module", protocol: "modbus", connection: { bus: "rs485_1", unitId: 2 } } }));
  await ok(await api.put(`${url}/api/setup/equips/fan1`, { data: { name: "fan1", dis: "Supply fan", tags: { fan: true } } }));
  await ok(await api.put(`${url}/api/setup/points/site_fan1_run`, { data: {
    name: "site_fan1_run", dis: "Fan running", equip: "fan1", device: "io2", type: "sensor", kind: "Bool",
    pollClass: "fast", tags: { run: true }, address: { objectType: "coil", address: 0, dataType: "bit" } } }));
  await ok(await api.put(`${url}/api/setup/points/site_fan1_cmd`, { data: {
    name: "site_fan1_cmd", dis: "Fan start", equip: "fan1", device: "io2", type: "cmd", kind: "Bool",
    pollClass: "fast", tags: {}, address: { objectType: "coil", address: 1, dataType: "bit" } } }));
  const names = (await (await api.get(`${url}/api/points`)).json()).map((p) => p.name);
  check(names.includes("site_fan1_run_hours") && names.includes("site_fan1_starts"),
        "the counter points are made automatically for equipment with a run point");

  // ---------- Setup -> Equipment ----------
  await page.goto(`${url}/#/setup/equipment`);
  await page.getByRole("button", { name: /Supply fan/ }).first().click();
  const panel = page.getByRole("region", { name: "Maintenance counters" });
  await panel.waitFor();
  await panel.getByTestId("counter-hours").waitFor();
  check(/h$/.test(await panel.getByTestId("counter-hours").textContent()), "the equipment shows its run hours");
  check(await panel.getByText("counted by the BMS from site_fan1_run").isVisible(), "and where they come from");
  await panel.getByLabel("Every (run hours)").fill("0.001");
  await panel.getByLabel("Alarm level").selectOption("warning");
  await panel.getByRole("button", { name: "Save interval" }).click();
  await panel.getByText("Service interval saved.").waitFor();
  // 0.001 h is 3.6 s of running.
  await page.waitForTimeout(5000);
  await page.reload();
  await page.getByRole("button", { name: /Supply fan/ }).first().click();
  await panel.getByText(/Service due/).waitFor({ timeout: 15000 });
  check(true, "the service comes due once the run hours since the last service reach the interval");
  const alarms = await (await api.get(`${url}/api/alarms`)).json();
  check(alarms.some((a) => a.message.startsWith("Service due: fan1") && a.level === "warning" && a.active),
        "and raises a Warning alarm");
  if (shots) await page.screenshot({ path: `${shots}/counters-due.png`, fullPage: true });
  await panel.getByRole("button", { name: "Service done" }).click();
  await panel.getByText("Service marked as done.").waitFor();
  check(await panel.getByText(/serviced .* by testadmin/).isVisible(), "Service done records who and when");
  const audit = await (await api.get(`${url}/api/audit?action=service-done`)).json();
  check(audit.length === 1 && audit[0].name === "fan1", "and is in the audit log");

  // ---------- the tap-to-control panel ----------
  await page.goto(`${url}/#/points`);
  const row = page.getByRole("row").filter({ hasText: "site_fan1_cmd" });
  await row.getByRole("button", { name: "Command" }).click();
  const dialog = page.getByRole("dialog", { name: "Command Fan start" });
  await dialog.getByLabel("Run hours and starts").waitFor();
  check(/h run · \d+ starts/.test(await dialog.getByLabel("Run hours and starts").textContent()),
        "the command panel shows the equipment's run hours and starts");
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && page) await page.screenshot({ path: `${shots}/counters-failure.png`, fullPage: true }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
