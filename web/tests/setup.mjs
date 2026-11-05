// Browser test of the setup screens (milestone 5B): Discover finds the I/O modules on the RS485
// bus, the commissioning wizard adds one with its points, the mapping table edits tags and
// round-trips a CSV, a device test read passes, an equipment template is copied and saved, and
// the change log records it all.
//
// Needs a fresh database loaded from config/site.example-setup.json, the simulators and the
// backend serving the built app (see docs/specs/m5-setup.md, "Running the screen tests"):
//   BMS_URL=http://127.0.0.1:8000 node tests/setup.mjs [screenshot-dir]
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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
const api = async (path) => (await fetch(`${url}${path}`)).json();
const shot = async (page, name) => shots && (await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }));

let page;
try {
  page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let promptAnswer = "";
  page.on("dialog", (d) => d.accept(d.type() === "prompt" ? promptAnswer : undefined));

  // ---------- Discover ----------
  await page.goto(`${url}/#/setup/discover`);
  const rtu = page.locator('[data-network="rtu:rs485_1"]');
  await rtu.getByLabel("Last unit ID").fill("4");
  await rtu.getByRole("button", { name: "Scan" }).click();
  await page.waitForFunction(
    () => /Done/.test(document.querySelector('[data-network="rtu:rs485_1"] .scan-progress')?.textContent ?? ""),
    null,
    { timeout: 30000 },
  );
  const results = page.getByRole("table", { name: "Results" });
  const unit2 = results.locator("tr", { hasText: "unit 2" });
  check((await unit2.textContent()).includes("New"), "Discover: unit 2 on the RS485 bus is new");
  check((await results.locator("tr", { hasText: "unit 1" }).textContent()).includes("Already added"), "Discover: unit 1 is already added");
  check((await results.locator("tr", { hasText: "unit 3" }).count()) === 1, "Discover: unit 3 found");
  await unit2.click();
  await page.getByLabel("Device name").fill("io2");
  await page.getByLabel("Display name").fill("Office I/O");
  await shot(page, "discover");
  await page.getByRole("button", { name: "Commission with a template…" }).click();

  // ---------- Wizard step 1 ----------
  await page.getByRole("heading", { name: "1. Add the device and test it" }).waitFor();
  check((await page.getByLabel("Device name").inputValue()) === "io2", "Wizard: opened with the device from Discover");
  await page.getByLabel("Device template").selectOption("cwt-mb328b");
  await page.getByRole("button", { name: "Test connection" }).click();
  await page.getByText(/points read/).first().waitFor({ timeout: 20000 });
  const readLine = await page.getByText(/points read/).first().textContent();
  const mapSize = Number(readLine.trim().split(" ")[0]);
  check(/^(\d+) of \1 points read$/.test(readLine.trim()), `Wizard: test connection reads the whole map (${readLine.trim()})`);
  await shot(page, "wizard-1");
  await page.getByRole("button", { name: "Next: equipment" }).click();

  // ---------- Wizard step 2 ----------
  await page.getByLabel("Name pattern", { exact: true }).fill("lights2");
  await page.getByLabel("Display name pattern").fill("Office lights");
  await page.getByLabel("Room", { exact: true }).selectOption("office");
  await page.getByRole("button", { name: "Build the table" }).click();
  await page.getByText(/points in the draft/).waitFor({ timeout: 10000 });
  const grid = page.getByRole("table", { name: "Points" });
  const rows = await grid.locator("tbody tr").count();
  check(rows === mapSize, `Wizard: one table of the new points (${rows} rows)`);
  await page.getByLabel("Display name of office_lights2_do0").fill("Office lights relay");
  await page.getByLabel("Name of office_lights2_di7", { exact: true }).fill("office_lights2_door");
  await page.getByRole("button", { name: "Test the new points" }).click();
  await page.getByText(`${mapSize} of ${mapSize} points read.`).waitFor({ timeout: 20000 });
  check(true, "Wizard: the new points test as communicating");
  check((await api("/api/setup/points/office_lights2_do0")).detail !== undefined, "Wizard: nothing is saved before go-live");
  await shot(page, "wizard-2");
  await page.getByRole("button", { name: "Next: verify" }).click();

  // ---------- Wizard step 3 ----------
  await page.getByRole("heading", { name: "Ready to go live" }).waitFor({ timeout: 10000 });
  await shot(page, "wizard-3");
  await page.getByRole("button", { name: "Go live", exact: true }).click();
  await page.getByRole("heading", { name: "4. Place on graphics (optional)" }).waitFor({ timeout: 10000 });
  const relay = await api("/api/setup/points/office_lights2_do0");
  check(relay.dis === "Office lights relay" && relay.device === "io2", "Wizard: go-live saved the points with the table's edits");
  check((await api("/api/setup/points/office_lights2_door")).name === "office_lights2_door", "Wizard: a renamed new point is saved under its new name");
  check((await api("/api/setup/equips/lights2")).space === "office", "Wizard: the equipment is in the chosen room");

  // ---------- Mapping table ----------
  await page.getByRole("button", { name: "Finish and open the mapping table" }).click();
  await page.getByRole("heading", { name: "Room mapping table" }).waitFor();
  await page.getByRole("button", { name: /^Untagged/ }).click();
  const untagged = await page.getByRole("table", { name: "Points" }).locator("tbody tr").count();
  const chip = Number((await page.getByRole("button", { name: /^Untagged/ }).textContent()).match(/\d+/)[0]);
  check(untagged === chip && (await page.locator('tr[data-point="site_meter1_power"]').count()) === 1,
    `Mapping: the untagged filter shows the untagged points (${untagged})`);
  const tags = page.getByLabel("Tags of site_meter1_power");
  await tags.fill("elec, power");
  await tags.blur();
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Saved 1 change; they are live.").waitFor({ timeout: 10000 });
  const power = await api("/api/setup/points/site_meter1_power");
  check(power.tags.elec === true && power.tags.power === true, "Mapping: tags edited in place are saved");

  await page.getByRole("button", { name: /^All/ }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).click()]);
  const csv = readFileSync(await download.path(), "utf8");
  check(csv.startsWith("name,dis,equip,room,tags") && csv.includes("office_lights2_do0"), "Mapping: CSV export has every point");
  const edited = csv.replace("site_meter1_voltage,Main meter voltage L1-N", "site_meter1_voltage,Mains voltage");
  const file = join(tmpdir(), "bms-mapping.csv");
  writeFileSync(file, edited);
  await page.locator(".file-pick input").setInputFiles(file);
  await page.getByText(/1 point changed/).waitFor();
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Saved 1 change; they are live.").waitFor({ timeout: 10000 });
  check((await api("/api/setup/points/site_meter1_voltage")).dis === "Mains voltage", "Mapping: a CSV import is saved");
  await shot(page, "mapping");

  // ---------- Devices: test connection ----------
  await page.goto(`${url}/#/setup/devices`);
  await page.getByRole("button", { name: /Simulated power meter/ }).click();
  await page.getByRole("button", { name: "Test connection" }).click();
  await page.getByText(/5 of 5 points read/).waitFor({ timeout: 15000 });
  check(true, "Devices: a saved device's test read passes");
  await page.getByText(/Raw requests and answers/).click();
  check((await page.locator(".raw pre").first().textContent()).startsWith("→ "), "Devices: raw requests are shown");
  await shot(page, "devices");

  // ---------- Equipment templates: copy and save ----------
  await page.goto(`${url}/#/setup/equip-templates`);
  await page.getByRole("button", { name: /^Power meter/ }).click();
  promptAnswer = "power-meter-site";
  await page.getByRole("button", { name: "Copy template" }).click();
  await page.getByLabel("Display name of power").fill("Active power");
  await page.getByRole("button", { name: "Save as version 1.0.0" }).click();
  await page.getByText(/Saved version 1.0.0/).waitFor({ timeout: 10000 });
  const saved = await api("/api/setup/equip-templates/power-meter-site");
  check(saved.source === "site" && saved.template.roles.find((r) => r.role === "power").dis === "Active power", "Templates: a copied template is saved for the site");
  await shot(page, "equip-templates");

  // ---------- Change log ----------
  await page.goto(`${url}/#/setup/changes`);
  await page.getByText(`commissioned io2: ${mapSize} points`).waitFor({ timeout: 10000 });
  check(true, "Change log: the go-live is recorded");
  check((await page.getByText(/started a modbus_rtu scan/).count()) >= 1, "Change log: the scan is recorded");

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
