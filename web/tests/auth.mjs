// Browser test of logins (milestone 6A): the login page, a wrong password, the live connection
// refused without a login, an admin adding an operator who cannot reach Setup, 2FA set-up and
// login with a code, the audit log, and signing out.
//   BMS_URL=http://127.0.0.1:8000 node tests/auth.mjs [screenshot-dir]
// Run after another test has created the test admin (or on a fresh database, opened locally).
import { createRequire } from "node:module";
import { createHmac } from "node:crypto";
import { signIn, TEST_PASSWORD, TEST_USER } from "./login.mjs";

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
const shot = async (page, name) => shots && (await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }));

function totp(secret, offset = 0) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offset));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, "0");
}

let page;
try {
  // ---------- not signed in ----------
  const anon = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await anon.goto(url);
  await anon.getByRole("button", { name: "Log in" }).waitFor();
  check(true, "a new browser gets the login page");
  const wsCode = await anon.evaluate(
    () =>
      new Promise((resolve) => {
        const ws = new WebSocket(`ws://${location.host}/api/live`);
        ws.onclose = (e) => resolve(e.code);
        setTimeout(() => resolve("open"), 5000);
      }),
  );
  check(wsCode === 4401, `the live connection is refused without a login (${wsCode})`);
  await anon.getByLabel("User name").fill(TEST_USER);
  await anon.getByLabel("Password", { exact: true }).fill("not the password");
  await anon.getByRole("button", { name: "Log in" }).click();
  await anon.getByText("Wrong user name or password.").waitFor();
  check(true, "a wrong password is refused");
  await shot(anon, "login");

  // ---------- admin adds an operator ----------
  page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await signIn(page, url);
  await page.getByText("Live", { exact: true }).waitFor({ timeout: 10000 });
  check(true, "signed in: the live connection works");
  await page.goto(`${url}/#/setup/users`);
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Olivia Operator");
  await page.getByLabel("User name").fill("olivia");
  await page.getByLabel("Role").selectOption("operator");
  await page.getByLabel("First password").fill("olivia's password");
  await page.getByRole("button", { name: "Add account" }).click();
  await page.getByText("Added olivia.").waitFor();
  check(true, "an admin adds an operator");
  await shot(page, "users");

  const op = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await op.goto(url);
  await op.getByLabel("User name").fill("olivia");
  await op.getByLabel("Password", { exact: true }).fill("olivia's password");
  await op.getByRole("button", { name: "Log in" }).click();
  await op.getByRole("button", { name: "Sign out", exact: true }).waitFor();
  check((await op.getByRole("button", { name: "Setup" }).count()) === 0, "an operator has no Setup button");
  await op.goto(`${url}/#/setup/devices`);
  check((await op.getByRole("navigation", { name: "Setup" }).count()) === 0, "an operator cannot open Setup by its address");

  // ---------- 2FA ----------
  await op.goto(`${url}/#/account`);
  await op.getByRole("button", { name: "Set up 2FA" }).click();
  const secret = (await op.locator("code.mono").textContent()).trim();
  check((await op.getByAltText("QR code for the authenticator app").count()) === 1, "2FA set-up shows a QR code");
  await op.getByLabel("Code from the app").fill(totp(secret));
  await op.getByRole("button", { name: "Turn on 2FA" }).click();
  await op.getByText("2FA is on.").waitFor();
  await shot(op, "account");
  await op.getByRole("button", { name: "Sign out", exact: true }).click();
  await op.getByLabel("User name").fill("olivia");
  await op.getByLabel("Password", { exact: true }).fill("olivia's password");
  await op.getByRole("button", { name: "Log in" }).click();
  const codeBox = op.getByLabel("Code from your authenticator app");
  await codeBox.waitFor();
  check(true, "with 2FA, the login asks for a code");
  await codeBox.fill(totp(secret));
  await op.getByRole("button", { name: "Log in" }).click();
  await op.getByRole("button", { name: "Sign out", exact: true }).waitFor();
  check(true, "logged in with the code");

  // ---------- audit log ----------
  await page.goto(`${url}/#/setup/audit`);
  await page.getByText("added operator account olivia").waitFor();
  check((await page.getByText("turned on 2FA").count()) >= 1, "the audit log has the account changes and 2FA");
  check((await page.getByText(/failed login/).count()) >= 1, "the audit log has the failed login");
  await shot(page, "audit");
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
