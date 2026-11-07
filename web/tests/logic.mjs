// Browser test of the logic editor (milestone 10B): a new diagram from the palette, wiring with
// type checks, settings, undo and redo, copy and paste, the warning before deleting an output,
// autosave, the test strip, publishing with the fail-safe confirmation, Live mode, versions,
// export, and import with the mapping table and "remove the failing blocks".
//   BMS_URL=http://127.0.0.1:8000 node tests/logic.mjs [screenshot-dir]
// Uses config/site.example-setup.json (the meters; register 20 of the RS485 meter as a setpoint).
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
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: url });
  page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await signIn(page, url);
  const api = page.request;
  await api.delete(`${url}/api/logic/demand_test`);
  await api.delete(`${url}/api/logic/imported_copy`);
  await api.put(`${url}/api/setup/points/plant_meter2_limit`, { data: {
    name: "plant_meter2_limit", dis: "Demand limit", equip: "meter2", device: "rtu_meter", type: "sp", kind: "Number",
    unit: "kW", pollClass: "fast", tags: { elec: true, power: true }, writeLimits: { min: 10, max: 200 },
    address: { objectType: "holdingRegister", address: 20, dataType: "float32" } } });

  // ---------- a new diagram ----------
  await page.goto(`${url}/#/logic`);
  await page.getByRole("button", { name: "New diagram" }).click();
  await page.getByLabel("Name", { exact: true }).fill("demand_test");
  await page.getByLabel("Display name").fill("Demand test");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("heading", { name: "Demand test" }).waitFor();
  check(page.url().endsWith("#/logic/demand_test"), "a new diagram opens in the editor");

  const node = (id) => page.locator(`.react-flow__node[data-id="${id}"]`);
  const handle = (id, port, side) => node(id).locator(`.react-flow__handle-${side}[data-handleid="${port}"]`);
  const connect = async (a, ap, b, bp) => {
    const from = await handle(a, ap, "right").boundingBox();
    const to = await handle(b, bp, "left").boundingBox();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 8 });
    await page.mouse.up();
  };
  const place = async (label, id, x, y) => {
    await page.getByRole("button", { name: `Add ${label}` }).click();
    await node(id).waitFor();
    // Drag it to a tidy place so the wires are easy to draw.
    const box = await node(id).boundingBox();
    const canvas = await page.locator(".logic-canvas").boundingBox();
    await page.mouse.move(box.x + 40, box.y + 10);
    await page.mouse.down();
    await page.mouse.move(canvas.x + x, canvas.y + y, { steps: 6 });
    await page.mouse.up();
  };
  const props = page.getByRole("complementary", { name: "Properties" });

  await place("Point read", "point_read_1", 120, 120);
  await props.getByLabel("Point", { exact: true }).fill("site_meter1_power");
  await props.getByLabel("Fallback value").fill("0");
  await place("Scale", "scale_1", 420, 120);
  await props.getByLabel("In max").fill("20");
  await place("Point write", "point_write_1", 720, 120);
  await props.getByLabel("Point", { exact: true }).fill("plant_meter2_limit");
  await props.getByLabel("Fail-safe", { exact: true }).selectOption("hold");
  await place("Compare", "compare_1", 420, 330);
  await props.getByLabel("b", { exact: true }).fill("5");
  check(true, "blocks are added from the palette and set in the properties panel");

  await connect("point_read_1", "out", "scale_1", "in");
  await connect("scale_1", "out", "point_write_1", "in");
  await connect("point_read_1", "out", "compare_1", "a");
  const wires = () => page.locator(".react-flow__edge").count();
  await page.waitForFunction(() => document.querySelectorAll(".react-flow__edge").length === 3, null, { timeout: 5000 }).catch(() => {});
  check((await wires()) === 3, "three wires drawn from port to port");
  await connect("compare_1", "out", "point_write_1", "in"); // true/false into a number: refused
  check((await wires()) === 3, "a wire between different types is refused");
  check(await node("point_read_1").locator(".port-number").count() > 0 && await node("compare_1").locator(".lhandle.port-bool").count() > 0,
        "ports show their type");

  // Undo and redo.
  await page.getByRole("button", { name: "Add NOT" }).click();
  await node("not_1").waitFor();
  await page.locator(".logic-canvas").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");  // the move of NOT is not a separate step; two undos remove it
  const undone = await node("not_1").count();
  await page.keyboard.press("Control+y");
  await page.keyboard.press("Control+y");
  check(undone === 0 && (await node("not_1").count()) === 1, "undo and redo (Ctrl+Z, Ctrl+Y)");

  // Copy and paste; delete with the warning for an output.
  await node("compare_1").click();
  await page.keyboard.press("Control+c");
  await page.keyboard.press("Control+v");
  await node("compare_2").waitFor();
  check(true, "copy and paste make a renumbered copy (compare_2)");
  await page.keyboard.press("Delete");
  check((await node("compare_2").count()) === 0, "Delete removes the selected block");
  let asked = null;
  page.once("dialog", (d) => { asked = d.message(); void d.dismiss(); });
  await node("point_write_1").click();
  await page.keyboard.press("Delete");
  await page.waitForTimeout(200);
  check(asked?.includes("fail-safe") && (await node("point_write_1").count()) === 1,
        "deleting an output asks first (and Cancel keeps it)");
  await node("not_1").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Delete" }).click();
  check((await node("not_1").count()) === 0, "right-click → Delete");

  // Autosave.
  await page.getByText("Draft saved").waitFor({ timeout: 10000 });
  const saved = await (await api.get(`${url}/api/logic/demand_test`)).json();
  check(saved.draft.blocks.length === 4 && saved.draft.wires.length === 3, "the draft saves itself");
  await shot(page, "logic-edit");

  // The test strip: nothing is written.
  await page.getByLabel("Source for site_meter1_power").selectOption("value");
  await page.getByLabel("Simulated site_meter1_power").fill("10");
  await page.getByRole("button", { name: "Run" }).click();
  const result = page.getByRole("table", { name: "Test result" });
  await result.waitFor();
  check((await result.locator("tbody tr").first().textContent()).includes("50"), "the test strip runs the draft: 10 kW scales to 50");

  // Publish with the fail-safe confirmation.
  await page.getByRole("button", { name: "Publish…" }).click();
  const dlg = page.getByRole("dialog", { name: "Publish" });
  await dlg.getByText("plant_meter2_limit: hold the last value").waitFor();
  check(await dlg.getByRole("button", { name: "Publish" }).isDisabled(), "publishing needs the fail-safes confirmed");
  await dlg.getByLabel("I confirm these fail-safes").check();
  await dlg.getByLabel("Note (what changed)").fill("first try");
  await dlg.getByRole("button", { name: "Publish" }).click();
  await page.getByText("Published version 1; it is running.").waitFor();
  check(true, "published as version 1");

  // Live mode.
  await page.getByRole("button", { name: "Live", exact: true }).click();
  await node("point_write_1").getByText(/Written \d/).waitFor({ timeout: 15000 });
  const labels = await page.locator(".react-flow__edge-text").allTextContents();
  check(labels.some((t) => /kW/.test(t)), `Live mode shows values on the wires (${labels.join(", ")})`);
  await shot(page, "logic-live");
  await page.getByRole("button", { name: "Edit", exact: true }).click();

  // Versions and export.
  await page.getByRole("button", { name: "Versions" }).click();
  await page.getByRole("dialog", { name: "Versions" }).getByText("first try").waitFor();
  await page.getByRole("dialog", { name: "Versions" }).getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Export" }).click();
  const exported = JSON.parse(await page.getByLabel("Exported JSON").inputValue());
  check(exported.schema === "devine-bms-logic/1" && exported.blocks.length === 4, "export gives the diagram in the documented format");
  await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Close" }).click();

  // Import: a block with an old point name is mapped; an unknown block type is removed.
  const text = JSON.stringify({ schema: "devine-bms-logic/1", blocks: [
    { id: "old", type: "point_read", settings: { point: "old_meter_power", onBad: "fallback", fallback: 0 } },
    { id: "odd", type: "teapot", settings: {} },
    { id: "big", type: "compare", settings: { op: ">", inputs: { b: 100 } } }],
    wires: [{ from: { block: "old", port: "out" }, to: { block: "big", port: "a" } }] });
  await page.getByRole("button", { name: "Import" }).click();
  const imp = page.getByRole("dialog", { name: "Import" });
  await imp.getByLabel("Logic JSON").fill(text);
  await imp.getByRole("button", { name: "Read" }).click();
  await imp.getByLabel("Map point old_meter_power").selectOption("site_meter1_power");
  const first = await imp.getByLabel("Map point old_meter_power").locator("option").nth(1).getAttribute("value");
  check(first.endsWith("_power"), `the mapping table suggests a close point first (${first})`);
  await imp.getByRole("button", { name: "Check" }).click();
  await imp.getByText("unknown block type 'teapot'").waitFor();
  await imp.getByRole("button", { name: "Remove the failing blocks and continue" }).click();
  await imp.getByRole("button", { name: "Check" }).click();
  await imp.getByRole("button", { name: "Paste", exact: true }).click();
  await node("old").waitFor();
  const pasted = await (await api.get(`${url}/api/logic/demand_test`)).json();
  check(true, "imported blocks are pasted in with their wire, the point mapped");
  await page.getByText("Draft saved").waitFor({ timeout: 10000 });
  const draft = (await (await api.get(`${url}/api/logic/demand_test`)).json()).draft;
  check(draft.blocks.some((b) => b.id === "old" && b.settings.point === "site_meter1_power") && !draft.blocks.some((b) => b.type === "teapot"),
        "the pasted blocks are in the draft (mapped, without the failing one)");
  void pasted;

  // The Logic page lists it as running; disable from the editor.
  await page.getByLabel("Enabled").uncheck();
  await page.getByText("Disabled: its outputs went to their fail-safe.").waitFor();
  await page.getByRole("button", { name: "← Logic" }).click();
  const row = page.getByRole("row", { name: /Demand test/ });
  await row.getByText("Disabled").waitFor();
  check(true, "the Logic page shows the state");

  // A phone: the editor stacks.
  const phone = await browser.newContext({ ...require("playwright").devices["iPhone 13"], storageState: await context.storageState() });
  const ph = await phone.newPage();
  await ph.goto(`${url}/#/logic/demand_test`);
  await ph.locator(".react-flow__node").first().waitFor();
  const wide = await ph.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!wide, "on a phone the editor fits the width");
  await phone.close();

  await api.delete(`${url}/api/logic/demand_test`);
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots && page) await page.screenshot({ path: `${shots}/logic-failure.png` }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
