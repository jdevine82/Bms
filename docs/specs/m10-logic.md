# Milestone 10: control logic

Status: **10A built** (6 Nov 2026); **10B built** (7 Nov 2026); **10C in progress** (7 Nov 2026). Main spec: 5.3 (control logic), 5.3.1 (editor, blocks, execution, publishing), 5.3.2 (import and export), 5.2.1 (value quality), 5.6 (commands and write limits), 18 (milestone 10).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **Three parts.** **10A:** the logic format (`devine-bms-logic/1`), every v1 block, the checks, the runtime (1 s cycle, fail-safes, variables), drafts, publishing and versions, the API, and a test run against simulated inputs, all usable as JSON. **10B:** the React Flow editor (palette, wiring, undo, copy and paste, Live mode, test strip, import dialog with mapping). **10C:** template logic and roll-out, the equipment panel's list of diagrams, variables published as points (HMI changes with the PIN), snippets. |
| 2 | Logic's BACnet write priority | **10**: below operator hand mode (8), so a hand override always beats logic, and above schedules (16). Release to device relinquishes priority 10. |
| 3 | Who writes a point (proposed in 10A) | **One writer per point.** A point written by a published diagram cannot also have a schedule occupancy map or be written by another diagram; the publish checks refuse it. |
| 5 | Where the editor lives (10B) | **Its own Logic page** in the top bar: everyone signed in sees the diagrams and Live mode; admins also get Edit mode, publishing, import and variables. |
| 6 | Variables published as points (10C) | **Real points on a built-in BMS logic device** (protocol `logic`, no wire): a published variable is a normal point, so graphics, trends, alarms, commands with write limits and the HMI (with the PIN) all work. Writing the point sets the variable; the variable is the point's live value. |
| 7 | Where template logic is edited (10C) | **The same editor in role mode**, opened from the equipment template: point blocks pick roles. Publishing saves a **new template version** with the logic in it; instances move to it with the template roll-out (all, a group, one). |
| 8 | Snippets (10C) | **Yes, per site**, stored in the setup (so they export with it); a Snippets group in the palette inserts one like a paste. |
| 4 | Modbus and MQTT outputs (proposed in 10A) | These protocols have no priorities: logic writes the value when it changes (and again every 10 minutes); while a point is in operator hand mode logic does not write it; when hand mode ends the point gets logic's value. Release to device means logic stops writing. |

## 10A: format, blocks, runtime and API

### The format (`devine-bms-logic/1`)

```json
{
  "schema": "devine-bms-logic/1",
  "name": "ahu1_control", "dis": "AHU 1 control",
  "scope": "equipment", "equip": "ahu1",
  "variables": [{"name": "occ_sp", "type": "number", "unit": "°C", "kind": "constant", "value": 21, "min": 15, "max": 28}],
  "blocks": [
    {"id": "temp", "type": "point_read", "settings": {"point": "office_temp", "onBad": "fallback", "fallback": 21}, "position": {"x": 0, "y": 0}},
    {"id": "sp", "type": "variable_read", "settings": {"variable": "occ_sp"}},
    {"id": "cool", "type": "compare", "settings": {"op": ">", "deadband": 0.5}},
    {"id": "fan", "type": "point_write", "settings": {"point": "ahu1_fan_cmd", "failsafe": {"mode": "release"}}}
  ],
  "wires": [
    {"from": {"block": "temp", "port": "out"}, "to": {"block": "cool", "port": "a"}},
    {"from": {"block": "sp", "port": "out"}, "to": {"block": "cool", "port": "b"}},
    {"from": {"block": "cool", "port": "out"}, "to": {"block": "fan", "port": "in"}}
  ]
}
```

- Points are referred to by **name**, never by ID. Scopes in 10A: `equipment` (one piece of equipment, `equip` named) and `site`; `template` arrives with 10C.
- **Unwired inputs** take their value from `settings.inputs` (for example `{"b": 21}`), so constants need no extra block.
- Value types: `bool`, `number`, `text`. A wire joins only the same type.

### v1 blocks (main spec 5.3.1)

| Type | Inputs | Outputs | Settings |
|---|---|---|---|
| `point_read` | | `out` (the point's kind) | `point`; `onBad`: `fallback` (with `fallback` value) or `failsafe` |
| `point_write` | `in` | | `point`; `failsafe`: `release` (default), `hold`, or `value` with `value` |
| `variable_read` | | `out` | `variable` |
| `variable_write` | `in`, `enable` (bool, optional) | | `variable` (a memory variable) |
| `schedule` | | `out` (bool, Occupied) | `schedule` |
| `and`, `or` | `in1` … `inN` (bool) | `out` | `count` 2 to 8 |
| `not` | `in` | `out` | |
| `compare` | `a`, `b` (number) | `out` (bool) | `op` (`<`, `>`, `<=`, `>=`, `==`, `!=`), `deadband` |
| `limit` | `in`, `min`, `max` | `out` | `rate` (units per second, optional) |
| `scale` | `in` | `out` | `inMin`, `inMax`, `outMin`, `outMax`, `clamp`, `unit` |
| `delay_on`, `delay_off` | `in` (bool), `time` (s) | `out` | |
| `min_on_off` | `in` (bool), `minOn`, `minOff` (s) | `out` | |
| `astro` | `enable` (bool, optional) | `out` (bool) | `active`: `night` or `day`; `sunsetOffset`, `sunriseOffset` (minutes) |
| `pid` | `pv`, `sp`, `enable` (bool), `track` (number) | `out` | `kp`, `ti`, `td` (s), `outMin`, `outMax`, `action` (`direct` or `reverse`), `sample` (s) |
| `manual_auto` | `auto`, `manual`, `mode` (bool, true = Manual) | `out` | `valueType` (`number` or `bool`) |

- **Compare with deadband:** for `>` the output goes true above `b + deadband` and false below `b − deadband` (and the other way for `<`).
- **PID** is a positional PID with anti-windup (the integral stops at the output limits) and, while `enable` is false, **tracks** `track` (or holds its output) so going back to auto is bumpless.
- **Astro** calculates sunrise and sunset locally (NOAA method) from the site's `geoCoord` and time zone; it needs the site's latitude and longitude.

### Quality and fail-safe (main spec 5.2.1, 5.3.1)

- Every value carries its quality. A `point_read` whose point is not Good gives its fallback, or (with `onBad: failsafe`) a **bad** value. A block with a bad input gives a bad output.
- A `point_write` whose input is bad applies its **fail-safe**: `release` (BACnet: relinquish priority 10; Modbus and MQTT: stop writing), `hold` (leave the last value) or `value` (write the stated value). The fail-safe also applies when the diagram is disabled, unpublished or removed, when a block fails, and when the server stops.

### The runtime

- Every published, enabled diagram runs on a **1 s cycle** in a fixed order from inputs to outputs. A loop must pass through a timing block (`delay_on`, `delay_off`, `min_on_off`); that block reads its input from the previous cycle. A loop without one is refused.
- Outputs are written at **priority 10** (decision 2), clamped to the point's write limits (5.6), only when the value changes (and again every 10 minutes), never while the point is in operator hand mode. When hand mode ends, the point gets logic's value.
- **Variables:** `constant` (set by a person, kept) or `memory` (written by `variable_write`, kept through a restart). Scope in 10A: `site` (shared) or `diagram` (private). Equipment scope arrives with templates in 10C.
- Timers and PID restart from a known state after a server restart (timers idle, PID from its tracked or minimum output).

### Drafts, publishing and versions

- Each diagram has a **draft** (saved on every change, may be incomplete) and numbered **published versions**. Only a published version runs.
- **Publish checks** (errors stop publishing): the format; known block types and valid settings; wires between existing ports of the same type; each input wired at most once; required inputs wired or given a value; bound points, variables and schedules exist; written points are writable; no loop without a timing block; **no two writers** for a point (decision 3); astro needs the site's location; the fail-safes are confirmed (`confirmFailsafe: true`). Warnings: blocks without positions, unused outputs.
- Publishing makes a new version (audit-logged); the previous ones can be restored into the draft.
- **Enable or disable** per diagram; disabling applies the fail-safes at once.

### API (admins change; any signed-in role reads)

- `GET /api/logic`, `GET /api/logic/{name}` (draft, published version, versions, state).
- `PUT /api/logic/{name}/draft` (create or save the draft), `POST /api/logic/{name}/check`, `POST /api/logic/{name}/publish` (`{confirmFailsafe, note}`), `POST /api/logic/{name}/restore` (`{version}`), `POST /api/logic/{name}/enable` (`{enabled}`), `DELETE /api/logic/{name}`.
- `GET /api/logic/{name}/live`: every block's inputs and outputs with quality, timers' time left, clamping, each output's state (written, hand mode, fail-safe).
- `POST /api/logic/{name}/test`: run the **draft** against simulated input values for a number of steps with a chosen step length, writing nothing; returns each step's outputs.
- `POST /api/logic/import`: validate pasted JSON and create or replace a draft (errors block it; warnings are returned).
- `GET /api/logic-variables`, `PUT /api/logic-variables/{name}` (create or change a site variable, or set its value within its limits; audit-logged), `PUT /api/logic/{name}/variables/{variable}` (a diagram's own variable).
- `GET /api/logic-blocks`: the block types with their settings schemas (for the editor's palette).

### What 10A built

- `backend/src/devine_bms/logic/`: `model.py` (the format and the block registry), `check.py` (the checks and the evaluation order), `evaluate.py` (one cycle, shared by the runtime and test runs), `astro.py` (sunrise and sunset), `engine.py` (the runtime), `service.py` (drafts, publishing, versions, import, variables); `api/logic_api.py`.
- The published JSON Schema: `config/schema/devine-bms-logic-1.schema.json` (with each block's settings; `python -m devine_bms.logic.write_schema` rewrites it).
- Database (migration 0013): `logic_diagram` (draft, enabled, published version), `logic_version` (each published body with its note, who and when), `logic_variable` (values kept through a restart; site variables' definitions).
- **Import** (main spec 5.3.2): errors in the diagram itself (unreadable JSON, unknown block types, bad settings, wires to missing ports or between types, an input wired twice, duplicate IDs, a loop without a timing block) stop it. What does not fit this site (unknown points, variables or schedules, a point another diagram writes) comes back as **warnings**, and the draft is created; the publish checks then insist on them. The mapping table arrives with the editor (10B).
- **Hand mode** ends at logic's value when logic writes the point, else the schedule's, else the value before (decision 027).
- A setup change reloads logic too, so a diagram whose point was removed or changed stops (its state shows the reason) instead of running on a stale view.

### How to try 10A (before the editor)

1. Set a site variable: `PUT /api/logic-variables/lights_on` with `{"type": "bool", "kind": "constant", "value": true}`.
2. Save a draft: `PUT /api/logic/<name>/draft` with the diagram JSON (the format above). The answer lists what the publish checks would say.
3. Try it: `POST /api/logic/<name>/test` with `{"inputs": {"<point>": value}, "steps": 10, "dt": 1}`; nothing is written.
4. Publish: `POST /api/logic/<name>/publish` with `{"confirmFailsafe": true, "note": "..."}`. Watch it: `GET /api/logic/<name>/live`.
5. Disable with `POST /api/logic/<name>/enable` `{"enabled": false}`: the fail-safes apply.

### Tests

- Backend (`tests/test_logic.py`): the block set; a good diagram and its order; every check (unknown type, bad settings, duplicate IDs, wired twice, types, unwired inputs, unwritable points, schedule and diagram writers, missing points, schedules and variables, constants written by logic, a missing fallback, astro without a location, loops with and without a timing block); compare with deadband and delay on with a clock; fallback and fail-safe on a bad input; limit with rate, scale with clamp and inversion; delay off and minimum on/off; AND, NOT, manual/auto and schedules; PID (direction, anti-windup, bumpless tracking); astro in Sydney; variable writes and a failing block. With the database and the simulated RS485 line: drafts and publish checks, the fail-safe confirmation, the outputs written (the limit clamped to its write limits), a variable change followed, hand mode winning and ending at logic's value, one writer per point, disable and enable with the fail-safes, a memory variable saved, versions and restore, test runs that write nothing, import (errors, warnings, name clash), removal and the audit log.
- Astro times checked against published times for Sydney (summer and winter) and London; polar day and night handled.

## 10B: the editor (web app)

Main spec 5.3.1 Layout, Editing blocks, Edit and Live modes, and 5.3.2. Built with React Flow (`@xyflow/react` 12.12.0, MIT).

- **Logic page** (`#/logic`): the diagrams with their state (draft, running, disabled, error), published version, what they write; **New diagram** and **Import** for admins. A diagram opens at `#/logic/<name>`.
- **Top bar:** name and display name, status (draft or published version N, running or not, unpublished changes), enabled switch, Edit and Live, Undo and Redo, Check, Publish, Versions, Export.
- **Palette (left):** the v1 blocks in their groups, searchable; drag a block onto the canvas or click it to add it in the middle.
- **Canvas:** blocks flow left to right, inputs on the left of a block and outputs on the right. Port shape and colour show the type (true/false round, number square, text diamond), and a wire only joins the same type; a new wire into a used input replaces the old one. Snap to grid, minimap, fit to screen. Blocks without positions are laid out by their order.
- **Selecting and editing:** click, Shift-click or drag a box; **Delete** or **Backspace**, the Delete button, or right-click → Delete. Deleting an output block, or a block that feeds one, asks first (its output falls to the fail-safe once published). A variable still used by a block cannot be removed. Undo and redo (Ctrl+Z, Ctrl+Y) for the session; cut, copy, paste and duplicate (Ctrl+X, Ctrl+C, Ctrl+V, Ctrl+D) in the import format, so blocks move between diagrams and browser tabs.
- **Drafts save automatically** a second after each change.
- **Properties (right):** the selected block's settings (points, variables and schedules picked from lists; values for unwired inputs; the fail-safe of an output); with nothing selected, the diagram's display name, scope, equipment, description and its own variables. The check results are listed here, each naming its block (click to select it).
- **Publish** shows each output's fail-safe and asks to confirm them, and takes a note.
- **Live mode** (published version): every wire shows its value, blue when true and grey when false; blocks show bad inputs, errors, time left on timers, clamping, PID mode and the next astro switch; outputs show written, hand mode or fail-safe. The variables list shows values; admins can set constants.
- **Test strip (below the canvas, Edit mode):** a value, switch or Offline for each point the diagram reads and each schedule; steps and step length; **Run** shows the outputs at each step. Nothing is written.
- **Import dialog:** paste text or choose a `.bmslogic.json` file; **Import as new diagram** or **Paste into this diagram**. Errors are listed per block, with **Remove the failing blocks and continue**. Unresolved points, variables and schedules are listed in a **mapping table** with suggestions; clashing block IDs are renumbered; blocks without positions are laid out.
- **Export:** the whole diagram or the selection, copied or saved as `<name>.bmslogic.json`.

### What 10B built

- `web/src/logic/`: `LogicPage.tsx` (the list, New diagram, Import, site variables), `LogicEditor.tsx` (canvas, top bar, keyboard, clipboard, autosave, Live polling, Publish, Versions, Export), `BlockNode.tsx`, `Palette.tsx`, `Properties.tsx`, `TestStrip.tsx`, `ImportDialog.tsx`, `blocks.ts` (the block ports, matching `logic/model.py`), `diagramOps.ts` (layout, fragments, merging).
- The Logic page loads separately, so the main page and the HMI do not carry the editor (about 250 KB).
- The API answers `ok` (the request worked) and `valid` (the diagram passes the checks) separately, so a draft with errors is still saved. `POST /api/logic/import` with `"check": true` checks without creating anything (the import dialog's Check).
- Blocks added from the palette step down to free space instead of covering another block.

### How to use the editor

1. **Logic → New diagram**, give it a name and display name.
2. Add blocks from the palette (click, or drag onto the canvas). Set each block on the right: pick its point, variable or schedule; give unwired inputs a value; choose an output's fail-safe.
3. Wire an output port (right side) to an input port (left side). Ports of different types do not join; their shape and colour show the type.
4. The draft saves itself; **Checks** on the right list what would stop publishing (click a block name to select it).
5. In **Test** below the canvas, simulate the points (a value or Offline) and schedules, then **Run**: the outputs at each step appear, and nothing is written.
6. **Publish…**: confirm the fail-safes, add a note. **Live** shows it running. **Versions** restores an earlier version into the draft; the **Enabled** switch stops it (fail-safes apply).
7. **Export** copies or saves the diagram or the selection; **Import** (on the Logic page or in a diagram) takes pasted text or a file, maps points, variables and schedules that are not found, and can remove failing blocks.

Keys: Delete or Backspace, Ctrl+Z, Ctrl+Y, Ctrl+C, Ctrl+X, Ctrl+V, Ctrl+D, Ctrl+A, Escape. Shift-click and Shift-drag select several.

### 10B tests

- Browser (`web/tests/logic.mjs`): a new diagram from the Logic page; blocks from the palette set in the properties panel; three wires drawn port to port; a wire between types refused; port types shown; undo and redo; copy and paste (renumbered); Delete; the warning before deleting an output (Cancel keeps it); right-click Delete; autosave; the test strip (10 kW scaled to 50, nothing written); Publish needing the fail-safes confirmed; Live values on the wires and the output written; Versions; Export; Import with the mapping table (suggestions), an unknown block type removed, and the paste landing in the draft; disabling; the Logic page's state; a phone without sideways scrolling.
- Backend: `ok` and `valid`, and the import check, in `tests/test_logic.py`.

## 10C: templates, variables as points, the equipment panel, snippets

### Variables as points (decision 6)

- A device with protocol **`logic`** (no connection settings) holds points whose address is `{"variable": "<name>"}` (a site variable) or `{"variable": "<name>", "diagram": "<diagram>"}` (a diagram's own variable). It is not polled: the logic runtime gives each point its variable's value (on every change and at least every 20 seconds), so it has quality, history and alarms like any point; a missing variable makes the point Bad.
- Writing the point (a command, the HMI Setpoints screen with the PIN, a schedule) sets the variable, within the point's write limits and the variable's limits, and is audit-logged as a command. A constant becomes a **setpoint** point; a memory variable becomes a read-only **sensor** point.
- **Publish as point** in the variables lists makes the point (and the BMS logic device the first time), with the variable's unit and limits; the point is then edited like any other in Setup.

### Template logic (decision 7; main spec 5.1.2, 5.3.1)

- An equipment template version can carry a **logic diagram** (`logic` in the template, the same format) whose point blocks name a **role** (`{"role": "room_temp"}`) instead of a point.
- **Editing:** Equipment templates → a template → **Logic** opens the editor in role mode (role pickers instead of points). Its draft is kept like any diagram's draft. **Publish** saves a new template version (the next version number, or one you give) with the logic in it; nothing changes on the instances until they are rolled out to that version (all, a group, or one at a time).
- **Running:** every instance on a version with logic runs its own copy, roles mapped to that instance's bound points, with its own variables. An instance whose required role is not bound does not run (its state says which). An **equipment diagram** for the same piece of equipment replaces the template's logic on it (an instance override always wins).
- **Per-instance switch:** each instance's logic can be disabled on its own (its outputs go to their fail-safe).
- Instances appear on the Logic page (scope Template) with Live mode.

### The equipment panel (main spec 5.3, 5.3.1)

- Setup → Equipment shows, for each piece of equipment, the diagrams that write its points (site, equipment and template diagrams) with their state, and whether it is **BMS-controlled** (a running diagram writes one of its points) or **device-controlled**.

### Snippets (decision 8; main spec 5.3.2)

- In the editor, **Save as snippet** stores the selection under a name (`logicSnippets` in the setup); the palette's **Snippets** group inserts one where you click, through the same mapping as a paste. Snippets can be removed from the Logic page.
