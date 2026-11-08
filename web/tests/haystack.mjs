// Browser test of the Haystack API settings (milestone 12a-Haystack): an API client account,
// the Security switch, the model export, and the API answering only while switched on.
//   BMS_URL=http://127.0.0.1:8000 node tests/haystack.mjs [screenshot-dir]
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
const API_PASSWORD = "api client password 1";

const browser = await chromium.launch();
let page;
try {
  page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept());
  await signIn(page, url);
  const api = page.request;

  // ---------- an API client account ----------
  await page.goto(`${url}/#/setup/users`);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Analytics platform");
  await page.getByLabel("User name", { exact: true }).fill("analytics");
  await page.getByLabel("Haystack API client (a program, not a person)").check();
  check(await page.getByLabel("Role").count() === 0, "an API client has no role to choose (viewer)");
  check(await page.getByText("Platform admin account").count() === 0, "and cannot be a platform account");
  await page.getByLabel("Password for the program").fill(API_PASSWORD);
  await page.getByRole("button", { name: "Add account" }).click();
  await page.getByText("Added analytics.").waitFor();
  const row = page.getByRole("region", { name: "Users" }).getByRole("button", { name: /Analytics platform/ });
  check(await row.getByText("API client").isVisible(), "the users list marks it as an API client");
  if (shots) await page.screenshot({ path: `${shots}/haystack-users.png` });

  // ---------- off by default; Security switches it on ----------
  check((await api.get(`${url}/api/haystack/about`)).status() === 404, "the Haystack API is off by default");
  await page.goto(`${url}/#/setup/security`);
  await page.getByRole("heading", { name: "Haystack API" }).waitFor();
  check(await page.getByText(`${url}/api/haystack`).isVisible(), "Security shows the API's address");
  await page.getByLabel("Haystack API on (read only)").check();
  await page.getByRole("button", { name: "Save security settings" }).click();
  await page.getByText("Saved.").waitFor();
  const about = await api.get(`${url}/api/haystack/about`, { headers: { Cookie: "" } });
  check(about.status() === 401 && about.headers()["www-authenticate"] === "HELLO",
        "switched on, it asks for Haystack authentication (a web session is not enough)");
  if (shots) await page.screenshot({ path: `${shots}/haystack-security.png` });

  // An API client cannot use the web app.
  const other = await (await browser.newContext()).newPage();
  await other.goto(url);
  await other.getByLabel("User name").fill("analytics");
  await other.getByLabel("Password", { exact: true }).fill(API_PASSWORD);
  await other.getByRole("button", { name: "Log in" }).click();
  await other.getByText("This is an API client account: it can only use the Haystack API.").waitFor();
  check(true, "an API client cannot log in to the web app");

  // ---------- the model export ----------
  await page.goto(`${url}/#/setup/site`);
  const link = page.getByRole("link", { name: "Export Haystack JSON" });
  await link.waitFor();
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  check(/-haystack\.json$/.test(download.suggestedFilename()), `the export downloads (${download.suggestedFilename()})`);
  const model = await (await api.get(`${url}/api/haystack-export?format=json`)).json();
  check(model._kind === "grid" && model.rows.some((r) => r.site) && model.rows.some((r) => r.point),
        "the export is a Haystack JSON grid with the site and its points");
  const zinc = await (await api.get(`${url}/api/haystack-export?format=zinc`)).text();
  check(zinc.startsWith('ver:"3.0"'), "and the Zinc export");

  // Off again.
  await page.goto(`${url}/#/setup/security`);
  await page.getByLabel("Haystack API on (read only)").uncheck();
  await page.getByRole("button", { name: "Save security settings" }).click();
  await page.getByText("Saved.").waitFor();
  check((await api.get(`${url}/api/haystack/about`)).status() === 404, "switched off, the API answers 404 again");
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && page) await page.screenshot({ path: `${shots}/haystack-failure.png`, fullPage: true }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
