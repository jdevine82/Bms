# Milestone 2: simulated Modbus device to stored data and a trend

Status: **reviewed** (1 Nov 2026). The five open choices are decided (section 12). Items still marked **[Assumed]** are build details that can be adjusted during the build and recorded in `docs/decisions/`.
Main spec: `devine-bms-specification.md`, milestone 2 in section 18.

## 1. Goal

The first thin end-to-end slice (section 12, step 1): a **simulated Modbus TCP power meter** is polled by the backend, its values become **Haystack-tagged points** with a quality status, they are **stored** in PostgreSQL + TimescaleDB, and the **web app** shows live values and a **trend chart**.

This slice sets up the foundations every later milestone builds on: the repository layout, the database schema for sites, rooms, equipment, devices and points, the site configuration file format, the Modbus driver, value quality, and the backend-to-web data path.

## 2. In scope and out of scope

**In scope**
- A Modbus TCP device simulator with one simulated power meter.
- Backend (Python 3.12, FastAPI, asyncio): config loading, Modbus TCP polling, value decoding and scaling, value quality (5.2.1), a live value cache, history storage, a REST API and a WebSocket for live values.
- Database: schema and migrations for the model in section 4 below, a TimescaleDB hypertable for history, and the 30-day raw retention policy.
- Web app (React + TypeScript + Vite): a points list with live values and quality, and a trend chart for one or more points, with CSV export of the plotted data (5.7).
- Automated tests (section 9).

**Out of scope (later milestones)**
- Logins, roles and the audit log (milestone 6 and section 7). The API is open on localhost only in this milestone.
- Writing to devices, commands, write limits and hand modes (5.6).
- Modbus RTU, BACnet, MQTT, Discover and the commissioning wizard (milestones 4 and 5). Devices and points come from the config file.
- Hourly and daily rollups (5.7) beyond creating the raw retention policy. **[Assumed]** Rollups are added in a later milestone.
- Alarms, schedules, logic, graphics and the HMI layout.
- The Haystack HTTP API (milestone 12a). Points are already stored with Haystack tags so the API needs no schema change.
- Standalone storage (the config-file implementation of the storage interface, 3.1). This milestone defines the interface and builds only the PostgreSQL implementation.
- Proxmox and LXC deployment. This milestone runs on a developer machine.

## 3. The simulated device

**Decided: a simulated power meter.** `simulators/modbus_meter.py` is a small self-contained Modbus TCP server (function codes 03 and 04). It does not use pymodbus or the BMS's own Modbus code, so a decoding bug in the BMS cannot be hidden by the same bug in the simulator. (The pymodbus server API was mid-rewrite in pymodbus 3.16.)

| Item | Value |
|---|---|
| Protocol | Modbus TCP, default `127.0.0.1:5020` (5020 avoids needing root for port 502) |
| Unit ID | 1 |
| Behaviour | Values follow a repeating daily-style pattern with small random noise, so trends look realistic. Energy only increases. |
| Fault injection | Modes to **stop answering** (`down`), **return a Modbus exception** (`exception`) and **stop changing its values** (`frozen`). Used by the tests in section 9. |

**Register map** (holding registers, function code 03; wire addresses are 0-based):

| Wire address | Manual address | Point | Data type | Byte/word order | Scale | Unit |
|---|---|---|---|---|---|---|
| 0 to 1 | 40001 | Active power | float32 | big-endian bytes, high word first | 1 | kW |
| 2 to 3 | 40003 | Voltage L1-N | float32 | big-endian bytes, high word first | 1 | V |
| 4 to 5 | 40005 | Current L1 | float32 | big-endian bytes, high word first | 1 | A |
| 6 | 40007 | Power factor | int16 | big-endian | 0.001 | (none) |
| 8 to 9 | 40009 | Import energy | uint32 | big-endian bytes, **low word first** | 0.1 | kWh |

The mixed word order and the scaled integers are deliberate, so the driver's decoding is tested from the start (5.1).

## 4. Data model (first tables)

All tables are created by migrations. **Decided:** SQLAlchemy 2 (async, with asyncpg) for database access and Alembic for migrations. TimescaleDB-specific steps (hypertable, retention policy) are written as raw SQL inside the Alembic migrations. IDs are UUIDs and serve as the Haystack `id` (5.2). Times are stored in UTC.

| Table | Purpose and main columns |
|---|---|
| `site` | `id`, `dis`, `tz` (default `Sydney`), `geo_coord`, `area`, `tags` (jsonb) |
| `space` | Rooms and sections (Haystack `space`). `id`, `site_id`, `dis`, `tags` |
| `equip` | `id`, `site_id`, `space_id` (nullable), `parent_equip_id` (nullable, the "part of" link), `dis`, `tags` |
| `equip_feed` | The "feeds" links (5.2): `from_id`, `to_id`, `to_kind` (equip or space), `medium` (air, chilled water, hot water, condenser water, elec, refrig) |
| `device` | `id`, `site_id`, `dis`, `protocol` (`modbus_tcp` in this milestone), `connection` (jsonb: host, port, unit ID, timeout, retries), `enabled` |
| `point` | `id`, `site_id`, `equip_id` (nullable), `space_id` (nullable), `device_id`, `name` (the `<room>_<equipment>_<function>` name, unique on the site), `dis`, `point_type` (`sensor`, `sp`, `cmd`), `kind` (`Number`, `Bool`, `Str`), `unit`, `enum` (nullable), `tags` (jsonb), `address` (jsonb: object type, wire address, data type, byte and word order, scale, offset), `poll_class` (`fast`, `normal`, `slow`), `stale_after_s` (nullable, default 3 times the class interval), `his` (logged or not) |
| `point_alias` | Old names kept after a rename (5.2): `point_id`, `name` |
| `point_his` | **TimescaleDB hypertable** on `ts`: `point_id`, `ts`, `num` (double, used for Number and Bool), `str` (text, for Str), `status` (`ok`, `stale`, `fault`, `down`), `reason` (text) |

- **Tags** are stored as Haystack tags in `tags` (jsonb). Markers are stored as `true`; values (`unit`, refs and so on) are typed. The standard and house libraries (Appendix A) are loaded from a versioned file and used to check tags when config is loaded. Unknown tags are allowed and treated as site custom tags (5.2).
- **Current values** live in an in-memory cache in the backend (value, timestamp, status, reason). They are not written to a table on every poll. **[Assumed]**
- **History rows (decided):** a row is written on every successful poll for points with `his` set, plus one row whenever the status changes (so gaps and their reasons are recorded). Change-of-value storage is not planned.
- **Retention:** a TimescaleDB retention policy drops raw rows older than 30 days (5.7).

## 5. Site configuration file

The site is defined by one file, `config/site.json`. **[Assumed]** It is the first version of the configuration export format (3.1), so standalone mode and the config export reuse it later.

- The format is versioned with `"schema": "devine-bms-config/0"`, with a JSON Schema in the repository. Entities refer to each other by `name`, never by database ID.
- On start-up the backend validates the file and **syncs** it into the database: it creates or updates entities by `name`. Points are never deleted automatically; points missing from the file are marked disabled, and their history is kept. **[Assumed]**
- The example site (`config/site.example.json`) has one site, no spaces, one equipment (`meter1`, tagged `equip ac elec meter siteMeter`), the simulated device, and the five points from section 3. The meter has no room, so its point names use `site` as the room part (for example `site_meter1_power`), and tagged per Appendix A (for example `elec power sensor`).

## 6. Backend

**Structure** (in `backend/`):
- `config/`: load, validate and sync the site configuration file.
- `storage/`: a **storage interface** (load configuration, write history, read history) with a PostgreSQL + TimescaleDB implementation. Standalone mode adds a second implementation later (3.1).
- `drivers/modbus/`: Modbus TCP client (pymodbus, async). Supports function codes 01, 02, 03 and 04, data types bit, int16, uint16, int32, uint32 and float32, byte and word order, scale and offset. Writes (05, 06, 15, 16) are not used yet.
- `poller/`: polling by speed class (5.1.4): Fast every 5 s, Normal every 30 s, Slow every 5 min. **Block reads**: neighbouring registers on one device are read in one request, up to a block size set per device (default 32 registers). One connection per device, reconnecting with back-off after a failure.
- `poller/quality.py`: value quality (5.2.1). **When a device stops answering, every point on it goes Bad (`down`)**, not only the speed class being polled, and the poller retries the connection with back-off (1 s doubling to 30 s). **When the device answers again, every speed class is read at once**, so Normal and Slow points do not keep showing `down` until their next scheduled poll. **Good** on a valid read. **Bad** with reason `down` on a timeout or connection failure, or `fault` on a Modbus exception or a value that cannot be decoded (for example a NaN float). **Stale** when there has been no new value for `stale_after_s` (default 3 times the class interval), checked by a timer.
- `api/`: FastAPI routes (section 7).

**Logging:** structured logs (JSON lines) with levels; one line per connection change, not one per poll.

**Settings** from environment variables or a gitignored `.env`: database URL, config file path, listen address (default `127.0.0.1:8000`), log level.

## 7. API

All responses are JSON. Times are ISO 8601 in UTC; the web app converts them to the site time zone.

| Method and path | Returns |
|---|---|
| `GET /api/health` | Backend status, database connection, devices connected |
| `GET /api/site` | The site with its spaces and equipment |
| `GET /api/points` | All points: id, name, dis, equipment, space, type, kind, unit, tags, and current value with status and timestamp. Filters: `equip`, `space`, `tag` |
| `GET /api/points/{id}` | One point with its current value |
| `GET /api/points/{id}/history?start=&end=` | History rows for the range. Gaps are left as gaps (5.2.1). Over 10,000 rows, the server thins the data to fit by taking the min and max of each time bucket, and the response says so. **[Assumed]** |
| `GET /api/points/{id}/history.csv?start=&end=` | The same range as CSV, with the time in site local time, the point name and its unit in the header (5.7) |
| `WS /api/live` | The client subscribes to point IDs and receives value, status and timestamp changes as they happen. On connect it receives the current values. |

An OpenAPI description is published by FastAPI at `/docs`.

## 8. Web app

In `web/`, React + TypeScript + Vite. Light and dark themes (6.1); branding and logins come later.

- **Points page:** a table of all points with name, display name, equipment, value with unit, status, and last update time, updated live over the WebSocket. Bad and Stale values are greyed, show the reason, and are never shown as a live number (5.2.1). A tag filter box.
- **Trend page:** pick one or more points (up to 4) and a range (last 1 hour, 24 hours, 7 days, 30 days, or custom). The chart shows the history, extends live while it is open, shows gaps as gaps, and uses a separate axis per unit. **Export CSV** downloads the plotted range. **Chart library (decided): uPlot**, chosen for speed with long time series and its small size, which also suits the Pi.
- Usable on a phone, but laid out for desktop first (6.1: trends are desktop-first). On a phone the points page shows the point, value and status only, so values are never off-screen.

## 9. Tests

- **Unit tests (pytest):** decoding of every data type with each byte and word order, scale and offset (including the register map in section 3); value quality rules, including the Stale timer and the down and fault reasons; config validation (good file, bad references, duplicate names, unknown tags treated as custom); block-read grouping.
- **Integration tests (pytest):** start the simulator and a test database (TimescaleDB), run the backend, and check that:
  1. all five points are Good within one poll cycle and their values match the simulator;
  2. history rows are written and returned by the history API;
  3. when the simulator stops answering, the points go Bad (`down`) within the timeout plus retries, and the history records the status change;
  4. when a point gets no new value within `stale_after_s`, it goes Stale. A device that keeps answering with an unchanged value is still Good (its readings are new, even if the number is the same); Stale means no reading arrived in time, for example when polling falls behind. The test sets a stale time shorter than the poll interval;
  5. when the simulator returns a Modbus exception, the points go Bad (`fault`);
  6. after the simulator recovers, the points return to Good and polling resumes without restarting the backend.
- **Web test (Playwright):** the points page shows live values, and the trend page draws a chart and downloads a CSV.
- **Soak run (manual):** 24 hours against the simulator with no missed polls beyond the configured retries, and steady memory use. This mirrors bench test 1 (section 16) before real hardware.

## 10. Done when

- A developer can follow the README to start the database, simulator, backend and web app on one machine and see live values and a trend within 10 minutes. **[Assumed]**
- All tests in section 9 pass, and the 24-hour soak run passes.
- Live values reach the web app within 1 to 5 seconds of a poll (section 17).
- The schema, config format and decisions made during the build are recorded in `docs/decisions/`, and the main spec is updated where they differ.

## 11. Developer setup (decided: native install)

Developers install **PostgreSQL + TimescaleDB natively** on their machine. **There is no Docker anywhere in the project**, in development or on sites (section 11 of the main spec).

- The README gives install steps for each supported developer OS, with the PostgreSQL and TimescaleDB versions pinned to match what sites will run. The developer OS list follows section 13 of the main spec (still TBC).
- A script `deploy/dev/setup-db.sh` creates the development database, user and TimescaleDB extension, reading the password from `.env`. **[Assumed]**
- The simulator runs as a normal Python process.
- Claude Code cloud sessions and any CI runner install the same packages with a setup script. **[Assumed]**
- A Proxmox `dev` box that mirrors a site (section 13) is still used later for deployment testing, not for day-to-day development.

## 12. Decisions for this milestone

| # | Question | Decision |
|---|---|---|
| 1 | Simulated device | A Modbus TCP power meter (section 3) |
| 2 | Database access | SQLAlchemy 2 (async, asyncpg) and Alembic migrations (section 4) |
| 3 | Chart library | uPlot (section 8) |
| 4 | Developer setup | Native PostgreSQL + TimescaleDB install; no Docker anywhere (section 11) |
| 5 | History storage | Every poll, plus a row on each status change (section 4) |
