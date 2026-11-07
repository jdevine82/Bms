// Browser test of standalone mode (milestone 11a), against a backend started with BMS_MODE=standalone
// on an empty database, with no state file and config/site.example-setup.json as the config file.
//   BMS_URL=http://127.0.0.1:8001 BMS_SA_RESET=<script that empties the database and restarts it>
//     BMS_SA_STATE=<the state file> node tests/standalone.mjs [screenshot-dir]
// The Pi's own screen is this machine (a kiosk); "the laptop" is a browser whose address is given
// with X-Forwarded-For (as through a local proxy).
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const url = process.env.BMS_URL ?? "http://127.0.0.1:8001";
const shots = process.argv[2];
const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "PASS" : "FAIL"} ${message}`);
  if (!ok) failures.push(message);
};
const shot = async (page, name) => shots && (await page.screenshot({ path: `${shots}/${name}.png` }));
const PASSWORD = "installer password 1";

const browser = await chromium.launch();
let kiosk;
try {
  // ---------- first boot: the Pi's screen ----------
  const pi = await browser.newContext({ viewport: { width: 1024, height: 600 } });
  kiosk = await pi.newPage();
  const errors = [];
  kiosk.on("pageerror", (e) => errors.push(e.message));
  await kiosk.goto(`${url}/hmi`);
  await kiosk.getByRole("heading", { name: "Set up this BMS" }).waitFor({ timeout: 20000 });
  const code = (await kiosk.getByLabel("Setup code").textContent()).trim();
  check(/^\d{6}$/.test(code), `the Pi's screen shows a 6-digit setup code (${code})`);
  check(await kiosk.getByRole("img", { name: /QR code/ }).count() === 1, "and a QR code to the web app");
  await shot(kiosk, "standalone-setup");

  // ---------- the installer's laptop ----------
  const laptop = await browser.newContext({ viewport: { width: 1280, height: 800 }, extraHTTPHeaders: { "X-Forwarded-For": "192.168.50.20" } });
  const page = await laptop.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept());
  await page.goto(url);
  await page.getByRole("heading", { name: "Create the first admin" }).waitFor();
  const fill = async (c) => {
    await page.getByLabel("Setup code (on the Pi's screen)").fill(c);
    await page.getByLabel("Your name").fill("Installer");
    await page.getByLabel("User name").fill("installer");
    await page.getByLabel("Password (at least 10 characters)").fill(PASSWORD);
    await page.getByLabel("Password again").fill(PASSWORD);
    await page.getByRole("button", { name: "Create the admin account" }).click();
  };
  await fill(code === "000000" ? "111111" : "000000");
  await page.getByText(/setup code is not right/).waitFor();
  check(true, "a wrong setup code is refused");
  await fill(code);
  await page.getByRole("button", { name: "Sign out", exact: true }).waitFor();
  check(true, "with the code from the Pi's screen the installer creates the first admin from a laptop");

  // The Pi's screen moves on to the standalone HMI by itself.
  await kiosk.getByRole("navigation", { name: "HMI screens" }).waitFor({ timeout: 20000 });
  const tabs = await kiosk.getByRole("navigation", { name: "HMI screens" }).getByRole("button").allTextContents();
  check(tabs.join(",") === "Graphics,Overrides,Schedules,Log", `the standalone bottom bar (${tabs.join(", ")})`);
  check(await kiosk.getByText("Standalone", { exact: true }).isVisible(), "the status strip says Standalone");
  check(await kiosk.getByText(/^USB: not yet$|^USB /).first().isVisible(), "and shows the USB export status");

  // Set-up for the screens: a setpoint, a PIN, a hand mode.
  const api = page.request;
  const ok = async (r) => { if (!r.ok()) throw new Error(`${r.url()}: ${await r.text()}`); return r.json(); };
  await ok(await api.put(`${url}/api/setup/points/plant_meter2_limit`, { data: {
    name: "plant_meter2_limit", dis: "Demand limit", equip: "meter2", device: "rtu_meter", type: "sp", kind: "Number",
    unit: "kW", pollClass: "fast", tags: { elec: true, power: true }, writeLimits: { min: 10, max: 200 },
    address: { objectType: "holdingRegister", address: 20, dataType: "float32" } } }));
  await ok(await api.post(`${url}/api/auth/me/pin`, { data: { pin: "4826", password: PASSWORD } }));
  const points = await (await api.get(`${url}/api/points`)).json();
  const limit = points.find((p) => p.name === "plant_meter2_limit");
  await ok(await api.post(`${url}/api/points/${limit.id}/command`, { data: { value: 150, mode: "hand", hours: 1, confirm: true } }));

  // ---------- Overrides: hand modes with Back to Auto (PIN), and setpoints ----------
  await kiosk.reload();
  await kiosk.getByRole("button", { name: "Overrides" }).click();
  const hand = kiosk.getByRole("region", { name: "Hand modes" }).getByRole("listitem").filter({ hasText: "Demand limit" });
  await hand.waitFor({ timeout: 10000 });
  check(await hand.getByText(/min left/).isVisible(), "Overrides lists the hand mode with the time left");
  await shot(kiosk, "standalone-overrides");
  await hand.getByRole("button", { name: "Back to Auto" }).click();
  await kiosk.getByRole("button", { name: /Installer/ }).click();
  for (const d of "4826") await kiosk.getByRole("button", { name: d, exact: true }).click();
  await kiosk.getByRole("button", { name: "Unlock" }).click();
  await kiosk.getByRole("region", { name: "Hand modes" }).waitFor();
  await kiosk.getByRole("region", { name: "Hand modes" }).getByRole("button", { name: "Back to Auto" }).click();
  await kiosk.getByText("Nothing is in hand mode. Everything runs in Auto.").waitFor({ timeout: 10000 });
  check(true, "Back to Auto (after the PIN) ends the hand mode");

  // ---------- Log: chart, Export now, Download CSV ----------
  await kiosk.getByRole("button", { name: "Log" }).click();
  await kiosk.getByRole("button", { name: "Export now" }).click();
  await kiosk.getByText(/Exported \d+ minutes to the USB stick|Export failed/).waitFor({ timeout: 10000 });
  check(await kiosk.getByText(/Exported \d+ minutes to the USB stick/).isVisible(), "Export now writes to the USB stick");
  check(await kiosk.getByRole("link", { name: "Download CSV" }).isVisible(), "Download CSV is offered");
  const csv = await (await api.get(`${url}/api/standalone/log.csv?hours=1`)).text();
  check(csv.startsWith("time,"), "the CSV has a time column then one column a point");
  await shot(kiosk, "standalone-log");

  // ---------- Setup → Standalone on the laptop ----------
  await page.goto(`${url}/#/setup/standalone`);
  await page.getByRole("heading", { name: "Standalone" }).waitFor();
  await page.getByText(/points \(\d+ logged\)/).waitFor();
  check(true, "the page shows the points against the standalone limit");
  await page.getByLabel("Export every (minutes)").fill("30");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Saved.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Save now" }).click();
  await page.getByText("Saved to the card.").waitFor();
  check(true, "settings saved, and the state file saved on request");
  check(await page.getByLabel("Address", { exact: true }).isVisible(), "network settings use the Pi's helper");
  await shot(page, "standalone-setup-page");
  const delivery = await (await api.get(`${url}/api/alarm-settings`)).json();
  check(Object.values(delivery.settings.channels).every((c) => !c.includes("sms")), "no SMS in standalone alarms");

  // ---------- a power cut ----------
  if (process.env.BMS_SA_RESET && process.env.BMS_SA_STATE) {
    const state = JSON.parse(readFileSync(process.env.BMS_SA_STATE, "utf8"));
    check(state.tables.app_user.some((u) => u.username === "installer") && state.standalone.usbIntervalMin === 30,
          "the state file on the card has the account and the settings");
    execFileSync("bash", [process.env.BMS_SA_RESET], { stdio: "ignore" });
    const after = await browser.newContext({ extraHTTPHeaders: { "X-Forwarded-For": "192.168.50.20" } });
    const p2 = await after.newPage();
    await p2.goto(url);
    await p2.getByLabel("User name").fill("installer");
    await p2.getByLabel("Password", { exact: true }).fill(PASSWORD);
    await p2.getByRole("button", { name: "Log in" }).click();
    await p2.getByRole("button", { name: "Sign out", exact: true }).waitFor({ timeout: 15000 });
    const st = await (await p2.request.get(`${url}/api/standalone/status`)).json();
    const pts = await (await p2.request.get(`${url}/api/points`)).json();
    check(st.settings.usbIntervalMin === 30 && pts.some((p) => p.name === "plant_meter2_limit"),
          "after a power cut the site comes back from the state file (account, point, settings)");
    check(!pts.find((p) => p.name === "plant_meter2_limit").override, "and nothing is in hand mode");
    await after.close();
  } else {
    console.log("(power cut not tested: set BMS_SA_RESET and BMS_SA_STATE)");
  }
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && kiosk) await kiosk.screenshot({ path: `${shots}/standalone-failure.png` }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
