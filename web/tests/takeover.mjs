// Browser test of HMI takeover and failback (milestone 11), with a site server and a Pi HMI in
// standby on two ports:
//   BMS_URL=http://127.0.0.1:8003 BMS_PI_URL=http://127.0.0.1:8004
//     BMS_SERVER_CTL=<script: start | stop the server> BMS_PI_START=<script: start the Pi in standby>
//     node tests/takeover.mjs [screenshot-dir]
// The server starts empty (config/site.example-setup.json as its config file) with
// BMS_HMI_URLS=<the Pi> and the shared BMS_STANDBY_TOKEN; the Pi uses short times
// (BMS_TAKEOVER_AFTER_S=4, BMS_FAILBACK_AFTER_S=4, BMS_HEARTBEAT_S=1).
// The kiosk is this machine; "the laptop" is a browser whose address is given with X-Forwarded-For.
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const url = process.env.BMS_URL ?? "http://127.0.0.1:8003";
const pi = process.env.BMS_PI_URL ?? "http://127.0.0.1:8004";
const serverCtl = process.env.BMS_SERVER_CTL;
const piStart = process.env.BMS_PI_START;
const shots = process.argv[2];
const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "PASS" : "FAIL"} ${message}`);
  if (!ok) failures.push(message);
};
const shot = async (page, name) => shots && (await page.screenshot({ path: `${shots}/${name}.png` }));
const run = (script, ...args) => execFileSync("bash", [script, ...args], { stdio: "ignore" });
const PASSWORD = "installer password 1";

if (!serverCtl || !piStart) {
  console.log("set BMS_SERVER_CTL and BMS_PI_START");
  process.exit(2);
}

const browser = await chromium.launch();
let kiosk;
try {
  // ---------- the server: an admin with a PIN; this machine is a kiosk ----------
  const laptop = await browser.newContext({ viewport: { width: 1280, height: 800 }, extraHTTPHeaders: { "X-Forwarded-For": "192.168.10.20" } });
  const page = await laptop.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = page.request;
  const ok = async (r) => { if (!r.ok()) throw new Error(`${r.url()}: ${await r.text()}`); return r.json(); };
  const local = await browser.newContext(); // the first admin is made on the server itself
  await ok(await local.request.post(`${url}/api/auth/first-admin`, { data: { username: "installer", dis: "Installer", password: PASSWORD } }));
  await local.close();
  await ok(await api.post(`${url}/api/auth/login`, { data: { username: "installer", password: PASSWORD } }));
  await ok(await api.post(`${url}/api/auth/me/pin`, { data: { pin: "4826", password: PASSWORD } }));
  await ok(await api.put(`${url}/api/auth/settings`, { data: { kioskAddresses: ["127.0.0.1"] } }));
  await ok(await api.put(`${url}/api/setup/points/plant_meter2_limit`, { data: {
    name: "plant_meter2_limit", dis: "Demand limit", equip: "meter2", device: "rtu_meter", type: "sp", kind: "Number",
    unit: "kW", pollClass: "fast", tags: { elec: true, power: true }, writeLimits: { min: 10, max: 200 },
    address: { objectType: "holdingRegister", address: 20, dataType: "float32" } } }));

  // ---------- the Pi starts in standby and takes the site from the server ----------
  run(piStart);
  const state = async () => (await (await page.request.get(`${pi}/api/standby/state`)).json());
  let st = await state();
  check(st.mode === "standby" && !!st.cacheRevision, `the Pi is in standby with the server's setup (${st.cacheRevision})`);

  const pane = await browser.newContext({ viewport: { width: 1024, height: 600 } });
  kiosk = await pane.newPage();
  kiosk.on("pageerror", (e) => errors.push(e.message));
  await kiosk.goto(`${url}/hmi?standby=${encodeURIComponent(pi)}`);
  await kiosk.getByRole("navigation", { name: "HMI screens" }).waitFor({ timeout: 15000 });
  check(true, "the kiosk shows the server's HMI");

  // A Pi page opened while the server is fine sends the screen back to the server.
  const stray = await pane.newPage();
  await stray.goto(`${pi}/hmi`);
  await stray.waitForURL((u) => u.href.startsWith(`${url}/hmi?standby=`), { timeout: 15000 });
  check(true, "the Pi's HMI in standby returns to the server's HMI");
  await stray.close();

  // ---------- the server stops: the Pi takes over ----------
  run(serverCtl, "stop");
  await kiosk.waitForURL((u) => u.href.startsWith(`${pi}/hmi`), { timeout: 30000 });
  check(true, "with the server down the kiosk moves to the Pi's HMI");
  await kiosk.getByText(/Direct mode: the site server is down/).waitFor({ timeout: 15000 });
  check(await kiosk.getByText("Direct mode", { exact: true }).isVisible(), "the status strip says Direct mode");
  const tabs = await kiosk.getByRole("navigation", { name: "HMI screens" }).getByRole("button").allTextContents();
  check(tabs.join(",") === "Overview,Graphics,Setpoints,Overrides,Server", `the direct-mode bottom bar (${tabs.join(", ")})`);
  await kiosk.getByText(/Direct mode: the HMI hmi-plant has taken over/).first().waitFor({ timeout: 10000 });
  check(true, "a Critical alarm says the HMI has taken over");
  await shot(kiosk, "takeover-direct");

  // A hand mode on the Pi (its own API: the PIN unlock, as on the screen).
  const pinApi = await browser.newContext();
  const r = await pinApi.request.post(`${pi}/api/auth/pin`, { data: { username: "installer", pin: "4826" } });
  check(r.ok(), "the server's PIN works on the Pi");
  const pts = await (await pinApi.request.get(`${pi}/api/points`)).json();
  const limit = pts.find((p) => p.name === "plant_meter2_limit");
  for (let i = 0; i < 50; i += 1) {
    const one = await (await pinApi.request.get(`${pi}/api/points`)).json();
    if (one.find((p) => p.id === limit.id).cur.status === "ok") break;
    await new Promise((res) => setTimeout(res, 200));
  }
  const cmd = await pinApi.request.post(`${pi}/api/points/${limit.id}/command`, { data: { value: 150, mode: "hand", hours: 1, confirm: true } });
  check(cmd.ok(), `the Pi commands in direct mode (${cmd.status()})`);
  await pinApi.close();

  await kiosk.getByRole("button", { name: "Server", exact: true }).click();
  await kiosk.getByText("The server is not answering").waitFor();
  check(await kiosk.getByRole("button", { name: "Hand control back" }).isDisabled(), "Hand control back waits for the server");

  // ---------- the server comes back, held by the HMI ----------
  run(serverCtl, "start");
  await page.goto(url);
  await page.getByRole("button", { name: "Sign out", exact: true }).waitFor({ timeout: 15000 });
  await page.getByRole("alert").filter({ hasText: /The HMI hmi-plant is in direct mode.*This server is not polling/ }).waitFor({ timeout: 15000 });
  check(true, "the web app says the server is not polling while the HMI holds the site");
  await shot(page, "takeover-web-held");
  await kiosk.getByText(/Control can be handed back/).waitFor({ timeout: 20000 });
  await shot(kiosk, "takeover-server-back");
  await kiosk.getByRole("button", { name: "Hand control back" }).click();
  await kiosk.getByRole("button", { name: /Installer/ }).click();
  for (const d of "4826") await kiosk.getByRole("button", { name: d, exact: true }).click();
  await kiosk.getByRole("button", { name: "Unlock" }).click();
  await kiosk.getByRole("button", { name: "Hand control back" }).click();
  await kiosk.waitForURL((u) => u.href.startsWith(`${url}/hmi?standby=`), { timeout: 20000 });
  check(true, "after Hand control back (PIN) the kiosk returns to the server's HMI");
  st = await state();
  check(st.mode === "standby" && st.auditPending === 0, "the Pi is in standby again and has sent its log");

  await page.getByRole("alert").filter({ hasText: /This server is not polling/ }).waitFor({ state: "detached", timeout: 20000 });
  check(true, "the server polls again (the banner goes)");
  const audit = await (await api.get(`${url}/api/audit?limit=50`)).json();
  const rows = Array.isArray(audit) ? audit : audit.entries ?? audit.rows ?? [];
  check(rows.some((e) => /\[HMI hmi-plant, direct mode\].*took over/.test(e.summary)) &&
        rows.some((e) => /\[HMI hmi-plant, direct mode\].*Demand limit|plant_meter2_limit/.test(e.summary)),
        "the server's audit log has the HMI's direct-mode entries");
  const after = (await (await api.get(`${url}/api/points`)).json()).find((p) => p.name === "plant_meter2_limit");
  check(!after.override, "the Pi's hand mode was released at failback");
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && kiosk) await kiosk.screenshot({ path: `${shots}/takeover-failure.png` }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
