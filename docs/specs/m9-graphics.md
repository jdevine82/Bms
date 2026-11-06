# Milestone 9: graphics

Status: **9A built** (6 Nov 2026); 9B planned. Main spec: 6.4 (symbols and graphics), 6.5 (tabs), 6.2 (HMI), 5.6 (commands), 18 (milestone 9).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **Two parts.** **9A:** the graphics model, the shipped symbol library, live rendering (states, colour rules, animation), tap to control, and the Site and room tabs on the web app and HMI, with starting graphics made automatically from the rooms and equipment. **9B:** the graphics editor in the web app, plus custom symbols and sanitised SVG import. |
| 2 | Where the symbols come from | **Equinor's engineering-symbols (MIT licence) where they fit**, recoloured by the BMS for states and the dark theme, and **the missing ones drawn for this project in the same line style** (light, door, meter, compressor and any other HVAC gap). The licence and credit ship with the symbols. |
| 3 | Where graphics are stored | **In the site setup** (`graphics` in the config file), so they are checked like everything else, exported with the setup (main spec 5.1.1: graphic bindings export as one file), backed up, and edited by admins (main spec 7). |

## 9A: model, library, rendering and tabs

### The model

A graphic has a name, display name, kind (`site`, `room` or `system`), an optional room (`space`), a size (a drawing area in its own units), an optional tab (`tab: {order}`), and items:

- **symbol:** a library symbol at a place and size, with bindings by point name: `run` (On or Off), `value` (a value symbol's reading or a level's fill), `fault`, and `command` (a writable point opened by tapping). Offline comes from the bound points' quality; Manual from a hand-mode override on the command point; Fault also from an active alarm on a bound point.
- **value:** a live reading with units (`--` and the unit when there is no data).
- **pipe** and **duct:** a polyline with a colour rule: *follows a symbol*, *value bands* (up to four, with hysteresis), or *proportional fill* (0 to 100 %). Flow animates only while the rule shows flow. No data: dashed grey.
- **room:** a rectangle with a name, live values and a value-band rule for its border and fill; tapping opens the room's tab.
- **text:** a label.

Checks: names unique, bound points exist, a `command` binding is a writable point, a room item's room exists, tab orders unique.

### Rendering (web app and HMI)

- SVG in the page, no scripts in symbols; animation by CSS (spinning fans and pumps, flowing pipes and ducts, level fills).
- **States** with the decided priority: Offline, then Fault, then Manual, then On or Off. Colour is never the only signal: Offline is dashed, Fault flashes with a badge, Manual has a hand badge, On animates.
- **Tap to control:** a symbol with a `command` binding opens the same command panel as the Points page (milestone 7A: limits, confirm, hand mode, audit). On the HMI it needs the PIN.

### Tabs (main spec 6.5)

- A **Graphics** page in the web app and the **Graphics** tab on the HMI show a tab strip: **Site** first, then the room and section graphics in their tab order.
- **Starting graphics:** a site with no graphics gets them made from its setup: a Site graphic with every room as a rectangle (its temperature and colour rule when it has a temperature point), and one graphic per room with its equipment's symbols bound by role and tags, laid out in rows. An admin can regenerate them, or edit them as JSON until the editor (9B).

### What 9A built

- **Config** (`graphics` in the setup file, `devine-bms-config/0`): graphics as above. Items have an `id` (unique in the graphic). Colours are named (`grey`, `blue`, `cyan`, `green`, `amber`, `red`) so each theme picks the shade. A value-band rule has up to four bands, each with `below` (the last has none), `colour` and `flow`, plus `hysteresis`; a fill rule has `min` and `max` (default 0 and 100). A symbol has `size` (default 64) and `rotate` (0, 90, 180 or 270). The published schema includes it.
- **Checks:** graphic names unique; item IDs unique in a graphic; bound points exist; a `command` binding is a writable point; band and fill points are numbers; band limits go up and the last band is open; fill max above min; a `follows` rule names a symbol in the same graphic; rooms exist; the symbol is in the library; tab orders unique.
- **Symbol library** (decision 025): 17 symbols in `web/public/symbols/`: pump, centrifugal and axial fans, compressor, on/off and proportional valves, damper, heating and cooling coils, heat recovery, filter, tank, temperature and humidity sensors, meter, light, door. 10 are Equinor's (MIT, licence in `LICENSE-equinor.txt`, credit in each file); 7 are drawn for the project in the same line style. `backend/src/devine_bms/graphics/library.json` says what each is, how it animates (a rotor at a spot, or the whole symbol) and its kind (state, position, level, value). `tools/build_symbols.py` rebuilds the SVGs from an Equinor checkout.
- **Starting graphics** (`graphics/generate.py`): a Site graphic with every room (its zone temperature, from a room sensor or else an indoor unit in it, with a cold/comfortable/warm/hot rule: below 18, 25 and 28 °C, or 64, 77 and 82 °F); one graphic per room with its readings and its equipment in rows, symbols chosen by equipment tag (pump, fcu, fan, heatExchanger, lighting, meter, chiller, heatPump, boiler, tank, door, valve, damper, filter) and bound by point tags (run state, else run command; `alarm` for fault; a writable run command for tapping; power for a meter); an air handler (`ahu`) as a duct run of outside air damper, filter, heating coil, cooling coil and fan, the duct following the fan; equipment in no room on a Plant graphic. They are shown until graphics are saved.
- **API:** `GET /api/graphics` (any signed-in role: the saved graphics in tab order, or the starting ones with `source: "generated"`), `GET /api/graphics/library`; admins: `PUT /api/setup/graphics/{name}` (with `?check=true` to check only; the first change saves the starting graphics with it), `DELETE /api/setup/graphics/{name}`, `POST /api/setup/graphics/generate` (replaces every saved graphic). All are checked setup changes in the audit log.
- **Web app:** a **Graphics** page with the tab strip (the open tab is in the address, `#/graphics/<name>`); **Setup → Graphics** lists them, edits one as JSON (Check, Save), adds, removes, and makes the starting graphics again.
- **HMI:** the **Graphics** tab with bigger tabs; tapping a symbol asks for the PIN first on a kiosk.
- **States:** Offline when any bound point is not Good (grey, dashed frame), Fault when the fault point is on or a bound point has an active alarm (red, flashing once a second, `!` badge), Hand when the command point has a hand-mode override (amber, hand badge), then On (green, rotor or whole symbol spins) or Off. Each symbol says its state in words under its name. Pipes and ducts: dashed grey with no data; a moving dash pattern while they flow. Animations stop when the device asks for reduced motion.

### How to set up graphics on a site

1. Set up rooms and equipment first (Setup → Rooms, Equipment, or from equipment templates): the starting graphics come from them, and their tags decide the symbols.
2. Open **Graphics** to see the starting graphics. They follow setup changes until one is saved.
3. To change one, **Setup → Graphics → Edit as JSON**, **Check**, then **Save**. Saving saves all the starting graphics into the setup; from then on new rooms and equipment do not appear by themselves: use **Make starting graphics again** (it replaces every saved graphic) or add them by hand.
4. Graphics are part of the setup file: they export, import and back up with it.

### Not in 9A

- Drawing graphics by hand (the editor), custom symbols and SVG import: 9B.
- Graphics follow the point list loaded with the page; after adding points, reload the page.

### Tests

- Backend (`tests/test_graphics.py`): every library symbol has a script-free SVG drawn in `currentColor`; the starting graphics for a plant room with an air handler and a pump and an office with an indoor unit (names, tab order, room temperature from the indoor unit, bindings by tag, the duct following the fan, the meter on Plant) and that they pass the checks; a site with no rooms; each check; unknown item types and colours refused; the API (starting graphics, the first save saving them all, a refused graphic with its reason, remove, make again, the audit log); the published schema.
- Browser (`web/tests/graphics.mjs`, on the simulated RS485 line): tabs; the office going from green at 22 °C to red at 30 °C; tapping a room opens its tab; tapping a pump opens the command panel and starts it in hand mode (Hand, spinning); the AHU duct flowing once the fan is On; a dirty filter showing Fault; a pump on a missing module showing Offline; the HMI tab with tap to command; no sideways scroll on a phone; Setup → Graphics refusing a bad graphic, saving, and making them again. `hmi.mjs` now checks the Graphics tab shows the Site graphic.

## 9B: the editor, custom symbols and SVG import

Planned after 9A.
