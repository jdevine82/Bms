// Browser test of the site's location on Setup -> Site, and device logins on the site's own MQTT
// broker (Setup -> Buses and networks). Against a backend loaded from config/site.example-mqtt.json
// (a broker at 127.0.0.1) with BMS_MQTT_USERS_CMD pointing at deploy/containers/bms-mqtt-users:
//   BMS_URL=http://127.0.0.1:8006 node tests/site-fixes.mjs [screenshot-dir]
import { createRequire } from "node:module";
import { signIn } from "./login.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const url = process.env.BMS_URL ?? "http://127.0.0.1:8006";
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
  const config = async () => (await (await api.get(`${url}/api/setup/config`)).json());

  // ---------- the site's location ----------
  await page.goto(`${url}/#/setup/site`);
  await page.getByLabel("Latitude").waitFor();
  await page.getByLabel("Latitude").fill("-33.8688");
  await page.getByLabel("Longitude").fill("");
  await page.getByRole("button", { name: "Save site settings" }).click();
  await page.getByText("give both the latitude and the longitude, or neither").waitFor();
  check(true, "one coordinate without the other is refused");
  await page.getByLabel("Longitude").fill("151.2093");
  await page.getByLabel("Floor area (m²)").fill("1250");
  await page.getByRole("button", { name: "Save site settings" }).click();
  await page.getByText("Saved and live.").waitFor();
  let site = (await config()).site;
  check(site.geoCoord?.lat === -33.8688 && site.geoCoord?.lng === 151.2093 && site.areaM2 === 1250,
        `the location and area are saved (${JSON.stringify(site.geoCoord)}, ${site.areaM2})`);
  await page.reload();
  check(await page.getByLabel("Latitude").inputValue() === "-33.8688", "and shown again after a reload");
  await page.getByLabel("Latitude").fill("95");
  await page.getByRole("button", { name: "Save site settings" }).click();
  await page.getByText(/less than or equal to 90|lat/i).first().waitFor();
  check(true, "a latitude outside -90 to 90 is refused by the setup checks");
  await page.reload();
  await page.getByLabel("Latitude").fill("");
  await page.getByLabel("Longitude").fill("");
  await page.getByRole("button", { name: "Save site settings" }).click();
  await page.getByText("Saved and live.").waitFor();
  site = (await config()).site;
  check(site.geoCoord == null, "clearing both removes the location");

  // ---------- device logins on the site broker ----------
  await page.goto(`${url}/#/setup/buses`);
  await page.getByRole("button", { name: /mqtt1/ }).first().click();
  const panel = page.getByRole("region", { name: "Device logins" });
  await panel.waitFor();
  check(await panel.getByText("No device logins yet.").isVisible(), "the site broker shows its device logins");
  await panel.getByLabel("New login name").fill("plug-kitchen");
  await panel.getByRole("button", { name: "Add login" }).click();
  const pw = panel.getByTestId("mqtt-password");
  await pw.waitFor();
  const first = (await pw.textContent()).trim();
  check(first.length >= 30, "adding a login shows its password once");
  if (shots) await page.screenshot({ path: `${shots}/mqtt-logins.png` });
  await panel.getByRole("button", { name: "Done" }).click();
  check(await pw.count() === 0 && await panel.getByText("plug-kitchen").isVisible(), "Done hides it; the login is listed");
  await panel.getByRole("button", { name: "New password" }).click();
  await pw.waitFor();
  check((await pw.textContent()).trim() !== first, "New password gives a different one");
  await panel.getByLabel("New login name").fill("plug-kitchen");
  await panel.getByRole("button", { name: "Add login" }).click();
  await panel.getByText(/already a login/).waitFor();
  check(true, "a second login with the same name is refused");
  await panel.getByRole("button", { name: "Remove" }).click();
  await panel.getByText("No device logins yet.").waitFor();
  check(true, "Remove takes the login away");
  const audit = await (await api.get(`${url}/api/audit?action=mqtt-login`)).json();
  check(audit.length === 3 && !JSON.stringify(audit).includes(first), "the audit log has the three changes and no password");
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && page) await page.screenshot({ path: `${shots}/site-fixes-failure.png`, fullPage: true }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
