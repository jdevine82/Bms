# Milestone 5: auto-discovery and point setup

Status: **5A and 5B built** (5 Nov 2026). Main spec: 5.1 (devices, templates, test tools), 5.1.1 (commissioning), 5.1.2 (equipment templates), 5.1.3 (Discover), 7 (admin role), 18 (milestone 5).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | The master copy of the site setup | **The database.** The web app changes it directly and changes go live without a restart. The config file (`devine-bms-config/0`) becomes the **export and import format** (main spec 3.1 and 5.1.1). At start-up the backend uses the database; a file is imported on purpose, or automatically only when the database has no site yet. Standalone mode (later) keeps saving the same format to its config file. |
| 2 | Changes before logins exist | **Localhost only until milestone 6.** Setup changes are refused unless the request comes from the same machine (127.0.0.1 or ::1). Reading is open, as now. Every change is recorded in a **change log** (who: the client address; what: before and after), which milestone 6 turns into the audit log (main spec 5.6). |
| 3 | How it is split | **Two parts.** **5A, backend:** the database as master, import and export, the setup API with the same checks as the config file, live reload, the localhost guard and change log, the device template library, test reads with raw requests and answers, the Discover scans, and equipment templates. **5B, screens:** the device and bus screens, the template editors, Discover, the 4-step commissioning wizard (5.1.1) and the mapping table with CSV. |

## 5A: backend

### Import, export and start-up

- `GET /api/setup/config` exports the whole site in `devine-bms-config/0`; `PUT /api/setup/config` imports one (with `?check=true` to only check it).
- Command line, for the bench and for moving a site:

  ```bash
  uv run python -m devine_bms.setup.cli export > site.json
  uv run python -m devine_bms.setup.cli import site.json
  ```

- Start-up: if the database has the site, it is used as it is. If it has no site and `BMS_CONFIG_PATH` points at a file, that file is imported once.

### Setup API

Every change goes through the same path: the current setup is exported, the change is applied to it, **the whole result is checked with the config file's checks**, and only then is it saved and the runtime reloaded. A change that fails a check is refused with the list of errors; warnings are returned and allowed.

- `PUT /api/setup/{kind}/{name}` adds or replaces one item, where kind is `spaces`, `equips`, `buses`, `devices` or `points`.
- `DELETE /api/setup/{kind}/{name}` removes one. Points and devices are disabled, not deleted, so their history is kept (as the config sync does). Spaces, equipment and buses can only be removed when nothing uses them.
- `POST /api/setup/{kind}/{name}/rename` keeps the item's ID; a renamed point keeps its old name as an alias (main spec 5.2).
- `?check=true` on any change returns the errors and warnings without saving.
- **Live reload:** after a change the runtime restarts its pollers with the new setup; current values are kept, so the screens do not blank.

### Device template library (main spec 5.1)

- Built-in templates (shipped files) and **site templates** saved in the database, with versions. List, read, save, delete, export and import as files.
- Templates cover Modbus, BACnet and MQTT points.
- Applying a template to a device creates its points (bulk create with a name prefix and an optional subset).

### Test reads (main spec 5.1, step 4)

- Read one point, or a device's whole point map, and see the value, the **raw request and answer**, errors and timing. Works for a saved device and for a device that is not saved yet.
- Reads go through the bus's own poller connection, between polls, so a serial port is never opened twice. The same mechanism carries commands ahead of polling later (milestone 7).
- Read-only in milestone 5.

### Discover scans (main spec 5.1.3)

- **BACnet/IP:** Who-Is, then vendor, model and object list where the device gives them.
- **BACnet MS/TP:** Who-Is through the bms-mstp service for one trunk.
- **Modbus TCP:** a range of IP addresses on port 502; a hit is anything that answers, including with an exception; vendor and model through device identification (function 43/14) where supported.
- **Modbus RTU:** a range of unit IDs on one bus, through the bus's own poller connection.
- Scans run in the background with progress, can be stopped, are limited to the chosen range, **never write**, and are recorded in the change log. A scan on a live serial bus needs a confirmation flag.
- Each result has a status: **New, Already added, Address clash** (two devices at one address), and a **template suggestion** from vendor and model.

### Equipment templates (main spec 5.1.2)

- **Nine built-in starter templates** (the decided list): indoor unit, AHU, room (a template for a room, not equipment), pump, fan, exhaust fan, ERV, light circuit, power meter. Roles and tags follow Appendix A. A site saves its own templates, with every version kept; built-in IDs cannot be reused.
- A template holds **point roles** (the point fields each role gives: display name, type, kind, unit, tags, speed class, logging; required or optional), tags for the equipment, parameters, and references for a graphic and logic once those exist.
- **Making equipment from a template:** each role is filled by a new point on a device (by address), by an existing point, or for every role at once **from a device template** (role names match device template keys, or a mapping is given). With a repeat, {n} in the names and the device template's address step make one instance per group: **three Mitsubishi indoor units from the BAC-HD150 template in one call.** New points are named `<room>_<equipment>_<role>`.
- **Overrides are worked out, not stored:** a point field that differs from what its role gives is an override. The instance view lists each role's overrides, and one can be reset to the template value.
- **Roll-out** of a new version to all instances, a list, a room or a tag, with a **preview** (`?check=true`): each field change, each override kept, required roles that now need a binding, and roles that were dropped. A field changes only where the point still has the old version's value: **an override always wins**.
- A **side-by-side difference** between two versions; **unlink** turns an instance into plain equipment.
- Templates and instances are part of the configuration format (`equipTemplates`, and `template` on equipment and rooms), so they are checked, exported and backed up with everything else (migration `0008`).

### Checks added for templates

- A template version is listed once; built-in IDs are not reused; role names are unique.
- An instance names a template version that exists, and of the right kind (equipment or room).
- Each binding names an existing point that belongs to that instance; one point fills one role.
- A required role that is not bound is a warning.

## Found and fixed while building 5A

- **Renaming a point to another point's old name** was allowed, which would have made old CSV files and logic resolve to the wrong point. Now refused (a point may take back its own old names).
- **The Modbus RTU simulator assumed every request is 8 bytes**, so a device identification request (7 bytes) made it lose its place; it now frames by function, as devices do, and answers device identification.
- **A flaky history test** (history is written in batches a moment after each reading); fixed.

## Notes

- Scans of BACnet/IP use the site's BACnet link; on loopback in tests a Who-Is goes to one address (`target`). Broadcast Who-Is across a subnet is checked on the bench.
- A test read of a point kept current by COV asks the device directly, so it always shows the device's own answer.
- The change log records the client address as "who" until logins exist (milestone 6).

## 5B: screens

A **Setup** page in the web app (header: Points, Trends, Setup), laid out as the mockups: a list on the left, the work in the middle and a panel on the right. Pages have their own addresses (`#/setup/discover` and so on).

| Group | Page | What it does |
|---|---|---|
| Commissioning | **Commission a device** | The 4-step wizard (below). |
| | **Discover** | Per network: range, Scan, progress, Stop. Results with status (New, Already added, Clash, Missing), what the device reported and the template suggestion. A picked result can be added, test read, or sent to the wizard. A scan on a serial bus asks for confirmation first. |
| | **Mapping table** | Every point: display name, equipment, room, tags, unit, scaling, editable in place. Filters (no room, untagged, duplicate names, not communicating), search, by device; select rows to set a room, move to equipment or add tags; add rooms by name. **Export and import CSV** (`name, dis, equip, room, tags, unit, scale, device`; matched by name; an unknown room name is created). The right panel shows the checks; everything is saved as **one change**. |
| Setup | **Devices, Buses, Rooms, Equipment** | A list with live status (online, offline, point count) and an editor (form or JSON) with Check, Save, Rename (keeps history and the old name as an alias) and Remove. Devices have **Test connection** with raw requests and answers; buses show their live connection and MS/TP trunk figures. |
| Templates | **Device templates** | Point maps, notes, status (datasheet only, bench tested); export, import, remove a site version; apply to a saved device with a prefix, a subset and a repeat. |
| | **Equipment templates** | Roles, parameters, instances and versions. Built-in ones are read-only: **Copy template** to change one. Editing makes a new version, with a **preview per instance before saving** (updated, keeps its own value, needs a binding). Then roll out to all or to chosen instances. Side-by-side difference between two versions. An instance shows each role as From template or Overridden, with **Reset to template**, and **Unlink**. |
| Site | **Site and setup file** | Display name, time zone and BACnet settings; export the whole setup, and import one (checked first, then confirmed). |
| | **Change log** | Every change and scan, who (the client address until logins), and before and after. |

### The commissioning wizard (main spec 5.1.1)

1. **Device:** a device template (filtered to the protocol) and the connection, or an existing device, or one sent from Discover. **Test connection** reads the template's whole point map (the first group, for a gateway) through the bus's own connection and shows pass or fail per point with the raw answers. Nothing is saved.
2. **Equipment:** an equipment or room template (or none: the device template's points only), the name and display name ({n} with bulk create), the room and the address step. **Build the table** makes the equipment and points **on a draft**, and shows the new equipment (name, room) and **one editable table** of the new points (names can still change here, since nothing is saved). **Test the new points** reads them.
3. **Verify and go live:** a checklist (points, logging, poll speed, communicating, write limits and alarms, which come in milestones 7 and 6) and the checks: errors (duplicate names, failed setup checks) block **Go live**; warnings about this device can be accepted. Go live saves the draft as one checked change.
4. **Place on graphics:** the graphics editor is milestone 9, so this step says so and is skipped; rooms list their points until then.

### Decisions made while building 5B

- **Drafts are kept in the browser** (local storage) until Go live. Each step's work is checked by the backend on the draft (`"draft": <whole setup>` on template apply, equipment create and roll-out returns the changed setup and saves nothing), so the checks are the same as for a saved change. A draft is lost if the browser's storage is cleared; that only loses unsaved work. [Assumed; easy to move to the database later if drafts should follow a user between machines.]
- **Saving a whole edited setup** (Go live, the mapping table): `POST /api/setup/commit` with the setup and the **revision** it was read at (`X-Setup-Revision` on `GET /api/setup/config`). If someone else changed the setup meanwhile, it is refused (409) instead of overwriting their change.
- The mapping table **does not rename saved points** in place; renaming goes through Rename on the point (or device) so the history and the old name are kept.
- A test read of points that belong to equipment not saved yet is allowed (the wizard's step 2): only the addresses matter for a read.

### Running the screen tests

The browser test changes the database, so it needs a fresh one loaded from `config/site.example-setup.json` and the two simulators:

```bash
cd backend
uv run python ../simulators/modbus_meter.py --port 5020 &
uv run python ../simulators/modbus_meter.py --port 5021 --framing rtu --units meter:1,io:2,io:3 &
# BMS_DATABASE_URL (in backend/.env) must name an empty, migrated database
BMS_CONFIG_PATH=../config/site.example-setup.json BMS_WEB_DIST=../web/dist uv run uvicorn devine_bms.api.app:app --port 8000 &
cd ../web && npm run build && BMS_URL=http://127.0.0.1:8000 npm run test:setup
```

It covers: Discover on the RS485 bus, the wizard from a Discover result to Go live (with an edited display name and a renamed point), the mapping table (tags in place, CSV export and import), a device test read with raw frames, copying and saving an equipment template, and the change log. 21 checks pass.

### Not covered yet

- Discover for BACnet/IP, MS/TP and Modbus TCP is in the screen but was only exercised through the API tests (5A); the browser test uses the RS485 bus.
- Replace a device (main spec 5.1.1, other tools) and the scan snapshot comparison (5.1.3) are not built.
- Setup is laid out for a laptop; below 1000 px wide it stacks into one column.
