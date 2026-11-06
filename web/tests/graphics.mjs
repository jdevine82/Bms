// Browser test of graphics (milestone 9A): starting graphics made from the rooms and equipment,
// the tab strip, live states (On, Off, Fault, Hand, Offline), a room's colour rule, tapping a
// room to open its tab, tapping a symbol to command it, and the HMI's Graphics tab.
//   BMS_URL=http://127.0.0.1:8000 node tests/graphics.mjs [screenshot-dir]
// Uses config/site.example-setup.json's RS485 line: the I/O module (unit 2, coils written by
// the BMS read back) and the meter's register 23.
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

const coil = (address) => ({ objectType: "coil", address, dataType: "bit" });
const POINTS = [
  ["pump1_cmd", "Pump 1 start", "pump1", "cmd", "Bool", { run: true }, "rtu_io", coil(1)],
  ["pump1_state", "Pump 1 running", "pump1", "sensor", "Bool", { run: true }, "rtu_io", coil(1)],
  ["ahu1_fan_cmd", "AHU 1 fan start", "ahu1", "cmd", "Bool", { fan: true, run: true }, "rtu_io", coil(2)],
  ["ahu1_fan_state", "AHU 1 fan running", "ahu1", "sensor", "Bool", { fan: true, run: true }, "rtu_io", coil(2)],
  ["ahu1_filter", "AHU 1 filter dirty", "ahu1", "sensor", "Bool", { alarm: true, filter: true }, "rtu_io", coil(5)],
  ["ahu1_filter_test", "AHU 1 filter test", "ahu1", "cmd", "Bool", {}, "rtu_io", coil(5)],
  ["light1_cmd", "Office lights", "light1", "cmd", "Bool", { run: true }, "rtu_io", coil(3)],
  ["office_temp", "Office temperature", null, "sensor", "Number", { air: true, temp: true, zone: true }, "rtu_meter",
    { objectType: "holdingRegister", address: 23, dataType: "uint16" }],
  // Written by the test to move the temperature (same register).
  ["office_temp_test", "Office temperature (test)", null, "sp", "Number", {}, "rtu_meter",
    { objectType: "holdingRegister", address: 23, dataType: "uint16" }],
  ["ghost_cmd", "Pump 2 start", "pump2", "cmd", "Bool", { run: true }, "rtu_ghost", coil(0)],
];

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 }, colorScheme: "dark" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await signIn(page, url);
  const api = page.request;
  const putItem = async (kind, name, item) => {
    const r = await api.put(`${url}/api/setup/${kind}/${name}`, { data: { name, ...item } });
    if (!r.ok()) throw new Error(`${kind}/${name}: ${await r.text()}`);
  };

  // ---------- a plant room and an office with equipment ----------
  // The I/O module (unit 2): an earlier test may have added it already.
  const devices = await (await api.get(`${url}/api/setup/devices`)).json();
  const io = devices.find((d) => d.connection?.bus === "rs485_1" && d.connection?.unitId === 2)?.name ?? "rtu_io";
  if (!devices.some((d) => d.name === io)) {
    await putItem("devices", io, { dis: "I/O module", protocol: "modbus", connection: { bus: "rs485_1", unitId: 2 } });
  }
  for (const p of POINTS) if (p[6] === "rtu_io") p[6] = io;
  await putItem("devices", "rtu_ghost", { dis: "Missing module", protocol: "modbus", connection: { bus: "rs485_1", unitId: 9 } });
  await putItem("equips", "pump1", { dis: "Pump 1", space: "plant", tags: { pump: true, motor: true } });
  await putItem("equips", "pump2", { dis: "Pump 2", space: "plant", tags: { pump: true, motor: true } });
  await putItem("equips", "ahu1", { dis: "AHU 1", space: "plant", tags: { ahu: true } });
  await putItem("equips", "light1", { dis: "Office lights", space: "office", tags: { lighting: true } });
  for (const [name, dis, equip, type, kind, tags, device, address] of POINTS) {
    await putItem("points", name, { dis, ...(equip ? { equip } : { space: "office" }), device, type, kind, tags, address,
      pollClass: "fast", ...(kind === "Number" ? { unit: "°C", ...(type === "sp" ? { writeLimits: { min: 0, max: 50 } } : {}) } : {}) });
  }
  check(true, "a pump, an air handler, lights and a room temperature are added");
  const ids = Object.fromEntries((await (await api.get(`${url}/api/points`)).json()).map((p) => [p.name, p.id]));
  const command = (name, value, extra = {}) =>
    api.post(`${url}/api/points/${ids[name]}/command`, { data: { value, confirm: true, ...extra } });
  for (const name of ["pump1_cmd", "ahu1_fan_cmd", "light1_cmd", "ahu1_filter_test"]) await command(name, false);
  await command("office_temp_test", 22);

  // ---------- starting graphics and tabs ----------
  // A run on a database where graphics were saved before: remove them to see the starting ones.
  const before = await (await api.get(`${url}/api/graphics`)).json();
  if (before.source === "setup") for (const g of before.graphics) await api.delete(`${url}/api/setup/graphics/${g.name}`);
  const listed = await (await api.get(`${url}/api/graphics`)).json();
  check(listed.source === "generated", "with no saved graphics the starting graphics are shown");
  await page.goto(`${url}/#/graphics`);
  await page.reload(); // the app reads the point list when it loads
  const tabs = page.getByRole("tablist", { name: "Graphics" });
  await tabs.getByRole("tab").first().waitFor();
  const names = await tabs.getByRole("tab").allTextContents();
  check(names[0] === "Site" && names.includes("Plant room") && names.includes("Office"),
        `the tab strip shows Site first, then the rooms (${names.join(", ")})`);

  const room = page.getByRole("button", { name: "Open Office" });
  await room.getByText("22 °C").waitFor({ timeout: 20000 });
  const stroke = () => room.locator("rect").getAttribute("stroke");
  check((await stroke()) === "var(--g-green)", "the office at 22 °C is green (comfortable)");
  await command("office_temp_test", 30);
  await room.getByText("30 °C").waitFor({ timeout: 20000 });
  check((await stroke()) === "var(--g-red)", "at 30 °C it turns red (hot)");
  await shot(page, "graphics-site");

  await room.click();
  await page.getByRole("tab", { name: "Office", selected: true }).waitFor();
  check(page.url().endsWith("#/graphics/room_office"), "tapping a room opens its tab (and the address follows)");
  check(await page.getByRole("button", { name: /^Office lights: Off/ }).isVisible(), "the office graphic has its lights");

  // ---------- states on the plant room ----------
  await page.getByRole("tab", { name: "Plant room" }).click();
  const pump = page.locator('[data-id="pump1"]');
  await pump.waitFor();
  check((await pump.getAttribute("data-state")) === "off", "Pump 1 is Off");
  await page.getByRole("button", { name: /^Pump 1: Off/ }).click();
  const panel = page.getByRole("dialog", { name: "Command Pump 1 start" });
  await panel.waitFor();
  check(true, "tapping the pump opens its command panel");
  await panel.getByRole("button", { name: "On", exact: true }).click();
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/Done\./).waitFor({ timeout: 20000 });
  await panel.getByRole("button", { name: "Close" }).click();
  await page.locator('[data-id="pump1"][data-state="manual"]').waitFor({ timeout: 20000 });
  const spins = await page.locator('[data-id="pump1"].spinning').waitFor({ timeout: 20000 }).then(() => true, () => false);
  check(spins, "started from the graphic (hand mode), Pump 1 runs and spins");
  check(await pump.locator(".g-badge-hand").isVisible() && await page.getByRole("button", { name: /^Pump 1: Hand/ }).isVisible(),
        "hand mode shows Hand in words with the hand badge");

  // The AHU's duct follows its fan: no flow while it is off, flow once it runs.
  const duct = page.locator('[data-id="ahu1-duct"]');
  check((await duct.getAttribute("data-flow")) === "no", "the AHU duct shows no flow while the fan is off");
  await command("ahu1_fan_cmd", true);
  await page.locator('[data-id="ahu1-duct"][data-flow="yes"]').waitFor({ state: "attached", timeout: 20000 });
  check((await page.locator('[data-id="ahu1-fan"]').getAttribute("data-state")) === "on", "the duct flows once the fan is On");

  await command("ahu1_filter_test", true);
  await page.locator('[data-id="ahu1-filter"][data-state="fault"]').waitFor({ timeout: 20000 });
  check(await page.getByRole("img", { name: /^Filter: Fault/ }).isVisible(), "a dirty filter shows Fault in words");

  await page.locator('[data-id="pump2"][data-state="offline"]').waitFor({ timeout: 15000 });
  check(await page.locator('[data-id="pump2"] .g-offline').isVisible(), "a pump on a missing module is Offline (dashed)");
  await shot(page, "graphics-plant");
  await page.emulateMedia({ colorScheme: "light" });
  await shot(page, "graphics-plant-light");

  // ---------- the HMI's Graphics tab ----------
  const hmiContext = await browser.newContext({ viewport: { width: 1024, height: 600 }, hasTouch: true,
                                                storageState: await context.storageState() });
  const hmi = await hmiContext.newPage();
  hmi.on("pageerror", (e) => errors.push(e.message));
  await hmi.goto(`${url}/hmi`);
  await hmi.getByRole("button", { name: "Graphics" }).click();
  await hmi.getByRole("tab", { name: "Site" }).waitFor();
  await hmi.getByRole("tab", { name: "Plant room" }).click();
  await hmi.locator('[data-id="pump1"][data-state="manual"]').waitFor({ timeout: 20000 });
  await hmi.getByRole("button", { name: /^Pump 1: Hand/ }).click();
  await hmi.getByRole("dialog", { name: "Command Pump 1 start" }).waitFor();
  check(true, "the HMI Graphics tab shows the plant room and tapping opens the command panel");
  await shot(hmi, "graphics-hmi");
  await hmi.getByRole("dialog").getByRole("button", { name: "Release to auto" }).click();
  await hmi.getByRole("dialog").getByText("Released to auto.").waitFor({ timeout: 20000 });
  await hmiContext.close();

  // ---------- a phone ----------
  const phone = await browser.newContext({ ...require("playwright").devices["iPhone 13"],
                                           storageState: await context.storageState() });
  const ph = await phone.newPage();
  await ph.goto(`${url}/#/graphics/room_plant`);
  await ph.getByRole("tab", { name: "Plant room" }).waitFor();
  const wide = await ph.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!wide, "on a phone the page does not scroll sideways (the graphic scrolls in its frame)");
  await shot(ph, "graphics-phone");
  await phone.close();

  // ---------- saving and Setup > Graphics ----------
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${url}/#/setup/graphics`);
  await page.getByRole("heading", { name: "Graphics" }).waitFor();
  await page.getByRole("row", { name: /room_plant/ }).getByRole("button", { name: "Edit as JSON" }).click();
  const box = page.getByLabel("Graphic room_plant (JSON)");
  const g = JSON.parse(await box.inputValue());
  g.dis = "Plant";
  g.items.push({ type: "value", id: "bad", x: 0, y: 0, point: "no_such_point" });
  await box.fill(JSON.stringify(g, null, 2));
  await page.getByRole("button", { name: "Check" }).click();
  await page.getByText("point 'no_such_point' does not exist").waitFor();
  check(true, "a graphic that fails the checks is refused with the reason");
  g.items.pop();
  await box.fill(JSON.stringify(g, null, 2));
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Saved Plant; it is live.").waitFor();
  const saved = await (await api.get(`${url}/api/graphics`)).json();
  check(saved.source === "setup" && saved.graphics.some((x) => x.dis === "Plant"), "saving one saves the graphics into the setup");
  page.on("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "Make starting graphics again" }).click();
  await page.getByText("Made the starting graphics from the rooms and equipment; they are live.").waitFor();
  const again = await (await api.get(`${url}/api/graphics`)).json();
  check(again.graphics.some((x) => x.dis === "Plant room"), "making the starting graphics again replaces the saved ones");

  // Clean up the missing module so later tests do not wait on it.
  await api.delete(`${url}/api/setup/points/ghost_cmd`);
  await api.delete(`${url}/api/setup/equips/pump2`);
  await api.delete(`${url}/api/setup/devices/rtu_ghost`);
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
