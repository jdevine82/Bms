# Milestone 5: auto-discovery and point setup

Status: **in progress** (5 Nov 2026). Main spec: 5.1 (devices, templates, test tools), 5.1.1 (commissioning), 5.1.2 (equipment templates), 5.1.3 (Discover), 7 (admin role), 18 (milestone 5).

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

- Point roles, an optional graphic reference and logic reference, parameters, versions, and instances that bind roles to device points.
- An instance's overrides always win; a new template version rolls out to all, a group or one instance at a time, after a side-by-side difference.

## 5B: screens

Planned after 5A: the setup pages in the web app for everything above, including the 4-step commissioning wizard and the mapping table.
