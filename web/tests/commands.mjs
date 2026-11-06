// Browser test of commands (milestone 7A): set a setpoint from the Points page and see the device
// read it back, hand mode with its badge and release to auto, the write limits, and on the HMI
// a kiosk unlocking commands with a person's PIN.
//   BMS_URL=http://127.0.0.1:8000 node tests/commands.mjs [screenshot-dir]
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

const browser = await chromium.launch();
let page;
try {
  page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await signIn(page, url);
  const api = page.request;

  // A writable setpoint on the simulated RS485 meter (holding registers 20-21).
  const put = await api.put(`${url}/api/setup/points/plant_meter2_limit`, { data: {
    name: "plant_meter2_limit", dis: "Demand limit", equip: "meter2", device: "rtu_meter", type: "sp", kind: "Number",
    unit: "kW", pollClass: "fast", tags: { elec: true, power: true }, writeLimits: { min: 10, max: 200 },
    address: { objectType: "holdingRegister", address: 20, dataType: "float32" } } });
  check(put.ok(), "a writable setpoint is added with write limits");

  await page.goto(url);
  const row = page.locator("tr", { has: page.getByTestId("value-plant_meter2_limit") });
  await row.getByRole("button", { name: "Command" }).click();
  const panel = page.getByRole("dialog", { name: "Command Demand limit" });
  await panel.getByLabel("New value").fill("250");
  check(await panel.getByRole("button", { name: "Set", exact: true }).isDisabled(), "a value above the write limits cannot be sent");
  await panel.getByLabel("New value").fill("120");
  await panel.getByRole("button", { name: "Set", exact: true }).click();
  await panel.getByText("Done. The device now reads 120.").waitFor({ timeout: 10000 });
  check(true, "set 120 and the device reads it back");

  await panel.getByLabel("New value").fill("150");
  await panel.getByLabel("Hand mode time").selectOption("1");
  await panel.getByRole("button", { name: "Hand mode" }).click();
  await panel.getByText("Hand mode: Demand limit: 150 kW for 1 h, then back to auto.").waitFor();
  check(true, "hand mode asks for confirmation first");
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/Done. The device now reads 150/).waitFor({ timeout: 10000 });
  await row.getByText(/Hand: 150 kW until/).waitFor({ timeout: 5000 });
  check(true, "the Points page shows the hand-mode badge");
  await shot(page, "command-panel");
  await panel.getByRole("button", { name: "Release to auto" }).click();
  await panel.getByText("Released to auto.").waitFor({ timeout: 10000 });
  await page.getByTestId("value-plant_meter2_limit").getByText(/^120/).waitFor({ timeout: 10000 });
  check((await row.getByText(/Hand:/).count()) === 0, "release to auto writes back 120 and clears the badge");
  await panel.getByRole("button", { name: "Close" }).click();

  // ---------- an operator with a PIN, on a kiosk ----------
  await api.post(`${url}/api/users`, { data: { username: "pat", dis: "Pat", role: "operator", password: "pat's password 1" } });
  const op = await browser.newPage();
  await op.goto(url);
  await op.getByLabel("User name").fill("pat");
  await op.getByLabel("Password", { exact: true }).fill("pat's password 1");
  await op.getByRole("button", { name: "Log in" }).click();
  await op.goto(`${url}/#/account`);
  await op.getByLabel("New PIN").fill("4826");
  await op.getByLabel("Password for PIN").fill("pat's password 1");
  await op.getByRole("button", { name: "Set PIN" }).click();
  await op.getByText("HMI PIN set.").waitFor();
  check(true, "an operator sets an HMI PIN on My account");
  await api.put(`${url}/api/auth/settings`, { data: { kioskAddresses: ["127.0.0.1"] } });

  const hmiContext = await browser.newContext({ viewport: { width: 1024, height: 600 }, hasTouch: true });
  const hmi = await hmiContext.newPage();
  await hmi.goto(`${url}/hmi`);
  await hmi.getByRole("button", { name: "Setpoints" }).click();
  await hmi.getByText("View only").waitFor();
  check((await hmi.getByRole("button", { name: "Change" }).count()) === 0, "the kiosk is view only until unlocked");
  await hmi.getByRole("button", { name: "Unlock with PIN" }).click();
  await hmi.getByRole("button", { name: "Pat" }).click();
  for (const d of "0000") await hmi.getByRole("button", { name: d, exact: true }).click();
  await hmi.getByRole("button", { name: "Unlock" }).click();
  await hmi.getByText("Wrong PIN.").waitFor();
  for (const d of "4826") await hmi.getByRole("button", { name: d, exact: true }).click();
  await hmi.getByRole("button", { name: "Unlock" }).click();
  await hmi.getByText("Changes by Pat · locks after 5 minutes idle").waitFor();
  check(true, "the right PIN unlocks commands; a wrong one is refused");
  await hmi.locator(".hmi-sp-row", { hasText: "Demand limit" }).getByRole("button", { name: "Change" }).click();
  const big = hmi.getByRole("dialog", { name: "Command Demand limit" });
  await big.getByLabel("New value").fill("80");
  await big.getByRole("button", { name: "Set", exact: true }).click();
  await big.getByText("Done. The device now reads 80.").waitFor({ timeout: 10000 });
  check(true, "a setpoint changed on the HMI");
  await shot(hmi, "hmi-setpoints");
  await big.getByRole("button", { name: "Close" }).click();
  await hmi.getByRole("button", { name: "Lock now" }).click();
  await hmi.getByText("View only").waitFor();
  check(true, "Lock now returns the kiosk to view only");

  const audit = await (await api.get(`${url}/api/audit?action=command`)).json();
  check(audit[0].user === "pat" && audit[0].summary === "set plant_meter2_limit to 80", "the audit log names the person who unlocked the HMI");
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  failures.push(String(e));
  console.log(`FAIL ${e}`);
  if (shots && page) await page.screenshot({ path: `${shots}/failure.png`, fullPage: true });
} finally {
  try {
    await page?.request.put(`${url}/api/auth/settings`, { data: { kioskAddresses: [] } });
  } catch {
    /* the page may be gone */
  }
  await browser.close();
}
if (failures.length) {
  console.log(`${failures.length} failed`);
  process.exit(1);
}
console.log("all passed");
