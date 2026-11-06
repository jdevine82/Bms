// Browser test of schedules (milestone 7B): occupancy values set in the mapping table, a schedule
// that applies to the meter writes its setpoint, a one-off exception switches it, a holiday
// calendar is saved, and the HMI Schedules tab shows the state.
//   BMS_URL=http://127.0.0.1:8000 node tests/schedules.mjs [screenshot-dir]
// Uses the RS485 meter of config/site.example-setup.json (its registers 20-23 can be written).
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
const shot = async (page, name) => shots && (await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }));
// The site's local time (Sydney), as the schedule screens use.
const local = (offsetMin = 0) =>
  new Date(Date.now() + offsetMin * 60000).toLocaleString("sv-SE", { timeZone: "Australia/Sydney" }).slice(0, 16).replace(" ", "T");

const browser = await chromium.launch();
let page;
try {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => d.accept());
  await signIn(page, url);
  const api = page.request;
  await api.put(`${url}/api/setup/points/plant_meter2_cap`, { data: {
    name: "plant_meter2_cap", dis: "Demand cap", equip: "meter2", device: "rtu_meter", type: "sp", kind: "Number",
    unit: "kW", pollClass: "fast", tags: { elec: true, power: true }, writeLimits: { min: 10, max: 200 },
    address: { objectType: "holdingRegister", address: 22, dataType: "int16" } } });

  // ---------- occupancy values in the mapping table ----------
  await page.goto(`${url}/#/setup/mapping`);
  await page.getByLabel("Occupied value of plant_meter2_cap", { exact: true }).fill("150");
  await page.getByLabel("Unoccupied value of plant_meter2_cap").fill("50");
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByText(/Saved 1 change/).waitFor({ timeout: 10000 });
  check(true, "occupancy values set in the mapping table");

  // ---------- a holiday calendar ----------
  await page.goto(`${url}/#/schedules`);
  await page.getByRole("tab", { name: "Holiday calendars" }).click();
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByLabel("Calendar name").fill("NSW public holidays");
  await page.getByRole("button", { name: "Add date" }).click();
  await page.getByLabel("Holiday from").fill("2026-12-25");
  await page.getByLabel("Holiday note").fill("Christmas Day");
  await page.getByRole("button", { name: "Save calendar" }).click();
  await page.getByText("Saved.", { exact: true }).waitFor();
  check(true, "a holiday calendar is saved");

  // ---------- a schedule over the meter ----------
  await page.getByRole("tab", { name: "Schedules" }).click();
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByLabel("Schedule name").fill("Office hours");
  const week = page.getByRole("table", { name: "Weekly pattern" });
  for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]) {
    await week.getByRole("button", { name: `Add a period on ${day}` }).click();
    await page.getByLabel(`${day} from`).fill("00:00");
    await page.getByLabel(`${day} to`).fill("23:59");
  }
  await page.getByLabel("NSW public holidays").check();
  await page.getByLabel("Sub meter").check();
  await page.getByRole("button", { name: "Add schedule" }).click();
  await page.getByText(/^Saved. Now Occupied/).waitFor({ timeout: 10000 });
  check(true, "a schedule is added and is Occupied now");
  await page.getByText("Demand cap: 150 occupied, 50 unoccupied").waitFor();
  check(true, "the schedule lists the point that follows it");
  await shot(page, "schedule");

  const value = async () => {
    const pts = await (await api.get(`${url}/api/points`)).json();
    return pts.find((p) => p.name === "plant_meter2_cap").cur.value;
  };
  const waitValue = async (want) => {
    for (let i = 0; i < 60 && (await value()) !== want; i++) await page.waitForTimeout(500);
    return value();
  };
  check((await waitValue(150)) === 150, "the setpoint follows the schedule: 150 when occupied");

  // ---------- a one-off exception ----------
  await page.getByRole("button", { name: "Add exception" }).click();
  await page.getByLabel("Exception from").fill(local(-60));
  await page.getByLabel("Exception to").fill(local(60));
  await page.getByLabel("Exception state").selectOption("0");
  await page.getByLabel("Exception note").fill("fire drill");
  await page.getByRole("button", { name: "Save schedule" }).click();
  await page.getByText(/^Saved. Now Unoccupied/).waitFor({ timeout: 10000 });
  check((await waitValue(50)) === 50, "an exception makes it Unoccupied and the setpoint goes to 50");
  const listed = page.getByRole("region", { name: "Schedules" }).getByText("Unoccupied", { exact: true });
  check((await listed.count()) >= 1, "the list shows the state");

  // ---------- the HMI ----------
  const hmi = await context.newPage();
  await hmi.setViewportSize({ width: 1024, height: 600 });
  await hmi.goto(`${url}/hmi`);
  await hmi.getByRole("button", { name: "Schedules" }).click();
  await hmi.getByText("Office hours").waitFor();
  await hmi.getByText(/^Unoccupied until/).waitFor();
  check((await hmi.getByText("exception: fire drill").count()) === 1, "the HMI Schedules tab shows the state and why");
  await shot(hmi, "hmi-schedules");

  const audit = await (await api.get(`${url}/api/audit?action=schedule-run`)).json();
  check(audit.some((e) => e.summary.startsWith("schedule office_hours: Unoccupied (exception: fire drill)")), "the audit log has the schedule's change");
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  failures.push(String(e));
  console.log(`FAIL ${e}`);
  if (shots && page) await page.screenshot({ path: `${shots}/failure.png`, fullPage: true });
} finally {
  await browser.close();
}
if (failures.length) {
  console.log(`${failures.length} failed`);
  process.exit(1);
}
console.log("all passed");
