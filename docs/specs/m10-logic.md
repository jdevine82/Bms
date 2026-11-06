# Milestone 10: control logic

Status: **10A in progress** (6 Nov 2026). Main spec: 5.3 (control logic), 5.3.1 (editor, blocks, execution, publishing), 5.3.2 (import and export), 5.2.1 (value quality), 5.6 (commands and write limits), 18 (milestone 10).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **Three parts.** **10A:** the logic format (`devine-bms-logic/1`), every v1 block, the checks, the runtime (1 s cycle, fail-safes, variables), drafts, publishing and versions, the API, and a test run against simulated inputs, all usable as JSON. **10B:** the React Flow editor (palette, wiring, undo, copy and paste, Live mode, test strip, import dialog with mapping). **10C:** template logic and roll-out, the equipment panel's list of diagrams, variables published as points (HMI changes with the PIN), snippets. |
| 2 | Logic's BACnet write priority | **10**: below operator hand mode (8), so a hand override always beats logic, and above schedules (16). Release to device relinquishes priority 10. |
| 3 | Who writes a point (proposed in 10A) | **One writer per point.** A point written by a published diagram cannot also have a schedule occupancy map or be written by another diagram; the publish checks refuse it. |
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
- `GET /api/logic-variables`, `PUT /api/logic-variables/{name}` (set a constant, within its limits; audit-logged).
