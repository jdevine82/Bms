// Browser test of Setup → Updates (milestone 12b) on a site server whose updater agent is the
// stand-in backend/tests/fake_updater.py (the real agent with pct replaced; version 0.9.9 fails).
//   BMS_URL=http://127.0.0.1:8005 node tests/updates.mjs [screenshot-dir]
// The server: BMS_UPDATER_URL / BMS_UPDATER_TOKEN for the agent, BMS_HMI_URLS for one HMI that does not answer.
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const url = process.env.BMS_URL ?? "http://127.0.0.1:8005";
const shots = process.argv[2];
const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "PASS" : "FAIL"} ${message}`);
  if (!ok) failures.push(message);
};
const PASSWORD = "installer password 1";

function makeRelease(version) {
  const dir = mkdtempSync(join(tmpdir(), "rel-"));
  const top = join(dir, `devine-bms-${version}`);
  for (const d of ["backend", "web/dist", "deploy/containers"]) mkdirSync(join(top, d), { recursive: true });
  writeFileSync(join(top, "VERSION"), `${version}\n`);
  writeFileSync(join(top, "backend/pyproject.toml"), "[project]\n");
  writeFileSync(join(top, "web/dist/index.html"), "<html>");
  writeFileSync(join(top, "deploy/containers/install-release.sh"), "#!/bin/bash\n");
  const file = join(dir, `devine-bms-${version}.tar.gz`);
  execFileSync("tar", ["-czf", file, "-C", dir, `devine-bms-${version}`]);
  return file;
}

const browser = await chromium.launch();
let page;
try {
  const local = await browser.newContext();
  const r = await local.request.post(`${url}/api/auth/first-admin`, { data: { username: "installer", dis: "Installer", password: PASSWORD } });
  check(r.ok(), "first admin");
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept());
  await page.goto(url);
  await page.getByLabel("User name").fill("installer");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).waitFor();

  await page.goto(`${url}/#/setup/updates`);
  await page.getByRole("heading", { name: "Updates" }).waitFor();
  check((await page.getByTestId("up-version").textContent()) === "0.1.0", "the page shows the version running");
  check(await page.getByText(/snapshots both containers/).isVisible(), "and explains the snapshot and rollback");
  check(await page.getByRole("button", { name: "Install" }).isDisabled(), "Install waits for a file");

  // A file that is not a release is refused with the reason.
  const junk = join(mkdtempSync(join(tmpdir(), "junk-")), "notes.tar.gz");
  writeFileSync(junk, "hello");
  await page.getByLabel("Release file").setInputFiles(junk);
  await page.getByRole("button", { name: "Install" }).click();
  await page.getByText(/not a .tar.gz release/).waitFor();
  check(true, "a file that is not a release is refused");

  // A good release: progress, then Passed, then the push to the HMI.
  await page.getByLabel("Release file").setInputFiles(makeRelease("0.2.0"));
  await page.getByRole("button", { name: "Install" }).click();
  await page.getByText("Updating from 0.1.0 to 0.2.0…").waitFor();
  const progress = page.getByLabel("Update progress");
  await progress.getByText("Passed: the new version is running.").waitFor({ timeout: 30000 });
  check(await progress.getByText(/snapshots bms-pre-0_2_0-/).isVisible(), "the steps show the snapshots");
  if (shots) await page.screenshot({ path: `${shots}/updates-passed.png`, fullPage: true });
  const hmis = page.getByRole("table", { name: "Standby HMIs" });
  await hmis.getByText(/0\.2\.0: not reachable/).waitFor({ timeout: 20000 });
  check(true, "the release is sent to the standby HMIs (this one does not answer)");
  const history = page.getByRole("table", { name: "Audit log" });
  await history.getByText("update to 0.2.0 passed its health check (from 0.1.0)").waitFor({ timeout: 15000 });
  check(await history.getByText("started the update from 0.1.0 to 0.2.0").isVisible(), "the history has the start and the result");

  // A release that fails its install: rolled back.
  await page.getByLabel("Release file").setInputFiles(makeRelease("0.9.9"));
  await page.getByRole("button", { name: "Install" }).click();
  await progress.getByText("Failed, and the site was put back as it was before the update.").waitFor({ timeout: 30000 });
  check(await progress.getByText(/pip failed/).first().isVisible(), "a failed update says why and that it was rolled back");
  await history.getByText(/update to 0\.9\.9 failed and both containers were rolled back/).waitFor({ timeout: 15000 });
  check(true, "and the history records the rollback");
  if (shots) await page.screenshot({ path: `${shots}/updates-rolled-back.png`, fullPage: true });
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && page) await page.screenshot({ path: `${shots}/updates-failure.png`, fullPage: true }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
