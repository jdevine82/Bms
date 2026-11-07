// Browser test of milestone 10C: a template's logic in role mode, published as a template version
// and rolled out to an instance (read-only, running); the equipment panel; a variable published
// as a point; snippets.
//   BMS_URL=http://127.0.0.1:8000 node tests/logic-templates.mjs [screenshot-dir]
// Uses config/site.example-setup.json (meter2 and register 20 of the RS485 meter as a setpoint).
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
const shot = async (page, name) => shots && (await page.screenshot({ path: `${shots}/${name}.png` }));

const browser = await chromium.launch();
let page;
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(d.type() === "prompt" ? "Limit follower" : undefined));
  await signIn(page, url);
  const api = page.request;
  const ok = async (r) => { if (!r.ok()) throw new Error(`${r.url()}: ${await r.text()}`); return r.json(); };

  // Set-up: the demand limit point, a site variable, and a site template for meter2.
  await ok(await api.put(`${url}/api/setup/points/plant_meter2_limit`, { data: {
    name: "plant_meter2_limit", dis: "Demand limit", equip: "meter2", device: "rtu_meter", type: "sp", kind: "Number",
    unit: "kW", pollClass: "fast", tags: { elec: true, power: true }, writeLimits: { min: 10, max: 200 },
    address: { objectType: "holdingRegister", address: 20, dataType: "float32" } } }));
  await ok(await api.put(`${url}/api/logic-variables/demand_limit`, { data: { type: "number", kind: "constant", value: 120,
                                                                            min: 50, max: 180, unit: "kW" } }));
  const existing = await (await api.get(`${url}/api/setup/equip-templates`)).json();
  if (!existing.some((t) => t.id === "limit-test")) {
    await ok(await api.post(`${url}/api/setup/equip-templates`, { data: { id: "limit-test", version: "1.0", dis: "Limit test meter",
      tags: { elec: true, meter: true }, roles: [{ role: "limit", dis: "Demand limit", type: "sp", kind: "Number", unit: "kW",
        tags: { elec: true, power: true }, writeLimits: { min: 10, max: 200 } }] } }));
  }
  const equip = await (await api.get(`${url}/api/setup/equips/meter2`)).json();
  equip.template = { id: "limit-test", version: "1.0", bindings: { limit: "plant_meter2_limit" } };
  await ok(await api.put(`${url}/api/setup/equips/meter2`, { data: equip }));

  // ---------- a template's logic, in role mode ----------
  await page.goto(`${url}/#/setup/equip-templates`);
  await page.getByRole("button", { name: /Limit test meter/ }).click();
  await page.locator(".pane-head").getByRole("button", { name: /^Logic/ }).click();
  await page.getByText("This is the logic of the equipment template").waitFor();
  check(page.url().includes("#/logic/tpl_limit_test"), "Logic on a template opens the editor in role mode");
  const node = (id) => page.locator(`.react-flow__node[data-id="${id}"]`);
  const props = page.getByRole("complementary", { name: "Properties" });
  await page.getByRole("button", { name: "Add Variable read" }).click();
  await props.getByLabel("Variable", { exact: true }).selectOption("demand_limit");
  await page.getByRole("button", { name: "Add Point write" }).click();
  check(await props.getByLabel("Role").locator("option", { hasText: "limit" }).count() === 1, "a point block picks a role, not a point");
  await props.getByLabel("Role").selectOption("limit");
  await props.getByLabel("Fail-safe", { exact: true }).selectOption("hold");
  const from = await node("variable_read_1").locator('.react-flow__handle-right[data-handleid="out"]').boundingBox();
  const to = await node("point_write_1").locator('.react-flow__handle-left[data-handleid="in"]').boundingBox();
  await page.mouse.move(from.x + 5, from.y + 5);
  await page.mouse.down();
  await page.mouse.move(to.x + 5, to.y + 5, { steps: 8 });
  await page.mouse.up();
  await page.getByText("Draft saved").waitFor({ timeout: 10000 });
  await page.getByRole("button", { name: "Publish…" }).click();
  const dlg = page.getByRole("dialog", { name: "Publish" });
  await dlg.getByText("limit: hold the last value").waitFor();
  await dlg.getByLabel("I confirm these fail-safes").check();
  await dlg.getByRole("button", { name: "Publish" }).click();
  await page.getByText(/Saved as template limit-test version 1\.1/).waitFor();
  check(true, "publishing a template's logic saves a new template version");
  await page.getByText("No problems.").waitFor({ timeout: 10000 });
  check(true, "the template's logic is checked against its roles (no problems)");
  await shot(page, "logic-template");

  // Roll out to the instance: it runs, read-only, with its own switch.
  await ok(await api.post(`${url}/api/setup/equip-templates/limit-test/rollout`, { data: { version: "1.1", all: true } }));
  await page.goto(`${url}/#/logic`);
  await page.reload();
  const row = page.getByRole("row", { name: /meter2__tpl/ });
  await row.getByText("Running").waitFor({ timeout: 10000 });
  check(await row.getByText(/Template limit-test 1\.1 on meter2/).isVisible(), "the instance is listed as template logic, running");
  await row.getByRole("button", { name: "Live" }).first().click().catch(async () => { await row.getByRole("button", { name: "Open" }).click(); });
  await page.getByRole("button", { name: "Live", exact: true }).waitFor();
  check((await page.getByRole("button", { name: "Edit", exact: true }).count()) === 0, "an instance has no Edit mode");
  await node("point_write_1").getByText(/Written 120 kW/).waitFor({ timeout: 15000 });
  check(true, "the instance writes the variable to its bound point (120 kW)");
  await shot(page, "logic-instance");

  // ---------- the equipment panel ----------
  await page.goto(`${url}/#/setup/equipment`);
  await page.getByRole("button", { name: /Sub meter|meter2/ }).first().click();
  const panel = page.getByLabel("Logic", { exact: true });
  await panel.getByText("BMS-controlled").waitFor();
  check(await panel.getByRole("link", { name: "meter2__tpl" }).isVisible(), "the equipment panel lists the diagram that writes it");

  // ---------- a variable published as a point ----------
  await page.goto(`${url}/#/logic`);
  await page.getByRole("row", { name: /demand_limit/ }).getByRole("button", { name: "Publish as point" }).click();
  await page.getByLabel("Point name for demand_limit").fill("plant_meter2_demand_sp");
  await page.getByLabel("Equipment", { exact: true }).selectOption("meter2");
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await page.getByText("Published demand_limit as the point plant_meter2_demand_sp.").waitFor();
  const points = await (await api.get(`${url}/api/points`)).json();
  const sp = points.find((p) => p.name === "plant_meter2_demand_sp");
  check(sp && sp.writable && sp.writeLimits?.max === 180, "the point is a writable setpoint with the variable's limits");
  // Changing it from the Points page sets the variable, and the instance writes the new value.
  await page.goto(`${url}/`);
  await page.reload();
  const prow = page.locator("tr", { has: page.getByTestId("value-plant_meter2_demand_sp") });
  await prow.getByRole("button", { name: "Command" }).click();
  const cmd = page.getByRole("dialog", { name: /Command/ });
  await cmd.getByLabel("New value").fill("150");
  await cmd.getByRole("button", { name: "Set", exact: true }).click();
  await cmd.getByText(/Done|Sent/).waitFor({ timeout: 10000 });
  const vars = await (await api.get(`${url}/api/logic-variables`)).json();
  check(vars.find((v) => v.name === "demand_limit")?.value === 150, "a command to the point sets the variable");
  await page.getByTestId("value-plant_meter2_limit").getByText(/^150/).waitFor({ timeout: 15000 });
  check(true, "the template logic writes the new value to meter2");

  // ---------- snippets ----------
  await api.delete(`${url}/api/logic/snippet_test`);
  await page.goto(`${url}/#/logic`);
  await page.getByRole("button", { name: "New diagram" }).click();
  await page.getByLabel("Name", { exact: true }).fill("snippet_test");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("button", { name: "Add Delay on" }).click();
  await page.getByRole("button", { name: "Add NOT" }).click();
  await page.locator(".logic-canvas").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+a");
  await page.getByRole("button", { name: "Save as snippet" }).click();
  await page.getByText("Saved the snippet Limit follower; it is in the palette.").waitFor();
  await page.getByRole("button", { name: "Insert snippet Limit follower" }).click();
  await node("delay_on_2").waitFor();
  check(await node("not_2").count() === 1, "a saved snippet inserts its blocks (renumbered)");
  const config = await (await api.get(`${url}/api/setup/config`)).json();
  check(config.logicSnippets?.some((s) => s.dis === "Limit follower"), "snippets are part of the setup file");

  // Clean up: logic must not keep writing register 20 for later tests.
  await api.post(`${url}/api/logic/meter2__tpl/enable`, { data: { enabled: false } });
  await api.delete(`${url}/api/logic/snippet_test`);
  await api.delete(`${url}/api/logic-snippets/limit_follower`);
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && page) await page.screenshot({ path: `${shots}/logic-templates-failure.png` }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
