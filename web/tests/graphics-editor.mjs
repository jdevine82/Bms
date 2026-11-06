// Browser test of the graphics editor, site symbols and SVG import (milestone 9B).
//   BMS_URL=http://127.0.0.1:8000 node tests/graphics-editor.mjs [screenshot-dir]
// Uses config/site.example-setup.json (rooms plant and office, two meters).
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

const EVIL_BACKGROUND = `<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600" onload="alert(1)">
  <script>alert(1)</script><rect x="20" y="20" width="960" height="560" fill="none" stroke="#888" stroke-width="4"/>
  <line x1="500" y1="20" x2="500" y2="580" stroke="#888" stroke-width="4"/><image href="http://example.com/x.png"/></svg>`;
const VALVE = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" onload="x()">
  <g id="off"><path d="M4 8 L28 24 L28 8 L4 24 Z" fill="none" stroke="currentColor"/></g>
  <g id="on"><path d="M4 8 L28 24 L28 8 L4 24 Z" fill="currentColor"/></g></svg>`;

const browser = await chromium.launch();
try {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept());
  await signIn(page, url);
  const api = page.request;
  // A run on a database where these were saved before.
  await api.delete(`${url}/api/setup/graphics/test_plant`);
  for (const id of ["twin-fan", "imported-valve"]) await api.delete(`${url}/api/setup/graphics/symbols/${id}`);

  // ---------- site symbols: draw one, import one ----------
  await page.goto(`${url}/#/setup/graphics`);
  await page.getByRole("heading", { name: "Site symbols" }).waitFor();
  await page.getByRole("button", { name: "Draw a symbol" }).click();
  await page.getByLabel("ID", { exact: true }).fill("twin-fan");
  await page.getByLabel("Name", { exact: true }).fill("Twin fan");
  await page.getByLabel("When On").selectOption("spin");
  const board = page.getByLabel("Symbol drawing board");
  await board.scrollIntoViewIfNeeded();
  const bb = await board.boundingBox();
  const at = (x, y) => [bb.x + (x / 64) * bb.width, bb.y + (y / 64) * bb.height];
  const dragOn = async (a, b) => {
    await page.mouse.move(...at(...a));
    await page.mouse.down();
    await page.mouse.move(...at(...b), { steps: 4 });
    await page.mouse.up();
  };
  await page.getByRole("button", { name: "Circle", exact: true }).click();
  await dragOn([32, 32], [56, 32]); // both layers: the casing
  await page.getByLabel("New shapes go in").selectOption("off");
  await page.getByRole("button", { name: "Line", exact: true }).click();
  await dragOn([20, 32], [44, 32]);
  await page.getByLabel("New shapes go in").selectOption("on");
  await page.getByLabel("Filled").check();
  await page.getByRole("button", { name: "Rectangle", exact: true }).click();
  await dragOn([28, 16], [36, 48]);
  const shapes = await page.getByRole("list", { name: "Shapes" }).getByRole("listitem").count();
  check(shapes === 3, `three shapes drawn: a circle in both layers, a line in Off, a filled rectangle in On (${shapes})`);
  await shot(page, "editor-symbol-board");
  await page.getByRole("button", { name: "Save symbol" }).click();
  await page.getByText("Saved the symbol Twin fan").waitFor();
  const lib = await (await api.get(`${url}/api/graphics/library`)).json();
  const twin = lib.find((s) => s.id === "twin-fan");
  check(twin?.svg.includes('class="state-off"') && twin.svg.includes('class="state-on"') && twin.shapes?.length === 3,
        "the drawn symbol is saved with Off and On groups and its shapes");

  await page.getByRole("button", { name: "Import an SVG" }).click();
  await page.getByLabel("ID", { exact: true }).fill("imported-valve");
  await page.getByLabel("SVG file").setInputFiles({ name: "valve.svg", mimeType: "image/svg+xml", buffer: Buffer.from(VALVE) });
  await page.getByText("Removed from the file: event handlers.").waitFor();
  check(true, "importing an SVG lists what was removed (its onload handler)");
  check(await page.getByRole("figure").filter({ hasText: "On" }).isVisible(), "the import shows Off and On previews");
  await page.getByRole("button", { name: "Save symbol" }).click();
  await page.getByText("Saved the symbol valve").waitFor();
  const bad = await page.getByRole("button", { name: "Import an SVG" });
  await bad.click();
  await page.getByLabel("SVG file").setInputFiles({ name: "x.svg", mimeType: "image/svg+xml",
    buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><g id="off"/></svg>') });
  await page.getByText(/missing: on/).waitFor();
  check(true, "an SVG without an On group is refused with the reason");
  await page.getByRole("button", { name: "Cancel" }).click();

  // ---------- a new graphic in the editor ----------
  await page.getByRole("button", { name: "New graphic" }).click();
  await page.getByRole("toolbar", { name: "Tools" }).waitFor();
  await page.getByLabel("Name", { exact: true }).fill("test_plant");
  await page.getByLabel("Display name").fill("Test plant");
  const overlay = page.getByLabel("Drawing", { exact: true });
  const W = 1000, H = 600;
  // Measured before every use: the page can scroll as the properties panel changes.
  const pt = async (x, y) => {
    await overlay.scrollIntoViewIfNeeded();
    const ob = await overlay.boundingBox();
    return [ob.x + (x / W) * ob.width, ob.y + (y / H) * ob.height];
  };
  const clickAt = async (x, y, opts) => page.mouse.click(...(await pt(x, y)), opts);

  // Place the drawn symbol and a library pump.
  await page.getByRole("button", { name: "Symbol Twin fan" }).click();
  await clickAt(400, 100);
  await page.getByRole("heading", { name: /Symbol/ }).waitFor();
  await page.getByLabel("Label").fill("Supply fan");
  await page.getByRole("button", { name: "Symbol Pump" }).click();
  await clickAt(100, 300);
  const pumpSym = page.locator('svg.graphic [data-id="pump-1"]');
  check(await pumpSym.getAttribute("transform") === "translate(100 300)", "a symbol is placed on the grid where clicked");

  // Drag it by about 100 to the right: it snaps to the grid.
  const [from, to] = [await pt(130, 330), await pt(233, 334)]; // measured before the button goes down
  await page.mouse.move(...from);
  await page.mouse.down();
  await page.mouse.move(...to, { steps: 5 });
  await page.mouse.up();
  check(await pumpSym.getAttribute("transform") === "translate(200 300)", "dragging moves it, snapped to the 10-unit grid");
  // Resize from the handle.
  const handle = page.locator('[data-handle="resize"]');
  const target = await pt(296, 396);
  const hb = await handle.boundingBox();
  await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
  await page.mouse.down();
  await page.mouse.move(...target, { steps: 4 });
  await page.mouse.up();
  const width = await pumpSym.locator("svg").first().getAttribute("width");
  check(width === "100", `the square handle resizes it (to 100: ${width})`);

  // A duct by its corners, following the fan.
  await page.getByRole("button", { name: "Duct", exact: true }).click();
  await clickAt(100, 140);
  await clickAt(300, 140);
  await clickAt(300, 220);
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: /Duct/ }).waitFor();
  await page.getByLabel("Rule", { exact: true }).selectOption("follows");
  await page.getByLabel("Symbol", { exact: true }).selectOption("twin-fan-1");
  const duct = page.locator('svg.graphic [data-id="duct-1"] polyline').first();
  check((await duct.getAttribute("points")) === "100,140 300,140 300,220", "a duct is drawn by clicking its corners");

  // A room with a value-band rule, and a live value.
  await page.getByRole("button", { name: "Room", exact: true }).click();
  await clickAt(600, 300);
  await page.getByLabel("Room", { exact: true }).selectOption("office");
  await page.getByLabel("Rule", { exact: true }).selectOption("bands");
  await page.getByLabel("Point", { exact: true }).fill("plant_meter2_power");
  await page.getByRole("button", { name: "Live value" }).click();
  await clickAt(150, 500);
  await page.getByLabel("Point", { exact: true }).fill("site_meter1_power");
  await page.getByLabel("Label").fill("Site power");
  await page.waitForFunction(() => document.querySelector('svg.graphic [data-id="value-1"]')?.textContent?.includes("kW"),
                             null, { timeout: 15000 });
  check(true, "a live value shows the point's reading while editing");

  // Undo and redo, duplicate and delete.
  await page.locator(".editor-overlay").click({ position: { x: 5, y: 5 } }); // select nothing
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  const valueText = () => page.evaluate(() => document.querySelector('svg.graphic [data-id="value-1"]')?.textContent ?? "");
  const undone = await valueText();
  await page.keyboard.press("Control+y");
  await page.keyboard.press("Control+y");
  const redone = await valueText();
  check(!undone.includes("Site power") && redone.includes("Site power"), "undo and redo (Ctrl+Z, Ctrl+Y)");
  await clickAt(230, 330);
  await page.keyboard.press("Control+d");
  check(await page.locator('svg.graphic [data-id="pump-2"]').count() === 1, "Ctrl+D duplicates the selected symbol");
  await page.keyboard.press("Delete");
  check(await page.locator('svg.graphic [data-id="pump-2"]').count() === 0, "Delete removes it");

  // A background, cleaned on import.
  await page.locator(".editor-overlay").click({ position: { x: 5, y: 5 } });
  await page.getByLabel("Import an SVG background").setInputFiles({ name: "plan.svg", mimeType: "image/svg+xml",
                                                                     buffer: Buffer.from(EVIL_BACKGROUND) });
  await page.getByText(/Removed from the file: .*<image>.*<script>/).waitFor();
  check(await page.locator("svg.graphic .g-background").count() === 1, "an SVG background is cleaned and drawn under the graphic");

  // Check, then a bad point is refused, then Save.
  await page.getByRole("button", { name: "Check", exact: true }).click();
  await page.getByText("It passes the checks.").waitFor();
  await shot(page, "editor");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Saved Test plant; it is live.").waitFor();
  const saved = (await (await api.get(`${url}/api/graphics`)).json()).graphics.find((g) => g.name === "test_plant");
  check(saved && saved.items.length === 5 && !saved.background.svg.includes("script") && !saved.background.svg.includes("onload"),
        "saved: two symbols, a duct, a room and a value, with the cleaned background");
  check(page.url().endsWith("#/setup/graphics/test_plant"), "after the first save the address names the graphic");

  await clickAt(630, 330); // the room
  await page.getByLabel("Point", { exact: true }).fill("no_such_point");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("point 'no_such_point' does not exist").waitFor();
  check(true, "a graphic failing the checks is refused with the reason");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Undo" }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Saved Test plant; it is live.").waitFor();

  // ---------- removing a symbol in use; tab order ----------
  await page.getByRole("button", { name: "← Graphics" }).click();
  await page.getByRole("heading", { name: "Tab order" }).waitFor();
  const cards = page.locator(".symbol-card", { hasText: "Twin fan" });
  await cards.getByRole("button", { name: "Remove" }).click();
  await page.getByText(/still used on Test plant/).waitFor();
  check(true, "a site symbol in use cannot be removed");
  await page.getByRole("button", { name: "Test plant (make a tab)" }).click().catch(() => {});
  const tabs = page.getByRole("list", { name: "Tabs in order" });
  if (!(await tabs.getByText("Test plant").count())) failures.push("test plant is not in the tab list");
  for (let i = 0; i < 5 && (await tabs.getByRole("listitem").nth(1).textContent())?.includes("Test plant") === false; i++) {
    await page.getByRole("button", { name: "Move Test plant up" }).click();
  }
  await page.getByRole("button", { name: "Save tab order" }).click();
  await page.getByText("Saved the tab order; it is live.").waitFor();
  const order = (await (await api.get(`${url}/api/graphics`)).json()).graphics.filter((g) => g.tab).map((g) => g.name);
  check(order[1] === "test_plant", `the tab order is saved in one step (${order.join(", ")})`);

  // ---------- the live view ----------
  await page.goto(`${url}/#/graphics/test_plant`);
  await page.reload();
  await page.getByRole("tab", { name: "Test plant", selected: true }).waitFor();
  check(await page.locator('svg.graphic [data-id="twin-fan-1"] .state-off').count() === 1,
        "the Graphics page draws the site symbol with its Off and On groups");
  await shot(page, "editor-live");

  // ---------- a phone: no editing ----------
  const phone = await browser.newContext({ ...require("playwright").devices["iPhone 13"], storageState: await context.storageState() });
  const ph = await phone.newPage();
  await ph.goto(`${url}/#/setup/graphics/test_plant`);
  await ph.getByText("The graphics editor needs a wider screen").waitFor();
  check(true, "on a phone the editor says it needs a wider screen");
  await phone.close();
  check(errors.length === 0, `no page errors (${errors.join("; ")})`);
} catch (e) {
  if (shots) for (const pg of browser.contexts().flatMap((c) => c.pages())) await pg.screenshot({ path: `${shots}/failure.png` }).catch(() => {});
  failures.push(String(e));
  console.log(`FAIL ${e}`);
} finally {
  await browser.close();
}
console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
process.exit(failures.length ? 1 : 0);
