# Devine BMS: Specification (v0.4 draft)

Single reference for the developer (Jason) and for Claude Code. Read this first in every session. Written from the planning Q&A on 31 Oct 2026; v0.4 updated 1 Nov 2026 (Haystack 4 compatibility, tags, relationships, point types, naming, template roll-out and graphics, site logic diagrams, HTTPS in the Tailscale container).

Items marked **[Assumed]** were inferred and need confirming. Items marked **[Open]** are undecided.

## 1. Overview

The Devine BMS is a **building management system**. It monitors and controls HVAC/refrigeration, power metering, solar/battery and lighting/general I/O across sites of roughly 100 to 1,000 points each.

Each site is **standalone**: a site server holds that site's data, users and logic, and serves the dashboard. Small sites can instead run **Standalone mode**, where the Pi HMI is the whole system (see 3.1). There is **no cloud portal**. Remote access is the site dashboard reached over **Tailscale**.

Audience: **Border to Border clients first, and it could be sold on to other businesses.** Per-site branding is supported from the start.

Because it may be sold, design with these in mind from the start (all **[Assumed]**):
- **Installable by others:** commissioning, updates and recovery must work without the developer on site (clear install guide, provisioning script, health checks).
- **Documentation:** installer guide, user guide, and a hardware compatibility list.
- **Licensing and ownership:** **proprietary software with no licence keys in v1 (decided).** The code stays private and each site is sold or installed as a system, with no key checks in the software. Keys tied to a site can be added later without redesign. Support terms and warranty are still to be decided before the first sale. **Before the first sale, check the licence of every component for commercial use**, including `bacnet-stack`, React Flow, TimescaleDB, Proxmox VE, Mosquitto, the Project Haystack defs (Academic Free License 3.0) and any Haystack libraries, the Python libraries (for example pymodbus and bacpypes3) and the Tailscale terms for business use, and record the result in the repository. **[Open]** until that check is done. Found so far: `bacnet-stack` is GPL-2.0-or-later with the GCC exception (linking into proprietary code allowed; its own files must stay open if changed and distributed); `bacpypes3` and `aiomqtt` are BSD-licensed; `paho-mqtt` is EPL-2.0 or BSD-3-Clause (BSD applies); the Project Haystack defs are under the Academic Free License 3.0; TimescaleDB's retention and compression features are under the Timescale License (allowed on your own servers, not for offering TimescaleDB as a hosted database service).
- **Security and updates:** a clear update and patch process, and no shared default passwords.
- **Support access (decided):** remote support through Tailscale rests on **standing consent in the customer's contract**, agreed when the system is installed. The platform admin account stays available, but **every support login is written to the audit log (5.6) and is visible to the customer's admin**, with the user, time and what was changed. The customer can ask for support access to be removed, and the access policy is part of the customer documentation and terms. **[Assumed]** that standing consent suits your customers; confirm the contract wording before the first sale.
- **BACnet (decided):** the BMS is described as a BACnet/IP and MS/TP **client that works with the devices on the supported hardware list (section 20)**, and nothing more. **There is no claim of BACnet certification, listing or conformance** unless it has been tested for that purpose. Wording for brochures and quotes is checked before a sale, and BTL testing is revisited only if a customer asks for it. **[Assumed]** that this wording is sufficient; check with BACnet International's rules before using the BACnet name or logo in marketing.
- **Safety and liability:** keep the rule that the BMS is never the only protection, and record it in customer documentation and terms.

Status: **planning / pre-build.** The stack in section 4 is confirmed, but native MS/TP and HMI failover must be proven on the bench before building on them.

## 2. Architecture and principles

```
 Field devices (meters, controllers, sensors, I/O)
   Modbus RTU/TCP | BACnet/IP | BACnet MS/TP | MQTT | vendor APIs
          |  (protocol drivers)
          v
   Site server (Proxmox)  <---->  Database (time series + config)
     |        |
     |        +--> API (REST/WebSocket)
     |                 |
     |                 +--> Site web UI (over Tailscale for remote users)
     |                 +--> Pi HMI (local kiosk)
     +--> Alarm engine --> email, SMS gateway, ntfy push

   Pi HMI also has its own BACnet connection for standby takeover
   if the site server fails.
```

- **Backend is the source of truth** while the site server is healthy. The web UI and HMI are clients of the same API.
- **Local-first.** The site server and HMI work with no internet. Remote access, SMS and push need internet.
- **One UI codebase.** The HMI runs the same web UI in a kiosk browser with a reduced layout.
- **Safety.** The BMS is never the only protection for equipment or people. Hardware interlocks and safety circuits stay independent of software.

## 3. System components

| Component | Description |
|---|---|
| Site server | Industrial/mini PC at each site running Proxmox. Runs the backend, database, protocol drivers, control logic, alarms and the web UI. Holds that site's data and user accounts. |
| Pi HMI | Raspberry Pi with a screen at the site. Operator interface, and standby controller if the site server fails. |
| Remote access | Tailscale. Users reach a site's dashboard by joining the tailnet. Tailscale ACLs decide who can reach which site. |
| Field devices | Modbus RTU/TCP, BACnet/IP, **native BACnet MS/TP (RS485)**, MQTT and vendor-API devices (meters, controllers, inverters, VRF, sensors, I/O). |

Because there is no central service, a user who needs several sites logs in to each site dashboard separately (accounts are per site).

### 3.1 Deployment modes (chosen at install) (decided)

The mode is chosen once by the provisioning script (`--mode full` or `--mode standalone`) and can be changed later by re-provisioning and importing the site configuration. **Storage (decided):** full mode keeps configuration and history in PostgreSQL + TimescaleDB; standalone mode keeps its configuration in **one file in the same format as the configuration export**, so the backend has a storage layer with two implementations behind one interface. Items inside this section still marked [Assumed] or [Open] remain so.

| | **Full mode** (default) | **Standalone mode** (small sites) |
|---|---|---|
| Hardware | Proxmox site server plus a Pi HMI | A Pi with a touch screen only |
| Who runs the backend | Site server | The Pi itself (the same Python backend in a light profile) |
| Pi HMI role | Client of the server, with standby takeover | The whole system |
| Database | PostgreSQL + TimescaleDB | None for history. Live values and the log are held in memory. Settings are a small config file. |
| Protocols | Modbus RTU/TCP, BACnet/IP, native MS/TP, MQTT, vendor APIs | Modbus RTU/TCP, BACnet/IP and MS/TP through USB RS485 adapters. MQTT and vendor APIs are optional extras. |
| Scale | Up to 1,000 points | Small, about 50 points (to be confirmed) |
| Screens | Overview, system graphics, setpoints, schedules, trends | Site overview graphic with a tab for each room or section, overrides, basic schedules, temperature and power log (see 6.5) |
| Control logic | Visual block editor | **The same visual block editor and v1 blocks, used from the web app that the Pi serves** (a laptop or phone on the LAN, or over Tailscale). Size limits to be confirmed on the bench. The touch screen shows logic state and variables but does not edit logic. |
| Alarms | Email, SMS gateway, ntfy | On-screen, plus optional ntfy and email |
| History | 30 days / 1 year / 5 years tiers | **Memory only** temperature and power log (a rolling window in RAM), with optional scheduled export to a USB stick, plus CSV download on demand. Without the USB export it is lost on reboot or power loss. |
| Remote access | Tailscale on the server | Optional Tailscale on the Pi |
| Resilience | Server failure covered by HMI takeover | No standby. Equipment relies on its own controllers. |
| Upgrade path | n/a | Export the site configuration and import it into a full-mode server |

- **One codebase.** Standalone mode reuses the HMI direct-mode engine (section 9) and the same UI, symbols and graphics. The only differences are the light backend profile and the reduced feature set.
- **Same configuration format** in both modes, so a standalone site can be upgraded by importing its configuration. History is exported as CSV and is not migrated automatically.
- **Setup and access.** **All setup happens in the web app (decided).** On first boot, with no configuration, the touch screen shows a setup page with the Pi's network address, a QR code and a **one-time setup code**. The installer opens that address from a laptop or phone on the same network, enters the code, and the web app guides them through the mode, network settings, time zone and the first admin account (sections 5.8 and 7). The code stops working once the admin account exists. **First-boot network (decided): the Pi tries DHCP for about 30 seconds, and if no address is given it uses a fixed fallback address** (proposed 192.168.50.10), which is shown on the setup page. The installer sets their laptop to the same range to reach it. Once set up, the network settings (fixed address or DHCP) are changed in the web app. **[Assumed]** After setup, the web UI is reachable from a laptop or phone on the LAN (or over Tailscale if enabled). Local accounts and PIN rules are the same as full mode. In full mode the provisioning script prints the setup code and the HMI shows the same setup page. **[Assumed]** The touch screen is for operating the site, not for configuring it.
- **No logging to the SD card.** To preserve the card, the log and live values live in RAM only (a ring buffer, for example the last 24 to 72 hours at one sample a minute, which is a few megabytes for 50 points). Nothing is written to the card during normal running. History is lost when the Pi reboots or loses power unless the USB export is on, and the screens say so.
- **What is written to the card.** Only the site configuration (devices, points, graphics, accounts, settings), and only when someone saves a change. Raspberry Pi OS runs with a **read-only root filesystem (overlay)**, with the config on a separate partition that is mounted writable just long enough to save. System logs go to RAM.
- **After a reboot or power loss** the Pi starts from saved configuration. Temporary state is not restored: hand and override modes return to Auto, and the log starts empty (the log is reloaded only if restore on boot is on). **Decided:** overrides return to Auto after a power loss and are not saved to the card.
- **Scheduled USB export (option).** A USB stick can be left plugged in. On a schedule (for example hourly or daily, set in the settings) the Pi appends the new log rows from RAM to CSV files on the stick, then syncs and unmounts it. This gives long-term history without writing to the card. **[Assumed]**
  - Files are split by month (for example `2026-11.csv`), with a header row, timestamps in site local time, and the point name and unit on every column.
  - The Pi mounts the stick only for the write, so pulling it out between writes is safe. If the stick is missing, full or fails, the screen shows a clear warning and the log keeps filling RAM. The next export catches up on anything still in the window.
  - Anything logged since the last export is lost on power loss, so the export interval sets how much you can lose.
  - Optional **restore on boot:** after a reboot the Pi reloads the most recent hours from the stick into RAM so the trends continue. **[Assumed]**
  - USB sticks wear too, so use a quality or industrial stick and an hourly interval at most. A stick is also the way to get history off a site, along with the CSV download.
- A site that needs more than this should move to full mode.
- **Backup.** Configuration export to USB, plus optional off-site config backup as in section 10.
- **Safety.** The same rule applies: the BMS is never the only protection for equipment or people.

## 4. Stack (confirmed)

| Area | Choice | Why |
|---|---|---|
| Site server OS | Proxmox VE on the mini PC, services in LXC containers (Debian LTS). No Docker. | Snapshots for rollback, built-in backups, web management |
| Backend | Python 3.12 + FastAPI, asyncio | Good libraries: `pymodbus`, `bacpypes3`, `aiomqtt` |
| BACnet MS/TP | C-based `bacnet-stack` 1.6.1 (pinned), in the `bms-mstp` service | Supports native MS/TP over serial; built and tested on a virtual trunk (milestone 4c) |
| Database | PostgreSQL + TimescaleDB | Time-series with tiered retention and rollups |
| Message bus (internal) | MQTT broker (Mosquitto) | Simple, also used for field MQTT devices |
| Web UI | React + TypeScript, Vite | Good for the graphics editor and logic editor |
| Graphics editor | SVG-based, bound to points | Scales to HMI screen and phones |
| Logic editor | Visual node-and-wire block editor using **React Flow** | Chosen authoring method for control logic; suits signal flow and PID |
| Logic blocks (v1) | Read and write (point read, point write, variable, schedule), logic (AND, OR, NOT, compare with optional deadband), timing (delay on, delay off, minimum on/off), control (PID, manual/auto); variables with site, equipment or diagram scope (5.3.1) |
| Standalone logic | Standalone mode has the same block editor in the web app served by the Pi; the touch screen shows state and variables only (5.3.1) |
| Logic import and export | Plain documented JSON, paste into the canvas or import dialog, validated before creation, always a draft; no code block in v1; admin role, audit-logged (5.3.2) |
| Logic editing | Delete by key, button or right-click; warning when deleting an output or feeder; undo and redo; drafts autosave and published versions can be restored (5.3.1) |
| User roles | Three roles per site: viewer, operator, admin, with a permission table (section 7); write limits apply to every role |
| Time source | Site server is the time master: internet time when available, own clock otherwise, manual set by admin; Pi follows the server; UTC stored, site time zone shown (5.8) |
| Alarm severity | Three levels: Info (screen only), Warning (screen, email, ntfy), Critical (all channels including SMS); channels per level editable per site (5.5) |
| Alarm recipients | A site recipient list with a tick per level; Critical cannot be muted; optional quiet hours for Warning only, sent as a summary; test message and delivery log (5.5.1) |
| Diagnostics | Health page, support bundle with secrets removed, and a read-only live traffic viewer for the BMS's own messages (admin only, limited buffer); whole-bus listening is an option (5.9) |
| Audit log retention | Kept 1 year, then deleted automatically; admin viewer with filters and CSV export; cannot be edited from the app (5.6) |
| Value quality | Every value is Good, Bad or Stale (default 3 times poll interval); shown as Offline on graphics, gaps in trends, fallback or fail-safe in logic (5.2.1) |
| Maintenance counters | Run hours and start counts for any equipment with a run state; service reminders by hours or starts as alarms; admin marks service done; device counters used where reported (5.10) |
| Login rules | Passwords of 10 or more characters, 30 minute web session timeout, 5 minute HMI PIN lock, lockout after 5 failures for 10 minutes, optional 2FA (admin can require it), always-on rate limiting; admin can change them (section 7) |
| First setup | All setup is in the web app; first boot shows the address, a QR code and a one-time setup code on the touch screen; the code stops working once the admin exists (3.1) |
| Software licence | Proprietary, no licence keys in v1; keys can be added later; component licence check recorded before the first sale (section 1) |
| Phone priority | Overview and alarms, and the Graphics tabs with tap-to-control are designed for phone first; trends are desktop first; editors and setup screens are desktop or tablet (6.1) |
| Polling | Speed classes per point (Fast about 5 s, Normal about 30 s, Slow about 5 min), block reads, BACnet COV where supported with a slow poll as back-up, bus load shown on the health page; Slow is delayed first when a bus is busy (5.1.4) |
| Bench plan extension | Only the highest risks added: logic fail-safe (17), polling load (18) and standalone limits with logic (19); other new features are tested during the build (section 16) |
| First-boot network | DHCP for about 30 seconds, then a fixed fallback address shown on the setup page (3.1) |
| Haystack | Data model follows Project Haystack 4 (standard library plus a house def library); read-only Haystack HTTP API (read, nav, hisRead, watch) with SCRAM login, off by default (5.2, 5.12) |
| BACnet server role | Read-only in v1, so another BMS can read Schedule, Calendar and Trend Log (and chosen points); off by default per point (5.11) |
| BACnet claims | Described as a client that works with the supported hardware list; no certification or listing claim; wording checked before a sale (section 1) |
| HTTPS for remote users | Handled by `tailscale serve` in the `bms-tailscale` container; `bms-web` serves plain HTTP only (sections 8 and 11) |
| Remote support consent | Standing consent in the customer contract; every support login is audit-logged and visible to the site admin; access can be removed on request (section 1) |
| Local HTTPS | HTTP on the site LAN and HTTPS over Tailscale; LAN web access restricted and rate-limited; optional LAN HTTPS later (section 8) |
| Future ESP32 I/O module | Room left in the spec only: it is a standard Modbus or BACnet device added through templates, with identity, configurable address and safe outputs; no BMS code changes; hardware, firmware and compliance out of scope for now (section 20, F1) |
| Added logic blocks | Limit (with optional rate limit), scale (linear range conversion with clamp), and astro time switch (sunrise and sunset with offsets, calculated locally from site latitude, longitude and time zone) (5.3.1) |
| Variables on the HMI | Variables published as points can be changed from the HMI by a logged-in operator within limits, audit-logged; not adjustable in direct mode (5.3.1) |
| Logic fail-safe default | Release to device; a block can choose hold last value or a stated value (5.3.1) |
| HMI | Raspberry Pi 4 or 5 running **64-bit Raspberry Pi OS only** (32-bit is not supported), Chromium in kiosk mode loading the site UI (HMI layout) | Reuses the web UI; one build target with ready-made 64-bit Python packages |
| Remote access | Tailscale | Chosen |
| Alarms | Email over SMTP, SMS through ClickSend (swappable plug-in), ntfy push (server is a site setting, default ntfy.sh) | Chosen (milestone 6) |
| Tests | pytest, Playwright, device simulators | See section 16 |

**BACnet stack (native MS/TP required):** `bacpypes3` covers BACnet/IP only. Native MS/TP uses `bacnet-stack` (open source), running as a service **on the Proxmox host** and exposing a local API (or MQTT) to `bms-backend`. BACnet/IP can stay on `bacpypes3`. See section 11 for the host-service trade-offs.

Confidence in the stack: medium-high. Main risks: native MS/TP timing, the size of the visual logic editor, and HMI failover.

## 5. Functional requirements

### 5.1 Device and point management
- Serial-to-Ethernet gateways: a Modbus RTU line can be reached through a gateway on the LAN. The device is added as a Modbus TCP connection (IP, port, unit ID), or as "RTU over TCP" where the gateway passes raw RTU frames. The template records which framing, the unit IDs behind the gateway, and the gateway's connection limit. Polling is paced because the gateway serialises requests onto the serial line.
- Protocols: Modbus RTU, Modbus TCP, BACnet/IP, **BACnet MS/TP (native, over RS485)**, MQTT, and vendor APIs through pluggable drivers.
- **MS/TP requirements:**
  - Act as an MS/TP master node on the trunk, with a configurable MAC address (0 to 127), baud rate (9600 to 115200) and max masters/info frames.
  - Discover devices on the trunk (Who-Is / token-passing node scan), read objects and properties, write to writable objects with priority arrays, and support COV where devices allow.
  - Multiple MS/TP trunks per site, each on its own RS485 port.
  - Show trunk health: token passing, retries, CRC errors, devices offline.
- **BACnet router (v1):** the BMS routes between MS/TP trunks and BACnet/IP, so other BACnet/IP tools on the device network can see MS/TP devices. Requires a documented BACnet network-number plan per site. **Built in milestone 8 inside bms-mstp (decision 024): each routing trunk has its own labelled IP address on the device network; network numbers are site settings (BACnet/IP default 1, trunks 101 upwards), checked for uniqueness.** Prove it on the bench (test 6).
- **Serial ports are never shared:** each RS485 bus (a Modbus RTU line or a BACnet MS/TP trunk) has its own dedicated adapter and port.
- **Auto-discovery plus UI:** scan the network (BACnet Who-Is, Modbus range scan, MQTT topic discovery), list what is found, then the user confirms, names, scales and tags points in the browser. The Discover screen is described in 5.1.3.
- Reusable **device templates** (for example a specific inverter or meter) applied per device. **[Assumed]** Reusable **equipment templates** (5.1.2) turn device points into equipment, with a shared graphic and settings.
- **Add and configure devices from the settings screen, with no code changes.** A person with the right role can add a device such as the CWT-MB328B(II) entirely in the UI, in both full and standalone mode:
  1. **Connection:** pick the bus or network (serial port with baud, parity, data bits and stop bits, or an IP address and port), the Modbus slave ID or the BACnet device ID, the timeout, retries and polling interval.
  2. **Point map (device template builder):** add points one by one or from a table, each with a name, Modbus object type (coil, discrete input, holding register or input register), address, data type (bit, 16 or 32-bit integer, float), byte and word order, scale and offset, units, read-only or writable, and write limits. The UI shows both the wire address and the manual-style address (for example 00001), with a one-click choice of 0-based or 1-based.
  3. **Save as a template** (named, versioned, exportable and importable as a file) and reuse it for any number of devices of that type. Built-in templates ship for supported hardware (section 20).
  4. **Test tools:** read and write a single point, run a read of the whole map, and see the raw request and response, errors and timing. Writes in the test tool follow the same write limits and audit log (5.6).
  5. **Bulk create:** generate named points from a pattern (for example `DI0` to `DI7`, `DO0` to `DO17`).
- **Configure the device itself through the BMS (where the device allows).** Where a device exposes its settings over the bus (for example slave ID, baud rate or IP address in Modbus registers), a template can include **configuration points** that are written from a settings page, with a clear warning and a confirm step because changing them can take the device off the bus. Devices that can only be configured by their vendor tool or DIP switches are listed in section 20 with that noted.
- **Scan and identify:** the Modbus scan tool checks a range of slave IDs or IP addresses, shows what answers, and flags **duplicate IDs or addresses**, which matter because many units ship with the same default.
- Per-point settings: units, scaling, polling interval, read-only or writable, min/max for writes.

### 5.1.1 Commissioning: add devices, map to rooms, tag points (decided)
Adding a device is a **4-step guided procedure** in the web app (served from the Pi in standalone mode). Each step can be saved and resumed, and nothing goes live until step 3. No code changes at any step. Templates (5.1.2, Appendix A) fill in almost everything, so the installer mainly checks and corrects.

1. **Add the device and test it.** Pick a built-in or saved device template (section 20) or start a blank one. Enter the connection (5.1), then **Test connection**: read the whole point map and show pass or fail per point with the raw response. For BACnet, run Who-Is and choose from the list found (or arrive here from Discover, 5.1.3).
2. **Create equipment from a template, in one table.** Pick the equipment or space template (5.1.2). The BMS creates the points, groups them into equipment and binds the roles, then shows **one editable table** with a row per point and columns for equipment, room, point name, display name, tags, units and scaling:
   - **Bulk create:** where a device presents many similar items (a Mitsubishi gateway with up to 50 groups of about 20 objects, or an I/O module with 8 inputs), the installer gives the repeat range (for example groups 1 to 12) and gets one instance per group, so nobody types hundreds of points. **Split points into equipment** (drag or multi-select) covers a device that serves several pieces of equipment, such as one I/O module driving lights in three rooms.
   - **Room:** equipment and individual points can each be given a room or section (a **location**); a point inherits its equipment's room unless set directly. Rooms can be created here by name (for example Meeting room, Plant room, Store) and are the same rooms drawn on graphics (6.4) and shown as tabs (6.5). Room names can also be imported from a spreadsheet.
   - **Names and tags** are filled in from the pattern `<room>_<equipment>_<function>` (5.2) and the template's tags (Appendix A). The installer can edit them or apply changes to many rows at once. Duplicate names are flagged.
3. **Verify and go live.** Limits, alarms and logging come from the template defaults (write limits and override timers 5.6, alarm limits and delays 5.5, logging rates 5.7). This step shows them as a **checklist** with any changes from the defaults, alongside the checks: points with no room, untagged points, duplicate names, points not communicating, and writable points with no limits. Any of them can be edited here. **Go live** is blocked until errors are fixed; warnings can be accepted. The change is audit-logged (5.6) and the configuration is versioned so it can be rolled back (10).
4. **Place on graphics (optional).** Choose where each piece of equipment appears: **add to a room tab**, **add to the Site tab**, or skip. The BMS drops a copy of the template graphic or matching symbol with its points bound (5.1.2), and the user arranges it in the graphics editor (6.1). This can be skipped and done later in the graphics editor. Rooms with no graphic yet get a default tab with their points listed.

**Other commissioning tools:**
- **Room mapping table.** A single table of every point or piece of equipment, with room, tags and graphic binding columns, editable in place, filtered by device, room or tag, and exportable and importable as a spreadsheet file (CSV) so a site can be set up in bulk offline.
- **Unassigned list.** Anything discovered or created that has no room is listed so it cannot be forgotten.
- **Replace a device.** Swap one device for another of the same template and keep its points, tags, rooms and history; only the connection changes.
- **Site setup export.** Devices, points, rooms, tags and graphic bindings export as one file (config export, 3.1) and import onto another site or as a starting template. History is not included.
- **Standalone mode** uses the same procedure with a smaller limit on points (3.1). The web app runs from the Pi; the touch screen cannot add devices.

**Data model:** equipment and points can carry an optional **location** (room or section), and rooms can hold points directly (5.2). Tags come from the Haystack and house libraries (5.2, Appendix A), with suggested tags shipped per template.

### 5.1.2 Equipment templates (decided)
An **equipment template** describes a type of equipment once (for example Indoor unit, AHU, Room, Pump, Light circuit). Equipment is then created from it many times, and only the differences are entered per instance. This is separate from a **device template** (5.1), which describes a device's registers or objects. A device template says what a Mitsubishi gateway offers; an equipment template says what an indoor unit is, and which gateway points feed it.

**A template holds:**
- **Point roles:** named points the equipment should have (for example `room_temp`, `setpoint`, `on_off_cmd`, `on_off_state`, `fault`), each with a point type (sensor, sp or cmd, 5.2) and value kind, units, tags, write limits, alarm defaults, logging rate and display name pattern. A role can be **required** or **optional**.
- **Graphic (optional):** a system graphic or symbol group bound to the roles, not to specific points (6.4). It is drawn once and used as the starting point for each instance's graphic (see Graphics and templates below).
- **Control logic (optional):** a block diagram (5.3) that refers to roles, so the same logic runs on every instance.
- **Parameters:** values set per instance, such as the display name, room, setpoint range, and schedule group.
- **Default hand-mode timer and override rules** (5.6).

**Creating equipment from a template (an instance):**
1. Pick the template and enter the instance name and room.
2. **Bind each role to a device point.** The wizard suggests a match from the device template (for example Mitsubishi member 09 for `room_temp`) and the user confirms. For gateways with numbered groups (H5, H6) the **bulk create** option makes one instance per group, binding by the instance-number pattern, with names typed or imported from a spreadsheet.
3. **Adjust the finer detail per instance** without changing the template: rebind a role to a different point, change scale or limits, disable an optional role, or **add extra points** that exist only on this instance (for example a second temperature sensor). Each change is marked **overridden** in the instance.
4. The instance gets its tags, limits, alarms, logging and graphic from the template, then the commissioning procedure in 5.1.1 continues (verify and go live, then optionally place on graphics).

**Changing a template later:**
- Editing a template shows how many instances use it, and a **preview of changes** per instance.
- Changes apply to every instance **except the settings an instance has overridden**. Graphics are the exception: placed graphics are independent copies and are not changed (see Graphics and templates below). **An override always wins** (decided): it stays as it is, and the instance lists its overrides so they can be reviewed or reset to the template value one at a time.
- Templates are **versioned**. An instance records which version it uses. Rolling back or applying an update is a deliberate action, audit-logged (5.6), and part of the config backup (10).
- **Rolling out a new version (decided).** Publishing a template version shows a **side-by-side difference** from the previous version (roles, tags, limits, alarms, graphic and logic) and lists the instances it affects. The admin then updates **all instances, a chosen group (by room, tag or list), or one at a time**. Instances not yet updated keep running on their current version, and the template page shows how many are on each version. This lets a change be tried on one unit first. Overrides still win (above).
- **Unlink** turns an instance into plain equipment that no longer follows the template.

**Graphics and templates (decided: copied per placement).** Placing a template graphic for an instance (in the commissioning wizard, 5.1.1 step 4, or in the graphics editor) makes an **independent copy** on that room or site graphic. The copy can be edited freely on that graphic: move, resize, add extra symbols for points only that instance has, or remove parts. **Template graphic changes do not reach existing copies**; they apply to new placements only. **[Assumed]** The copy stays bound to its instance by **role**, so rebinding a role in the instance (5.1.2) updates the copy without redrawing it. The graphics editor offers **Replace from template** on a copy, which swaps in the current template graphic after a warning that local edits will be lost. Rooms (6.4) can carry a template graphic in the same way.

**Built-in starter templates** (decided list): indoor unit (Mitsubishi H5, H6), AHU, **room (a space template, below)**, pump, fan, **exhaust fan**, **ERV** (energy recovery ventilator, including Mitsubishi Lossnay), light circuit, and **power meter** (power and energy consumption). Device-level starters also ship for supported hardware: relay outputs (H1, H2) and temperature and humidity sensors (H3, H4). Generic valve and damper templates are included. A site can copy and edit them, and export or import them as files (also between sites).

**Standalone mode** supports equipment templates too, within the point limit in 3.1.

### 5.1.3 Discover and add devices **[Assumed]**
A **Discover** screen finds devices on every network the site uses and lets the user add them with a click. It works for BACnet/IP, BACnet MS/TP and Modbus RTU/TCP, in both full and standalone mode.

**Scanning:**
- One **Scan** button per bus or network. Results appear live as devices answer, with a progress bar and a Stop button.
- **BACnet/IP:** Who-Is broadcast on the device network, optionally across routed networks. Each result shows device ID, IP address, vendor, model name and firmware where the device reports them.
- **BACnet MS/TP:** a token-passing node scan of one trunk at a time. Each result shows MAC address, device ID, vendor and model. The trunk health (5.1) is shown while scanning.
- **Modbus TCP:** a scan of an IP range and port 502. **Modbus RTU:** a scan of a range of slave IDs on one serial port at a chosen baud rate and parity. A hit means that something answered at that address. Where a device supports the Modbus device identification request, its vendor and model are shown; otherwise the row says **Unknown device** and the user picks the template.
- **Safety:** a scan limits itself to a chosen range, shows the expected traffic, and asks for confirmation before it starts on a live serial bus. Scans never write to a device. The scan is logged (5.6). **While the HMI has taken over control (section 9), scanning is allowed but shows a warning** that it adds bus traffic during manual control and asks for confirmation. It is not blocked. **Decided.**
- Results can be saved as a **scan snapshot** and compared against the last scan to show new, changed and missing devices.

**Matching and adding:**
- **Template suggestion:** a result is matched against the built-in and saved device templates by vendor, model and, for BACnet, an object signature. A confident match shows the template name with a tick. Several possible matches show a short list. No match shows **Choose template**.
- **Add:** click a row to add it, or tick several rows and use **Add selected**. **Adding several at once is decided.** Each selected device is tested in turn, rows that pass are added, and rows that fail or have a clash stay in the list with the reason. Devices that need a template chosen by hand can't be added in a batch until one is picked. Adding opens the device in the connection step of 5.1 with the address already filled in, runs **Test connection**, then carries on into the equipment template wizard (5.1.2) for creating equipment.
- **Status per row:** New, Already added, Address clash (two devices at one ID or IP, which many units do by default), or Added but not answering.
- **Fix a clash from the screen:** where a template includes configuration points (5.1), an address clash row offers **Change address**, with the warning and confirm step.
- **Manual add:** an **Add by address** button covers devices that scans can't see (a different subnet, a gateway, or a device with scanning disabled).
- **Gateway devices** (for example Mitsubishi H5, H6) add their units in one step: the device is added once, and the units behind it are created with the bulk create step in 5.1.2.

**Standalone mode:** the same screen, run from the web app on the Pi. The touch screen can't scan or add devices.

#### 5.1.4 Polling (decided; exact intervals proven on the bench, open item 36)
- **Speed classes per point (decided).** Each point in a device or equipment template (5.1, 5.1.2) has a speed class, which a site or instance can override:

| Class | Default interval | Typical use |
|---|---|---|
| Fast | about 5 seconds | Temperatures, run states, fault flags, setpoints being controlled |
| Normal | about 30 seconds | Humidity, valve and damper positions, power |
| Slow | about 5 minutes | Energy counters, filter signs, run hours, configuration values |

- **Block reads:** neighbouring Modbus registers are read in one request, and BACnet points in one ReadPropertyMultiple request, where the device allows it. The template records the safe block size for each device.
- **COV where supported:** BACnet devices that support change-of-value notification are subscribed to for their Fast and Normal points, with a slow poll as a back-up in case a notification is missed. Modbus has no COV and is always polled.
- **Bus budget:** the BMS estimates the time each bus needs for its points (using the device's measured response times) and shows the load on the device health page (5.9). If a bus cannot keep up, **the Slow class is delayed first, then Normal, and Fast last**, and the page warns and names the devices responsible. A command is always sent ahead of polling.
- **Stale time** follows the point's class (5.2.1: 3 times its interval).
- **Standalone mode** uses the same classes, with a longer default for Slow points to save CPU.
- The point count and bus load for the sizing targets in section 17 are proven on the bench (section 16).

### 5.2 Data model
Hierarchy plus tags:

```
Site > Equipment > Point
Site > Room (space) > Point   (room sensors with no equipment)
```

Equipment can be created from an **equipment template** (5.1.2), which defines its point roles, tags, limits, alarms and graphic. Points keep a link to their template role until an override is made.

- Customer name is a site attribute (for branding and reports), not a separate tenant level, because each site server is standalone.
- Each point has tags (for example `discharge`, `air`, `temp`, `sensor`; Haystack calls supply air `discharge`) used for search, grouping, reports and binding graphics.
- **Haystack compatible (decided).** The data model follows **Project Haystack 4**, so every site, room, piece of equipment and point is a valid Haystack entity and can be read by Haystack tools (5.12). The mapping is:

| BMS item | Haystack entity and tags |
|---|---|
| Site | `site` with `dis`, `tz` (for example `Sydney`), `geoCoord` (also used by the astro time switch, 5.3.1), `area` |
| Room or section (location) | `space` with `siteRef` |
| Equipment | `equip` plus its type markers (for example `ahu`, `fanCoilUnit`, `pump`, `meter`, `elec`), with `siteRef` and `spaceRef` |
| Point | `point` plus `sensor`, `sp` or `cmd`, with `siteRef`, `equipRef`, `spaceRef`, `kind`, `unit`, and `his` when logged, `writable` when writable |
| Hidden permanent ID (renaming, below) | `id` (a Haystack Ref), with `dis` as the display name |
| Value quality (5.2.1) | `curVal` and `curStatus`: Good is `ok`, Stale is `stale`, Bad from a device fault is `fault`, Bad from no communication is `down`; disabled points are `disabled` |

- **Tag vocabulary (decided):** tags come from the **Haystack 4 standard library**. Where Haystack has no tag for a meaning, the BMS adds its own in a **house def library** (a Haystack 4 library with its own namespace, for example `devine`), so house tags are valid Haystack too. Both are versioned files in the repository, shipped with the software and used by templates (5.1.2), Discover (5.1.3) and the mapping table (5.1.1). Tags are picked from a menu. Equipment templates name their Haystack equipment type, and the template checker warns when a required tag is missing. **Version (decided): Haystack 4**, pinned to **haystack-defs v4.0.0** (the `ph` and `phIoT` libraries; build dated 7 July 2025), recorded in the repository (Appendix A). The newer Xeto-based Haystack format is not used in v1; because its tag and API names are largely the same, moving later is expected to be mainly a library change. The tag sets for the starter templates and the house library are drafted in **Appendix A** and finalised before the database schema is built.
- **Custom tags (decided):** a site admin can add site-only tags. They are marked **custom** in the menu and the mapping table, and can be used for search, grouping, trends and graphic binding on that site. Templates (5.1.2) and the shipped libraries rely on Haystack and house tags only, so custom tags never change how a template behaves. Custom tags are included in Haystack output (5.12) as plain marker tags, since Haystack allows tags outside its library. Custom tags are part of the site configuration export (5.1.1). A custom tag that later appears in the house list can be merged into it.
- **Location:** equipment and points can be assigned to a room or section. A point inherits its equipment's location unless set directly. **Rooms are Haystack spaces, not equipment (decided).** A point can belong to a room directly, with no equipment, which suits room sensors (temperature, humidity, CO2, occupancy). The **Room** starter template is a **space template**: it works like an equipment template (roles, tags, limits, alarms, graphic, versions and roll-out, 5.1.2) but creates a room and its points. Locations match the rooms and tabs in the graphics (6.4, 6.5). Setup is described in 5.1.1.
- **Relationships (decided).** Besides each point's equipment and optional location, the model stores two optional links:
  - **Part of:** equipment can sit inside other equipment, for example a supply fan and a cooling coil inside AHU-1. A point still belongs to one piece of equipment (the fan), and the parent (AHU-1) shows its children's points. Nesting is limited to a set depth (proposed 3 levels) and the checker rejects loops. Stored as Haystack `equipRef` on the child equipment.
  - **Feeds:** one piece of equipment, or a room, is supplied by another, with a medium: **air** (AHU-1 feeds Meeting room), **water** (pump feeds coil), **electricity** (main meter feeds sub-meter or distribution board), or **refrigerant** (outdoor unit feeds indoor units). One thing can be fed by several. Stored as Haystack refs: `airRef`, `chilledWaterRef`, `hotWaterRef` and `condenserWaterRef` for water, `elecRef` for electricity, `refrigRef` for refrigerant (all standard in Haystack 4.0.0).
  - Uses: the room-to-duct link on graphics (6.4), meter trees for power trends (5.7), filtering the mapping table (5.1.1), and alarms that show what else is affected. Equipment templates (5.1.2) can include child equipment (an AHU template with its fans and coils). Both links are part of the configuration export.
- **Point types (decided): Haystack's three.** Every point is exactly one of **sensor** (a measured or reported value, including run status, fault and alarm states), **sp** (a setpoint) or **cmd** (a command to equipment). Each point also has a **value kind**, stored as Haystack `kind`: `Number` (with `unit`), `Bool` (true/false, with `enum` for the labels such as Off and On), or `Str` with an `enum` for multistate values (a named list, for example Cool, Heat, Fan, Auto, Dry). Device **configuration points** (5.1) are not a separate type; they carry a configuration flag that adds the warning and confirm step.
- Naming: lowercase, descriptive, consistent. **Pattern (decided): `<room>_<equipment>_<function>`**, for example `meeting_ac1_room_temp` or `plant_ahu1_supply_air_temp`. Names are unique on the site. Equipment with no room uses `site` as the room part (for example `site_meter1_power`). [Assumed] The name is stored as the house tag `devineName` (Appendix A); Haystack tools use `id` and `dis`.
- **Renaming (decided).** Every point and piece of equipment also has a hidden permanent ID (the Haystack `id`), which history, logic, alarms and the audit log use internally, so a rename never breaks them. When equipment moves room or a room is renamed, the BMS **offers** to rename the affected points, shows a preview of old and new names, and updates references in logic diagrams itself. The old names are kept as searchable **aliases**, and logic JSON or CSV that uses an old name still resolves to the right point on import (5.3.2). Renames are audit-logged (5.6). Section 5.3.2's rule that exports use names, never internal IDs, is unchanged. Display names are separate and editable.
- Units stored in SI/metric, using **Haystack unit names** (for example `°C`, `%RH`, `kW`, `kWh`); converted for display. Timestamps stored in UTC, displayed in site local time (default Australia/Sydney).

#### 5.2.1 Value quality (decided)
- **Every value carries a quality flag (decided): Good, Bad or Stale.** The reason is stored with it.
  - **Bad:** the device is offline, a read failed or timed out, the device returned an error, or the device itself reports a sensor fault (for example an open circuit on an input module).
  - **Stale:** no new value for longer than a set time. The default is **3 times the point's poll interval**, adjustable per point or template (5.1.2).
  - **Good:** a recent, valid value.
- **Graphics and HMI:** a Bad or Stale value shows the symbol's Offline state (6.4) and the value is greyed with the reason available on tap. It is never shown as a live number.
- **Alarms:** Bad and Stale feed the offline device and stale data alarms (5.5).
- **Trends and history:** gaps are shown as gaps and are not filled in. Summaries (5.7) use Good values only.
- **Logic:** each input block uses a **fallback value** or the block's **fail-safe** when its input is not Good (5.3.1). Logic never runs on a Bad value silently.
- **Commands:** a write to a point that is Bad is still attempted and reports the result to the operator, so a recovering device can be commanded. The attempt and result are audit-logged (5.6).
- **Poll and COV settings** (the speed class of each point, COV where a device supports it, 5.1.4) are set in the device and equipment template (5.1, 5.1.2) and drive the stale time.

### 5.3 Control logic (mixed, visual block editor)
- Some systems keep logic in their own controllers; the BMS monitors, adjusts setpoints and schedules.
- Other systems are driven by BMS logic, which writes outputs to devices.
- Each equipment item is flagged **device-controlled** or **BMS-controlled** so it is always clear where the logic lives.
- **Logic is authored in a visual block editor** in the browser: blocks for point reads/writes, comparisons, timers, PID, staging, setback, and so on, wired together per equipment item.
- **[Assumed]** Logic is saved as versioned data, validated before it runs, and the server executes it in a controlled runtime with write limits applied (see 5.6). Logic can be tested against simulated points before going live, and has a clear enable/disable per item.
- This is a **large build**. Plan it as its own milestone, and do not block earlier milestones on it.

### 5.3.1 Logic editor (web app) **[Assumed]**
Control logic is drawn as a **function block diagram** in the browser (React Flow), one diagram per equipment template or per piece of equipment, or a **site diagram** that spans several (below). Web app mockup sheets 5 and 6 (6.3.1).

**Diagram scope (decided):**
- **Template diagram:** belongs to an equipment template and refers to roles, so it runs on every instance (5.1.2). It covers one piece of equipment.
- **Equipment diagram:** belongs to one piece of equipment and picks its points directly.
- **Site diagram:** a one-off diagram that **picks points from any equipment on the site directly**, for jobs that span several pieces of equipment, such as lead/lag staging of a pump set or running an AHU when any room it feeds calls. Site diagrams are not templates and are not reused across instances. They can be exported and imported (5.3.2) to reuse them on another site, with references mapped on import. The publish checks apply as usual, including **no two diagrams write the same output**, so a site diagram and a template diagram can never both drive one point. The equipment panel lists every diagram that writes to that equipment, so it is clear where its logic lives. Reusable multi-equipment templates (for example a pump set template) are **not in v1**.

**Layout:**
- **Block palette (left):** searchable groups. **v1 block set (decided):** Read and write (point read, point write, **variable**, schedule), Logic (AND, OR, NOT, **compare**), Math (**limit**, **scale**), Timing (**delay on, delay off**, minimum on/off, **astro time switch**) and Control (**PID**, **manual/auto**). Staging, setback, reset curves and pulse blocks are later; they can be built from the v1 blocks. Drag a block onto the canvas.
- **Canvas (centre):** blocks flow left to right: inputs on the left, logic in the middle, outputs on the right. Wires join an output port to an input port. A wire can only join compatible types (true/false, number, text), shown by the port style.
- **Top bar:** logic name, status (draft or published, version number), a **BMS-controlled** flag (5.3), the template and number of instances that use it, and an enabled or disabled switch.
- **Properties panel (right):** settings for the selected block, checks before publishing, and the Publish and Save draft buttons.
- **Test strip (below the canvas):** sliders and switches for simulated points, step and fast-run buttons, and the resulting output values.

**Editing blocks (in Edit mode):**
- **Select:** click a block or wire. Shift-click or drag a box to select several.
- **Delete:** press **Delete** or **Backspace**, use the **Delete** button on the canvas toolbar, or right-click and choose Delete. Deleting a block also removes the wires attached to it. A wire is deleted the same way, or by dragging its end off a port.
- **Warnings before deleting:** deleting an **output block** (point write) or a block that feeds an output shows a short warning, because the output then falls to its fail-safe once the change is published. A **variable** that other logic still uses cannot be deleted until it is unused.
- **Undo and redo** (Ctrl+Z, Ctrl+Y) for every edit, for the whole editing session.
- **Cut, copy, paste and duplicate** (Ctrl+X, Ctrl+C, Ctrl+V, Ctrl+D), using the same format as import and export (5.3.2).
- **Drafts save automatically.** Nothing a user deletes affects running logic until a version is published, and earlier versions can be restored (see Publishing below).
- Other editing helpers: snap to grid, align and distribute, fit to screen, and a minimap for large diagrams.

**Edit and Live modes:**
- **Edit mode** moves and wires blocks.
- **Live mode** shows the current value on every wire and block (true or false, numbers with units), and colours wires blue when true and grey when false. It uses the same data as the HMI and does not write anything.

**v1 blocks in detail:**
- **Compare:** less than, greater than, less than or equal, greater than or equal, equal, not equal. Output is true or false. An optional **deadband** gives hysteresis so the output does not chatter (for example on above setpoint plus 0.5, off below setpoint minus 0.5).
- **Timer delays:** **delay on** (output follows the input only after it has been true for a set time), **delay off** (output stays true for a set time after the input goes false), and **minimum on/off** (holds the output on or off for a minimum time). Times are set on the block, from a variable, or from a point. The block shows the time remaining in Live mode.
- **PID:** inputs process value, setpoint and an optional enable; output 0 to 100 % (or engineering units). Settings: proportional gain, integral time, derivative time, output minimum and maximum, direct or reverse acting, sample time, and **anti-windup**. In manual it **tracks** the manual output so that going back to auto is bumpless. Auto-tune is not in v1.
- **Manual/auto:** inputs auto value, manual value and a mode. The output is the auto value in Auto, and the manual value in Manual. It follows the operator hand mode in 5.6 (including its 1, 2 or 8 hour timer, default 2 hours) and returns to Auto when the timer ends. The block, the graphics symbol and the HMI show the state (Auto or Manual) and who set it. A PID block can take its manual-track input from this block.
- **Limit:** holds a number between a minimum and a maximum (each a constant, a variable or a point). An optional **rate limit** caps how fast the output can change (for example 5 % per second, so a valve or damper is not slammed). The block shows in Live mode when it is clamping (at minimum, at maximum or rate-limited).
- **Scale:** a straight-line conversion from an input range to an output range, for example 0 to 10 V to 0 to 100 %, or 4 to 20 mA to 0 to 50 °C. Settings: input minimum and maximum, output minimum and maximum (swap them to invert), the output unit, and **clamp to output range** on or off. This is for logic calculations; scaling a device's raw register to a value belongs on the point (5.1).
- **Astro time switch:** a true/false output tied to sunrise and sunset at the site, for outside lights, blinds and similar. The site latitude, longitude and time zone are set once in the site settings (daylight saving follows the time zone), and the times are **calculated locally, so it works with no internet**. Settings: which state is true (night, or day), an **offset** before or after sunset and sunrise (for example on 30 minutes before sunset, off 15 minutes after sunrise), and an optional enable input. It also outputs the next on and off times, shown in Live mode, and a manual or hand mode override is still possible through the manual/auto block.
- **Variables:** named values the logic can read and write, shown in a **Variables** list in the logic editor and in Live mode. Each has a type (number with units, true/false, or text) and a **scope**: **site** (shared by all logic), **equipment** (one piece of equipment or all instances of a template, as separate copies), or **diagram** (private to the diagram). A variable is either a **constant** (a value the user sets, such as a setpoint or a time, with limits) or a **memory value** written by a variable-write block (such as a latched alarm, a counter or a calculated target). Constants and memory values are saved through a restart. A variable can be **published as a point** so it appears on graphics, the HMI and schedules, with write limits (5.6), and a change to it is audit-logged. **Variables published as points can be changed from the Pi HMI** by a logged-in operator (PIN, as in 6.2), within the variable's limits, with the change audit-logged (5.6). The HMI shows the unit, allowed range and who last changed it. In direct mode (section 9) the logic does not run, so variables are not adjustable there. **Decided.**

**Blocks refer to template roles.** In a template's logic (5.1.2), an input block reads `room_temp` or `setpoint`, not a specific point, so the same diagram runs on every instance. A diagram made for a single piece of equipment can pick points directly.

**Execution (proposed):**
- The runtime evaluates each diagram on a fixed cycle (proposed 1 second) in a defined order from inputs to outputs. Feedback loops need a delay block, and the checker rejects loops without one.
- Timers keep their state through a server restart where it is safe to, and otherwise restart from a known state.
- **Output blocks** show the point, the allowed values, the BACnet write priority and the hand mode state. The write limits in 5.6 always apply. An operator hand mode (5.6) overrides a logic output until it times out.
- **Fail-safe:** each output block has a stated fail-safe (release to the device's own control, hold last value, or go to a stated value). The default is **release to device** (decided). This applies when logic is disabled, a block fails, an input is bad, or the server stops. It must be confirmed before publishing.
- Bad inputs (offline or fault) are shown on the block. The user chooses per input whether the logic uses a stated fallback value or goes to the fail-safe.

**Publishing:**
1. **Checks:** every input is bound, outputs sit within write limits, no loops without delay, a fail-safe is set, and no two diagrams write the same output.
2. **Test:** run against simulated points before going live (16). Recommended but not forced.
3. **Publish:** creates a new version and is audit-logged (5.6). A diagram for a single piece of equipment goes live at once. **Template logic is part of the template version (decided):** publishing it creates a new template version, and instances move to it through the roll-out in 5.1.2 (all, a group, or one at a time), so new logic can be tried on one unit first. An instance override always wins. Previous versions can be restored.
4. **Disable:** one switch per piece of equipment, which applies the fail-safe.

**Standalone mode** has the same editor, served by the Pi's web app and used from a computer or phone (3.1). The touch screen shows logic state and variables only. The Pi's smaller capacity limits the number of diagrams and blocks (open item 16). **Decided.**

### 5.3.2 Importing and exporting logic **[Assumed]**
Logic can be written outside the editor and pasted in, as Node-RED does with its import and export. Web app mockup sheet 7 (6.3.1).

- **Export:** the selected blocks, a whole diagram, or a template's logic, copied to the clipboard or saved as a file (`.bmslogic.json`). Copy and paste inside the editor (Ctrl+C and Ctrl+V) uses the same format, so blocks can move between diagrams, browser tabs and sites.
- **Import:** paste into the canvas (Ctrl+V) or use the **Import** dialog with a text box and a file chooser. Pasted blocks land at the cursor and join the diagram, or **Import as new diagram** makes a separate one.
- **Format:** plain JSON, documented and versioned (for example `"schema": "devine-bms-logic/1"`), with a published schema in the repository. Each diagram lists **blocks** (id, type, settings, optional position), **wires** (from block and port to block and port) and any **variables** it uses. Points and template roles are referred to by role name or point name, never by internal ID. Because the format is documented, a diagram can be written by hand, generated by a script, or drafted with an AI tool and then pasted in.
- **Validation before anything is created:**
  - **Errors block the import:** unknown block types, settings out of range, wires to missing ports or between incompatible types, duplicate block IDs inside the pasted text, and loops without a delay block.
  - **Warnings can be accepted:** unresolved references and missing positions.
  - Each message names the block and the problem, and the dialog offers **Remove the failing blocks and continue**.
- **Mapping references:** a reference to a template role is matched automatically when that role exists. Unresolved points, roles, variables and schedules are listed in a **mapping table** with suggestions, so the user picks the real item or creates it (for example a new variable). Block IDs that clash with existing ones are renumbered. Blocks without positions are laid out automatically.
- **Imported logic is data only and is always a draft.** It never runs until it has passed the publish checks (5.3.1) and been published. **There is no code or script block in v1**, so pasted text cannot run arbitrary code. Importing needs the admin role (section 7) and is audit-logged (5.6).
- **Snippets (option):** save a selection as a named snippet (for example "Run-on timer with alarm") and reuse it across diagrams and sites. Snippets use the same format. **[Assumed]**

### 5.4 Schedules
- Calendar-based: events with recurrence, holidays and exceptions, and priority layers (for example a holiday overrides the weekly pattern).
- Schedules apply to equipment or groups by tag.
- **BMS plus controllers:** the BMS runs schedules and can also write them into controller schedule objects (BACnet Schedule/Calendar) so equipment keeps its schedule if the server fails.
- **The BMS is the schedule master.** A push overwrites the controller's schedule. The BMS periodically reads controller schedules and flags any that were changed locally, so the operator can re-push or accept the difference.
- **[Open]** Which devices support schedule objects (to be found on the bench).

### 5.5 Alarms
- Alarm types: high/low limit, offline device, stale data, state change.
- Delivery: on-screen, **email over SMTP**, **SMS** through a gateway API, and **push through ntfy**. SMS, email and push need internet at the site.
- The SMS sender is built as a **swappable plug-in**, so the provider can change. **The first plug-in is ClickSend (decided, milestone 6).**
- The push sender is also swappable. **The ntfy server is a site setting, defaulting to the public ntfy.sh (decided, milestone 6)**, so a site can move to a self-hosted server without code changes. Keep alarm text free of sensitive detail either way.
- **Three severity levels (decided):** **Info**, **Warning** and **Critical**. Each alarm has a level, shown by text and shape as well as colour (6.3). Default channels, which a site can change in its settings:

| Level | Screen | Email | ntfy push | SMS |
|---|---|---|---|---|
| Info | Yes | No | No | No |
| Warning | Yes | Yes | Yes | No |
| Critical | Yes | Yes | Yes | Yes |

  Templates (5.1.2) carry a default level for each alarm, and a level can be overridden per instance. Which people receive each level is described in 5.5.1 once decided.
- Acknowledge, comment and clear, with history.
- Escalation rules not required in v1.

#### 5.5.1 Alarm recipients **[Assumed]**
- **Site recipient list (decided).** An admin keeps one list of people for the site. Each person has a name, email address, phone number (SMS) and ntfy topic, and a tick for each level they receive (Info, Warning, Critical). It works the same in standalone mode.
- **Critical always goes out.** A recipient cannot mute Critical, and at least one recipient must be ticked for Critical or the settings page shows a warning.
- **Quiet hours (optional)** can be set per person for **Warning** only, for example 10 pm to 6 am. Warnings raised in that window are held and sent as one summary when it ends. Info never leaves the screen.
- **Test message:** an admin can send a test to any recipient and see whether it was delivered or failed.
- **Delivery log:** each notification records who it went to, the channel and the result (sent, failed or held), viewable alongside the alarm history.
- Recipient details are personal data kept on the site server only (section 8). Alarm text carries no sensitive detail (5.5).

### 5.6 Commands and safety
- **Audit log** for every command: user, time, point, old and new value. Configuration changes (devices, templates, logic, users, settings) are logged the same way.
  - **Kept for 1 year (decided).** Older entries are deleted automatically. The period can be made configurable per site later.
  - **Viewer and export:** admins see it in the web app with filters (user, point, date, type) and can export it as CSV. Operators see their own actions (7). It cannot be edited or deleted from the app.
  - **Standalone mode:** a shorter list held in memory, written out with the scheduled USB export (3.1). **[Assumed]**
  - Commands made in direct mode are logged on the HMI and synced to the server afterwards (section 9).
- **Write limits:** server enforces min/max on setpoints.
- **Confirm prompts** for critical commands (start/stop, overrides).
- **Override timers:** manual overrides expire and return to auto. The operator picks 1, 2 or 8 hours when setting a hand mode, with **2 hours as the default**. Each site can change the default and the choices in its settings. This applies in both modes. **Decided.**
- Hardware interlocks and safety circuits remain independent of software.

### 5.7 History and reporting
- **Tiered retention:** **30 days** full resolution, **1 year** hourly summaries, **5 years** daily summaries. Make periods configurable per site later. **[Assumed]**
- v1 reporting (decided): **live dashboards** and **trend charts**, with **CSV export** of any trend. **There are no separate energy reports in v1.** Energy and solar are trended like any other point: power meters (the power meter equipment template, 5.1.2) provide tagged kW and kWh points, so consumption, demand, solar generation and grid import and export can be plotted and exported. Daily and monthly usage reports, and tariff costs, are later.
- Scheduled exports are not in v1.

### 5.8 Time and clocks **[Assumed]**
- **The site server is the time master (decided).** It syncs from internet time (NTP) whenever it has a link and keeps running on its own clock when it does not. An admin can also set the time and time zone by hand in the site settings.
- **The Pi HMI follows the server.** In direct mode (section 9) the Pi runs on its own clock from its last sync. In standalone mode the Pi is the master, with NTP when it has a link and a manual setting otherwise.
- **Devices:** the BMS can send BACnet time synchronisation to devices that accept it. It is off by default and set per device. Modbus devices are not synchronised.
- **Storage and display:** timestamps are stored in UTC and shown in the site time zone, including daylight saving (5.2). Schedules (5.4), the astro time switch (5.3.1) and logs all use the site time.
- **Clock health:** the BMS raises an alarm if the clock has not synced for a set number of days (proposed 7), or if a sync changes the time by more than a set amount (proposed 1 minute). Large jumps are written to the audit log (5.6).
- A battery-backed clock on the Pi is not required. Confirm on the bench how a Pi behaves after a long power loss with no internet, because logs and schedules depend on a sane clock at boot.

### 5.9 Diagnostics and support **[Assumed]**
- **Health page (admin):** one page showing every device and bus (answering or not, last good read, error counts, response times, retries), MS/TP trunk health (5.1), the server and Pi (CPU, memory, disk, temperature, uptime, power state), service status, time sync (5.8), and software versions. It uses the same data as the alarms for offline devices.
- **Support bundle:** one button builds a file with recent logs, configuration, device and bus status and versions, with **passwords, PINs, keys and recipient details removed**. It can be downloaded and sent, and is the usual starting point for remote support.
- **Live traffic viewer (decided):** an admin tool that shows the Modbus and BACnet messages on a chosen device or bus as they happen. Each line shows the time, bus, device, direction, function or service, point, raw bytes, result and timing. It has filters (device, point, errors only), pause and resume, and export to a file.
  - It shows **the BMS's own requests and responses**. It does not see other masters on the bus. **Listening to a whole RS485 bus** (other masters, or devices talking to each other) needs a separate adapter in listen-only mode, which is an option rather than part of v1. **[Assumed]**
  - It is **read-only** and never sends anything. Capture is limited in size (a rolling buffer in memory) and in time, so it cannot fill the disk or slow polling, and it turns itself off after a set time.
  - Writes are shown with the user who sent them (5.6). PINs and passwords are never shown.
  - It is the same tool as the test tool in 5.1 (raw request and response) but for live traffic, and it works in standalone mode, with a smaller buffer.
- **Remote support** runs over Tailscale with the customer's consent (section 1), and the admin role is needed for these tools (section 7).

### 5.10 Maintenance counters (decided)
- **Run hours and start counts are in v1 (decided).** Any equipment with a run state point (a true/false sensor role tagged `run`, 5.1.2) gets a **run-hours counter** and a **start counter** automatically. They are counted by the BMS from the state changes it sees.
- **Where they show:** on the equipment panel (the tap-to-control screen, 6.4, and the web app), and as read-only points that logic (5.3.1), alarms (5.5) and trends can use.
- **Service reminders:** each piece of equipment (or its template) can set a service interval in hours or starts. When it is reached the BMS raises an alarm at a chosen level (default Info, 5.5) with a message such as "Service due: ac1_indoor, 2,000 hours". An admin marks the service as done, which resets the "since service" counter and writes an audit entry (5.6). Total hours are never reset.
- **Device counters:** where a device reports its own run hours (a role such as `run_hours` in its template), the BMS shows and uses that value instead, so a BMS outage does not create a gap.
- **Gaps:** time when the BMS cannot see the state (value Bad or Stale, 5.2.1) is not counted, and the equipment panel marks the counter as estimated for that period.
- **Storage:** counters are saved with the history in full mode and survive restarts. **Standalone mode** keeps the SD card free of writes (3.1), so counters are written to the configuration partition at most once a day and when a reminder fires, and to the USB export if it is on. **[Assumed]**
- Counters, intervals and service marks are included in the configuration export (5.1.2).

### 5.11 BACnet server: exposing data to other systems **[Assumed]**

Purpose: let another BMS or supervisory system (for example the head-end of a large multi-building site) read data from this BMS. This is **not** needed for the Pi HMI to control pumps and motors, which writes to the field devices directly (section 9).

- **Read-only in v1.** The BMS appears as a BACnet device (BACnet/IP, and MS/TP where the plan allows). Outside systems read; they do not write. Writable objects (for example schedule edits from outside) are left out until a rule for who wins is decided.
- **Objects exposed from the database:** Schedule and Calendar objects (from 5.4), Trend Log objects (from the history in 5.7, read with ReadRange), and optionally live point values as standard input/value objects. Which points are exposed is chosen per point in the web app (off by default).
- **Takeover:** in direct mode the Pi HMI does not serve these objects, so they go offline for the outside system. The spec says so rather than hiding it.
- **Claims:** only the objects and services that pass bench tests are listed as supported (section 1).
- **Proving it:** needs a bench test with a second BACnet client reading Schedule, Calendar and Trend Log (ReadRange) from the BMS.

### 5.12 Haystack API **[Assumed]**

Purpose: let Haystack tools (for example SkySpark, analytics and energy platforms, or a customer's own scripts) read this site's model, live values and history. It sits beside the BACnet server (5.11), which serves BACnet-only systems.

- **Standard Haystack HTTP API, read-only in v1 (decided).** Operations: `about`, `ops`, `formats`, `read` (by filter or id), `nav`, `hisRead` (history from 5.7), and `watchSub`, `watchPoll` and `watchUnsub` for live values. Formats: Haystack JSON and Zinc. `pointWrite`, `hisWrite` and `invokeAction` are **not offered in v1**, so outside systems cannot command anything. Writes can be added later once a rule for write priority against BMS logic, schedules and HMI hand modes is decided (5.6).
- **What it serves:** the entities and tags in 5.2, current values with `curStatus`, and history for points tagged `his`, with gaps left as gaps (5.2.1).
- **Access:** **off by default**; an admin switches it on in the site settings. Clients log in with **Haystack SCRAM authentication** (the password is never sent in clear) using a site account (section 7). An **API client** account type has viewer rights only and cannot log in to the web app. Every API login is audit-logged (5.6), and requests are rate-limited. Reached over Tailscale like the web app; LAN access follows the same network restrictions (section 8).
- **Load:** API reads come from the database and the live value cache, never from the field buses, so a client cannot add bus traffic. `hisRead` ranges and watch counts are limited (sizes to be set).
- **Standalone mode:** the API is available on the Pi, with history limited to the in-memory log (3.1).
- **Direct mode:** the API is served by the site server only, so it goes offline during HMI takeover (section 9), as the BACnet server does.
- **Import and export:** the site model (sites, rooms, equipment and points, no history) can be exported as a Haystack JSON or Zinc file, and the template library can import Haystack-tagged point lists to speed up commissioning. **[Assumed]**
- **Claims:** describe the BMS as "Project Haystack 4 compatible" for the operations above only. Check Project Haystack's rules on using the name before marketing it (section 1).
- **Testing:** an automated test suite reads the model, live values and history through a Haystack client library and checks them against the defs (section 16).

## 6. User interface

### 6.1 Site web app
- Served by each site server and reached over Tailscale for remote users.
- Responsive. **Phone priority screens (decided): Overview and alarms, and the Graphics tabs** (the Site tab and room tabs, with tap-to-control, 6.4 and 6.5). These two must be designed and tested for a phone first. Setpoints and overrides are reached through the room tabs and tap-to-control on a phone. Trends work on a phone but are laid out for desktop first. **The editors** (graphics editor, symbol manager, logic editor, device, template and mapping screens, diagnostics) are designed for a desktop or tablet and are usable but not optimised on a phone.
- **Light and dark themes** with a user switch.
- **Branding per site:** logo, name and colours configurable per site (white-label).
- Built-in **graphics editor**: drag and drop symbols, bind them to points, save per site. Symbols come from the symbol library (see 6.4).
- Built-in **visual logic editor** (see 5.3).
- Pages: overview, system graphics, trends, alarms, schedules, logic (editor, variables, import and export), devices (discover, templates, equipment, mapping table), diagnostics (health, traffic viewer, support bundle), audit log, users (including Haystack API client accounts, 5.12), and site settings.

### 6.2 Pi HMI
- Screens: system overview with active alarms, per-system graphics, setpoints and overrides, schedules and short trends.
- **Graphics use the tab structure in 6.5 in both modes:** a Site tab with a whole-site graphic, then a tab for each room or section. The bottom bar holds the other functions.
- Dark theme by default, large touch targets, auto-start on boot, watchdog restart.
- **Login:** anyone can view; a PIN or user login is required to command, and commands are logged to that user.

### 6.3 HMI mockups

Design mockups for the Pi HMI, on a **7 inch touch screen, 1024 x 600 landscape**: https://claude.ai/artifact/WzmjwLpT8QxJsUuG2DiZyb (private to the owner until shared). Values shown are sample data and `[Site name]` is a placeholder. When the mockups and this specification disagree, update both and record the change in section 19.

| # | Mockup screen | Spec sections it shows |
|---|---|---|
| 1 | Overview: equipment tiles and active alarms | 6.2 system overview with active alarms; 5.5 alarms (severity, acknowledge); 5.3 BMS or device control shown on each tile |
| 2 | Site tab: the whole site in one graphic (full mode) | 6.5 graphics tabs; 6.4 rooms and colour rules; 6.2 per-system graphics |
| 3 | Room tab (same in both modes): process graphic and room controls | 6.5 room tabs; 5.6 commands and write limits; 6.4 symbols and ducts |
| 4 | Plant room tab: AHU-1 process diagram with live values and control state | 6.4 process diagrams and colour rules; 5.3 control flag and mode; 5.4 schedule state; 5.6 override state |
| 5 | Tap to control: pump control panel (opened from sheet 4) | 5.6 write limits, override timers, audit log; 6.2 login; 6.4 tap to control |
| 6 | Standalone mode: site tab | 3.1 standalone mode; 6.5 tabs; USB log status in the top bar |
| 7 | Standalone mode: overrides and setpoints | 3.1 standalone mode; 5.6 write limits and hand-mode timers; hand modes return to Auto after power loss |
| 8 | Standalone mode: schedules (simple weekly on and off times per zone) | 3.1 standalone mode; 5.4 schedules (reduced in standalone) |
| 9 | Standalone mode: log and USB export | 3.1 memory-only log, scheduled USB export, CSV download |
| 10 | Setpoints and overrides (full mode) | 5.6 write limits, confirm prompts, override timers, audit log; 6.2 setpoints and overrides |
| 11 | PIN to command | 6.2 login (view open, PIN to command); 5.6 audit log records the user |
| 12 | Schedules (full mode: today, next changes, exceptions) | 5.4 calendar schedules, holidays, BMS as master, controller sync flagged; 6.2 schedules |
| 13 | Short trends (full mode) | 6.2 short trends; 5.7 history and retention (long history is on the web app) |
| 14 | Direct mode (server offline) | 9 standby takeover (automatic after 30 seconds and a clear bus), manual control only, failback after a healthy period, operator hand-back; 11 hardware access |
| 15 | Symbol library: air side (fan, damper, coils, door, and temperature and humidity sensors that show °C and %) | 6.4 symbol library, Off and animated On states, value symbols |
| 16 | Symbol library: water, refrigeration, electrical (pump, compressor, valves, tank, pipe flow, light, meter) | 6.4 symbol library, Off and animated On states, value symbols |
| 17 | Symbol states (Off, On, Fault, Offline, Manual) | 6.4 symbol states and priority; 5.5 alarms; 5.6 override state |
| 18 | Pipes, ducts and colour rules | 6.4 static elements and the three colour rules |

Design choices shown in the mockups (all **[Assumed]**, confirm or change):
- Dark theme only on the HMI, with status shown by text and shape as well as colour (blue normal, amber warning, red alarm).
- Bottom navigation with five tabs: Overview, Graphics, Setpoints, Schedules, Trends. Touch targets are at least 44 px.
- A lock button in the top bar shows "View only" or the operator session with a countdown to auto-lock (5 minutes proposed).
- In direct mode, Schedules and Trends are unavailable because they depend on the server, and email, SMS and push alarms are paused.
- Setpoint and override changes show the allowed range, and overrides show their expiry time.

### 6.3.1 Web app and phone mockups

Design mockups for the web app (1280 x 800) and the phone (390 x 844), on their own canvas: https://claude.ai/artifact/2q3y19226mcWDdcE8r3qkc (private to the owner until shared). The Pi HMI mockups in 6.3 are on a separate canvas. All values are sample data.

| # | Mockup screen | Spec sections it shows |
|---|---|---|
| 1 | Discover and add devices (web app): scan results across BACnet/IP, MS/TP and Modbus, template suggestion, address clashes | 5.1.3 discover and add devices |
| 2 | Equipment template editor (web app): point roles, tags, limits, alarms, and the change preview where an override always wins | 5.1.2 equipment templates |
| 3 | Create equipment from a template (web app): one instance per Mitsubishi group, matched points, overridden settings marked | 5.1.1 commissioning; 5.1.2 equipment templates |
| 4 | Room mapping table and verify screen (web app): bulk room and tag edits, CSV, go-live checks | 5.1.1 commissioning |
| 5 | Logic editor (web app): block palette, function block diagram with live values, properties and checks, simulated test | 5.3 control logic; 5.3.1 logic editor; 5.6 write limits |
| 6 | Logic editor: second diagram with delay on, PID, manual/auto and variables (AHU supply air temperature) | 5.3.1 v1 blocks and variables; 5.6 hand mode |
| 7 | Import logic (web app): paste JSON, validation results, reference mapping, import as draft | 5.3.2 importing and exporting logic |
| 8 | Device and bus health (web app): buses, devices, server and Pi health, live traffic viewer, support bundle | 5.9 diagnostics and support; 5.2.1 value quality |
| 9 | Alarms and recipients (web app): active list, three levels, default channels, site recipient list, delivery log | 5.5 alarms; 5.5.1 alarm recipients |
| 10 | Symbol manager (web app, 1280 x 800) | 6.1 graphics editor; 6.4 import and custom symbols |
| 11 | Phone (390 x 844): overview and alarms | 6.1 phone priority screens; 5.5 alarms |
| 12 | Phone (390 x 844): room tab with graphic and controls | 6.1 phone priority screens; 6.5 room tabs; 5.6 override timers |

### 6.4 Graphics symbols and custom site graphics

Graphics are **drawn in the web app only**. The Pi HMI shows them live and cannot edit them (it keeps the view and command role).

- **Symbol library** shipped with the BMS: fans, compressors, pumps, temperature and humidity sensors, coils, doors, volume control dampers, proportional valves, on/off valves, tanks, pipe or duct flow, lights and meters. Each symbol is an SVG on a 64 x 64 viewBox so it scales to any size.
- **Per-site custom symbols:** a site can draw a new symbol in the editor or **import an SVG**. An imported SVG names an `off` group and an `on` group. The `on` group may be animated.
- **Value symbols.** Sensors and meters (temperature in °C, humidity in %, power in kW and similar). Level symbols such as tanks show the percentage with an animated fill show their live value with units instead of Off and On. They show `--` with the unit when there is no data, and the units come from the point.
- **Static elements.** Pipes, pipe elbows and tees, ducts, duct elbows and duct transitions are drawn in the editor and snap to a grid. They have no value of their own and take their look from a colour rule.
- **Colour rules.** Each static element binds to a point or symbol and uses one of three rules:
  1. *Follows a symbol:* takes that symbol's state (Off grey and still, On blue and flowing, Fault red and flashing, Offline dashed grey).
  2. *Value bands:* up to four bands on any number (for example a duct is blue below 16 °C, green 16 to 24, amber above 24, red above 28) with hysteresis so it does not flicker.
  3. *Proportional fill:* a 0 to 100 % point such as valve position fills the pipe along its length.
  Colour is never the only signal: flow animation, dashes and flashing also change. An element with no data is dashed grey.
- **System graphics.** One graphic type covers both process diagrams and floor layouts. A system graphic combines symbols, static elements, rooms and live value readouts (with units). Flow animation runs only while the bound point shows flow. The top bar of each graphic shows run state, mode, BMS or device control, schedule state and overrides.
- **Rooms.** A room is a resizable rectangle (squares included) drawn in the editor. Free-form outlines are not in v1. It shows its name and live values (temperature, setpoint, humidity, damper position, occupancy and similar) and takes its border and fill colour from a value-band rule (for example below 20 °C blue, 20 to 24 green, above 24 amber, above 28 red). Rooms can be linked to the duct that serves them, and tapping a room opens its setpoint screen.
- **Tap to control.** On a process diagram, tapping a symbol that has a writable point opens a control panel (Auto, Hand on, Hand off, setpoint or speed). It follows the command rules in 5.6: write limits, confirm step, hand-mode expiry and audit log. Viewing is open. Commanding needs a PIN or login on the HMI and a user with command rights on the web app. Symbols with no writable point do nothing when tapped.
- **States.** Every symbol supports Off, On (animated), Fault, Offline (no data) and Manual (overridden). Fault, Offline and Manual are drawn automatically from the same artwork (colour, dashed outline, badge) unless the site supplies its own.
- **State priority:** Offline, then Fault, then Manual, then On or Off. Colour is never the only signal (shape and badge are also used).
- **Binding.** A symbol binds to one or more points: a run status for On or Off, an alarm state for Fault, a communications state for Offline, and an override flag for Manual. Analogue values (valve position, level) can drive the animation.
- **Animation** uses SVG animation (SMIL) or CSS, with no scripts. Imports are **sanitised**: scripts, event handlers and external images or fonts are removed, and file size is limited.
- **Pipes and ducts** are drawn as segments that animate in the flow direction only when the bound point shows flow.
- **Storage.** Library symbols ship with the software. Custom symbols and graphics are stored per site and included in the site backup.
- **Performance.** Animated graphics must stay smooth on the Pi (see bench test 15). The HMI may reduce or stop animation if the frame rate drops.

### 6.5 Graphics tabs (standalone and full mode)

- **Navigation is tabs.** A tab strip runs across the top: **Site**, then one tab for each room or section (for example Plant room, Office, Meeting room, Store, Workshop, Cool room). The strip scrolls if there are more tabs than fit. The bottom bar holds the other functions: **Graphics, Overrides, Schedules, Log**.
- **Site tab:** one graphic of the whole site. Rooms are rectangles with live temperature, status and a colour rule (6.4), plus site totals such as outside temperature and site power. Tapping a room opens that room's tab. Site alarms are shown on the graphic.
- **Room or section tabs:** each has its own process graphic (supply and return duct, damper, sensors, lights, equipment) with live values, and a control panel for setpoint and Auto, On or Off. Hand modes return to Auto after a timer. Commanding needs a PIN, as in 6.2.
- **Standalone bottom bar screens:**
  - **Overrides:** setpoints for each room with the allowed range, and a list of active hand modes with the time left and a Back to Auto button.
  - **Schedules:** simple weekly on and off times for each zone (Monday to Friday, Saturday, Sunday) and a short list of exceptions such as public holidays. Calendar-based schedules and controller schedule sync are full mode only.
  - **Log:** a chart of temperatures and power from the in-memory log (1, 6, 24 or 72 hours), the USB export schedule and status, Export now, and Download CSV.
- **Status strip:** the top bar shows the Standalone label, the USB log export status (last export time or a warning), alarm count and the lock state.
- **Tabs are made in the graphics editor:** a site chooses which graphics become tabs and their order. Rooms and sections are normal system graphics (6.4).
- **Full mode uses the same structure** for its graphics. Its bottom bar stays Overview, Graphics, Setpoints, Schedules and Trends. The Graphics section shows the Site tab and the room and section tabs; the web app uses the same tabs. **Decided.**
- Mockups: sheets 2 to 4 show the tabs in full mode (Site, Room, Plant room) and sheets 6 to 9 show standalone mode (site tab, overrides and setpoints, schedules, log). The graphics are the same in both modes. Only the top bar and bottom bar differ.

## 7. Users, roles and access

- **Accounts live on each site server** (no central user directory).
- **Three roles per site (decided):** viewer, operator and admin. A platform admin (you) account exists on every site.

| Action | Viewer | Operator | Admin |
|---|---|---|---|
| View graphics, values, trends, alarms, schedules | Yes | Yes | Yes |
| Acknowledge and comment on alarms | No | Yes | Yes |
| Commands, setpoint changes, hand modes, variables published as points (5.6) | No | Yes, within write limits | Yes, within write limits |
| Edit schedules | No | Yes (decided, milestone 7B) | Yes |
| Add and edit devices, templates, points, rooms, tags, Discover | No | No | Yes |
| Edit graphics, symbols and logic, import logic (5.3.2), publish logic | No | No | Yes |
| Users, PINs, site settings, backup and restore, updates | No | No | Yes |
| View the audit log | No | Own actions (**[Assumed]**) | Yes |

Write limits apply to every role, including admin (5.6). The same roles apply in standalone mode.
- **Haystack API client accounts (5.12):** a separate account type with viewer rights, for machines rather than people. It can only use the Haystack API, cannot log in to the web app or HMI, and is created and removed by an admin. **[Assumed]**
- Remote reachability is controlled by **Tailscale ACLs** (who can reach which site), then site accounts control what they can do.
- **Login: local accounts on each site server** with hashed passwords and **optional authenticator-app 2FA**. Works fully offline.
- **Login rules (decided defaults; an admin can change them in the site settings):**
  - **Passwords** of at least 10 characters, stored hashed and salted. No shared default passwords (section 1). The first admin sets theirs at setup, on a **first-run web page that is only offered from the server itself while the site has no accounts (decided, milestone 6)**.
  - **Web sessions** time out after 30 minutes idle.
  - **HMI PIN** unlocks commands for 5 minutes of idle time and then locks, as in the mockups (6.2). PINs are at least 4 digits and are separate from passwords.
  - **Lockout:** 5 failed logins (password or PIN) lock that account for 10 minutes. Lockouts and failed logins are written to the audit log (5.6) and an admin can unlock an account.
  - **2FA** stays optional, and an admin can require it for the admin role.
  - **Rate limiting** on the login page is always on, because the LAN uses HTTP (section 8).

## 8. Networking and security

- Site networks **vary**, so the site server supports: separate device network (two ports), shared customer LAN, or a dedicated 4G router.
- Remote access via **Tailscale** only. No inbound port forwarding.
- Every user who needs remote access needs Tailscale, or a shared node, on their device.
- **HTTP on the local network, HTTPS over Tailscale (decided).** The site web app and the HMI are served over plain HTTP on the site LAN. Remote users reach the same site over Tailscale with HTTPS (Tailscale certificates; the tunnel is already encrypted). **HTTPS is handled in the `bms-tailscale` container by `tailscale serve`, which renews its certificate itself and passes requests to `bms-web` over HTTP (decided).** `bms-web` holds no certificates. In standalone mode with Tailscale switched on, the Pi does the same with `tailscale serve` (section 11). **[Assumed]** Consequences to design for:
  - **Passwords and PINs cross the LAN unencrypted.** Keep the web app on the device or an isolated management network, never on a shared customer LAN without the customer's agreement, and restrict it to chosen networks in the site settings. Rate-limit and lock out failed logins.
  - **Secure-context browser features** (for example the clipboard button for copy in the logic editor, 5.3.2) only work on HTTPS. On the LAN the editor falls back to selecting text and the browser's own copy and paste.
  - **Optional HTTPS on the LAN** can be added later as a setting (a site certificate), without changing anything else. **[Assumed]**
  - The Pi kiosk loads the interface from the server or from its own local backend, over the device or management network.
- Device network kept separate from the customer's business network wherever possible.
- Authenticated access with roles; control commands logged with user and time.
- Secrets in environment files or a secrets store, never in the repo.
- Regular updates for the Pi and server (see section 11).
- No formal compliance standard targeted for now. Good-practice hardening only.

## 9. Offline and failure behaviour

- **Internet down:** site server and Pi HMI keep running normally. Remote access, SMS, email and push are unavailable until the link returns.
- **HMI offline requirement:** view and command only.
- **Site server failure: the Pi HMI takes over with full control (standby takeover).**
  - The HMI has its **own BACnet connection** (an RS485 adapter for MS/TP and an Ethernet link to the device network) and a cached copy of the point list and equipment configuration needed to read and command points.
  - Normally the HMI is a client of the site server only. It does **not** write directly to devices.
  - The HMI watches a heartbeat from the site server. If the heartbeat is lost for **30 seconds**, the HMI switches to direct mode and takes over reading and commanding.
  - **Failback:** when the server returns, the HMI shows "server is back" but stays in direct mode. After the server has been healthy for a few minutes (proposed 5), an **operator taps to hand control back**. The HMI then stops polling and releases the buses, clears any overrides it set, and the server resumes logic and schedules. The HMI never hands back automatically.
  - Every command made in direct mode is logged on the HMI and synced to the server's audit log afterwards.
  - Design rule: **only one writer at a time**, so the server and HMI never command the same points together.
  - **In direct mode the HMI gives manual control only.** It does not run BMS logic or schedules. Equipment relies on its own controllers (including schedules written into controllers, see 5.4) until the server returns.
  - **Takeover covers BACnet (IP and MS/TP) and Modbus (RTU and TCP).** MQTT and vendor-API devices are not covered in direct mode. **[Assumed]**
  - Modbus RTU is single-master, so the HMI stays silent on the bus until takeover, and the server must be fully stopped before the HMI starts polling. Modbus TCP devices may limit concurrent connections, so the server's connections must be closed first. The same applies to a serial gateway: its connection limit and the serial line behind it mean the HMI opens its own connection only after the server's are closed, and the Pi needs no USB adapter for that line.
  - **Takeover trigger (Decided):** takeover is **automatic**, with **manual failback**. The HMI takes over when the heartbeat has been lost for 30 seconds **and** each bus is clear: no server polling heard on a Modbus RTU line for 60 seconds, and no server token traffic (MAC 0) heard on an MS/TP trunk. If a bus is not clear, the HMI does not talk on it, shows a warning, and raises an alarm. The HMI shows a "direct mode" banner and raises a Critical alarm when it takes over. This replaces the earlier manual-only takeover rule. Failback stays manual (above).
  - **Wiring (Decided):** the Pi HMI sits away from the site server, so each of its USB RS485 adapters is a **node on the daisy-chained trunk** (cable in and out of the Pi's location), never a long stub. Keep any tap to the adapter very short. The extra cable counts toward the trunk length limit, and the Pi's connection point is a possible fault point for the whole trunk, so it uses a proper terminal block and is labelled.
  - **MS/TP address (Decided):** on every MS/TP trunk, **MAC 0 is the server and MAC 1 is the Pi HMI**. Both are reserved. Discover and the mapping table refuse to assign them to field devices. The Pi stays silent (not in the token ring) until takeover, then joins as a master. Commissioning records the addresses and checks that no field device uses 0 or 1.
- **[Open]** Sites with more than about 4 serial buses: a second Pi or a serial gateway is needed (limit of USB adapters on one Pi not yet tested).
- Risk: this adds a second control path. It must be proven on the bench, including failback.

## 10. Backup and recovery

- **Local backup by default** (USB/NAS at the site) using Proxmox **vzdump** scheduled backups of the containers: database, configuration, graphics, logic, device templates.
- **Optional off-site backup per site:** a site can switch on automatic off-site backup of configuration only (small, no history), for example pulled to a machine you control over Tailscale or sent to a target the customer chooses. It is off by default and needs the customer's consent.
- **Risk:** if the site is lost (fire, theft, hardware failure with no good backup), configuration and history are lost. This was a deliberate choice; revisit if it becomes a problem.
- Backup schedule and restore procedure to be documented and tested.

## 11. Deployment and updates

Each site server runs **Proxmox VE** with LXC containers. **Decided in milestone 12a (decision 030): two containers, `bms-app` (backend, web app and Mosquitto) and `bms-db` (PostgreSQL + TimescaleDB), on a private bridge, with Tailscale and `tailscale serve` on the Proxmox host** (`deploy/README.md`). The table below is the original five-container plan, kept for reference:

| Container | Contents |
|---|---|
| `bms-backend` | FastAPI backend, BACnet/IP and Modbus drivers, alarm engine, logic runtime |
| `bms-db` | PostgreSQL + TimescaleDB |
| `bms-mqtt` | Mosquitto broker |
| `bms-web` | Web UI and reverse proxy, plain HTTP only (LAN and the Tailscale container behind it) |
| `bms-tailscale` | Tailscale node for remote access. Runs `tailscale serve` to provide HTTPS with a Tailscale certificate (renewed automatically) and passes requests to `bms-web` |

Plus one service **on the Proxmox host**:

| Host service | Contents |
|---|---|
| `bms-mstp` | `bacnet-stack` MS/TP service, one per trunk, with the RS485 adapters attached directly to the host. Exposes a local API (text requests, JSON answers over TCP) to `bms-backend` (decided, milestone 4c; `mstp/README.md`). The BACnet router is separate (milestone 8). |

- Running a service on the host gives the best serial timing for MS/TP. Trade-offs: it sits outside the one-service-per-container layout, and host upgrades can affect it. Mitigation: keep it small, run it as a systemd unit installed by the provisioning script, harden the host, and test after every Proxmox upgrade. **[Assumed]**
- Containers are built from a **standard Debian template plus a provisioning script** kept in `deploy/`, so any site can be rebuilt the same way. No Docker.
- **Updates are triggered by you over Tailscale** to a chosen site. Take a Proxmox snapshot first, run health checks after, and roll back automatically if they fail. The Pi HMI is updated the same way.
- Pis (HMI) run Raspberry Pi OS with a versioned config/package, outside Proxmox. In **standalone mode** the same Pi package also runs the light backend, with `bacnet-stack` (MS/TP) installed on the Pi and no Proxmox or containers (see 3.1).
- Hardware access:
  - Modbus RTU: pass a USB/serial RS485 adapter through to `bms-backend`.
  - BACnet MS/TP: a **separate** RS485 adapter per trunk, attached to the Proxmox host. Use quality isolated adapters with a stable device name (udev rule by serial number). Prefer low-latency adapters over cheap clones.
  - BACnet/IP: `bms-backend` and `bms-mstp` must sit on the device network bridge (BACnet uses broadcasts).
  - Tailscale in LXC needs `/dev/net/tun` enabled on the container.
- **[Assumed]** "Proxmox" means Proxmox VE with LXC containers on the site server.

## 12. Development process

1. **Define the slice.** Pick one thin end-to-end feature (for example, read one device point, store it, show it on the web UI and the HMI).
2. **Write the spec** in this repo (`docs/`) before coding: inputs, outputs, edge cases, failure behaviour.
3. **Build and test locally** against simulated devices first, then real hardware.
4. **Commit small, often**, with clear messages. One feature or fix per branch.
5. **Review and test** before merging: automated tests plus a manual check on the HMI hardware for anything UI or I/O related.
6. **Deploy to the test bench** before any live building.
7. **Record decisions** in section 19 and in `docs/decisions/` as they are made.

### Working with Claude Code
- Keep this file current. It is the shared context.
- Ask Claude Code to propose a plan for a slice before it edits files.
- Prefer small, reviewable changes over large rewrites.
- Claude Code should not invent device addresses, credentials, or protocol details. Ask, or read from config.
- Never commit secrets. Use environment variables or a gitignored `.env`.
- Do not assume a language, framework or database that contradicts a recorded decision.

## 13. Environment

### Development machine
- OS: _TBC_
- Editor/IDE: _TBC_
- Version control: Git, hosted on _TBC_
- Runtimes and package managers: pin versions in the repo.
- Environments: `dev` (developer machines with a native PostgreSQL + TimescaleDB install, plus a Proxmox box or VM that mirrors the site layout for deployment testing), `test` (bench with real hardware), `prod` (sites). **No Docker anywhere**, in development or on sites (decided).

### Raspberry Pi HMI
- Model: **Raspberry Pi 4 or Pi 5** (decided). Exact model and RAM size _TBC_; older 32-bit-only boards (Pi 1, Pi 2, Pi Zero / Zero W) are not supported.
- Screen: 7 inch touch, landscape. Specific model _TBC_. The mockups use 1024 x 600; the HMI layout is tested at both **1024 x 600** and **1280 x 720** (the official Raspberry Pi Touch Display 2, rotated) until the screen is chosen.
- OS: **Raspberry Pi OS (64-bit) only** (decided), minimal install. The 32-bit OS is not supported or tested, even though a Pi 4 or 5 can run it. A Pi that came with 32-bit Raspberry Pi OS is reflashed with the 64-bit image (Raspberry Pi Imager) before install, and the provisioning script refuses to install on a 32-bit OS. This applies to both the HMI and standalone mode.
- Mode: auto-start into kiosk UI on boot, with a watchdog/auto-restart. **Decided (milestone 3):** Raspberry Pi OS Lite (64-bit) with `cage` and Chromium in kiosk mode, a systemd service that restarts it, and the hardware watchdog; the HMI is the web app's HMI layout at `/hmi` (`hmi/README.md`).
- Extra hardware: one **USB RS485 converter per serial bus** (each MS/TP trunk and each Modbus RTU line the HMI must take over), plus Ethernet to the device network. Sites have **at least 2 serial buses**.
- Use quality isolated USB converters on a **powered USB hub**, with stable device names (udev rule by serial number). Plan for up to about 4 converters per Pi. **[Assumed]**
- Storage: quality SD card or SSD; plan for write-wear (log rotation, limited local writes)
- Network: static IP or reserved DHCP on the site network

### Site server
- Industrial/mini PC running Proxmox VE (see section 11). Hardware model: _TBC_. Needs enough USB/serial ports for the RS485 adapters, and two network ports where a separate device network is used.

### Test bench
- Simulated devices for development without hardware.
- A physical bench with at least one real controller, meter and inverter, plus RS485 adapters for Modbus RTU and MS/TP, and a Pi HMI to test failover.

## 14. Repository layout

```
devine-bms/
  README.md
  devine-bms-specification.md   # this file
  docs/
    decisions/         # one short note per decision
    specs/             # one spec per feature
  backend/
  mstp/                # bacnet-stack MS/TP and router service
  web/                 # site web UI, graphics editor, logic editor
  hmi/                 # Pi/kiosk config and HMI failover code
  simulators/          # fake devices for testing
  deploy/              # Proxmox provisioning scripts, container configs, service files
  tests/
```

## 15. Conventions

- Naming: consistent, lowercase, descriptive point/device names (for example `ahu1_supply_temp`).
- Units: SI/metric stored; converted only for display.
- Timestamps: UTC stored; site local time displayed (Australia/Sydney by default).
- Logging: structured logs with levels; no sensitive data in logs.
- Config: all site-specific values (devices, addresses, thresholds) live in config, not code.

## 16. Testing

- **Simulated devices:** software Modbus, BACnet/IP and MQTT simulators for development and automated tests. MS/TP simulation uses a pair of USB RS485 adapters wired back to back, or virtual serial ports.
- **Bench with real gear:** a physical rig with a controller, meter and inverter.
- **Failover tests:** stop the site server and confirm the HMI detects it, takes over, logs commands, and hands back cleanly. Test that server and HMI never write together.
- **Logic tests:** run block-editor logic against simulated points before it controls real equipment.
- Automated tests are strongly recommended for the alarm, schedule, logic and command-limit code.
- **Haystack conformance tests:** read the model, live values and history through a Haystack client library, and check every entity against the Haystack and house defs (5.12).
- Pilot site not selected. Recommend the first live site be low risk, with BMS control limited at first.

### Bench-test plan (prove these first, in this order)

Each test has a pass mark. Do not build features on top of a test until it passes. Pass marks are proposals.

| # | Test | What to prove | Pass mark |
|---|---|---|---|
| 1 | Modbus RTU and TCP baseline | Poll real meter/inverter, store values, show a trend | 24 hours with no missed polls beyond the configured retry |
| 2 | BACnet/IP | Who-Is discovery, read/write with priority arrays, COV | Reads and writes match what the controller shows |
| 3 | **Native MS/TP on the Proxmox host** | `bacnet-stack` as master on a real trunk with several devices | 72 hours with no token loss and near-zero CRC errors; discovery finds all devices |
| 4 | MS/TP at load | Poll roughly the full point list for the largest planned trunk at the planned interval | No growth in retries or timeouts; no missed polling cycles |
| 5 | Serial adapter stability | Quality USB RS485 converters keep their device names across reboot and unplug | Same device name every time (udev rule works) |
| 6 | BACnet router | Route between an MS/TP trunk and BACnet/IP; a laptop BACnet tool sees MS/TP devices | Stable for 24 hours; documented network-number plan works |
| 7 | Schedule objects | Find which bench controllers support schedule objects; push a BMS schedule; detect a local change | Push succeeds, local edit is flagged |
| 8 | **HMI takeover** | Stop the site server; HMI takes over BACnet and Modbus control after 30 seconds | Automatic takeover within 30 seconds of heartbeat loss only when the buses are clear; manual commands work on both protocols |
| 9 | **HMI failback** | Restart the server; operator hands back | Server and HMI never write together; HMI overrides cleared; server resumes |
| 10 | HMI wiring | USB RS485 converters tapped onto an MS/TP trunk and a Modbus RTU line, HMI silent until takeover, Pi at the far end of the cable (not beside the server), MAC 0 server and MAC 1 Pi | No bus disturbance while the HMI is in standby; takeover blocked while a server master is still heard |
| 11 | Pi serial capacity | Run the HMI with 2, then 4 USB RS485 converters on a powered hub | Stable at 4; note the limit |
| 12 | Tailscale remote access | Dashboard reachable over Tailscale from a phone and a laptop; ACLs block other sites | Correct access and blocking; updates can be pushed remotely with rollback |
| 13 | Power loss | Pull power on the server, HMI and a controller | Everything self-recovers with no manual steps |
| 14 | Alarm delivery | Email, SMS plug-in and ntfy push from a site | Delivered within the 1-minute target |
| 15 | HMI graphics performance | Show a full-screen site graphic with about 40 animated symbols and animated pipes on the 7 inch screen for 24 hours | Smooth animation, steady CPU and memory, touch response under 200 ms |
| 16 | Standalone mode on a Pi | Run the light backend, MS/TP, Modbus and BACnet/IP polling about 50 points plus the in-memory logger on a Pi for 7 days with a read-only root | Stable polling, RAM use steady, no writes to the card during running, recovers from power loss with config intact. USB export writes on schedule, survives the stick being pulled and replaced, and catches up |
| 17 | **Logic fail-safe** | Run a logic diagram that writes a real output (a BACnet point with priority and a Modbus point). Disable it, stop the server, and feed it a Bad input | The output releases to the device (BACnet priority relinquished, Modbus device default) or goes to its set fail-safe within the set time; a PID returns to auto without a jump (5.3.1) |
| 18 | **Polling load** | Poll the full point list on the longest planned RS485 bus (Modbus RTU and MS/TP) using the Fast, Normal and Slow classes, with block reads and COV where supported, while sending commands | Fast points meet their interval; commands go out ahead of polling; the Slow class delays first when the bus is full; the health page shows the load (5.1.4) |
| 19 | **Standalone limits with logic** | On a Pi in standalone mode, run about 50 points, the logic runtime with several diagrams (including PID) and the web app on a laptop and phone together for 7 days | Stable cycle time and polling; web app stays responsive; no writes to the SD card in normal running except counter saves (5.10); record the diagram and block limit (open item 16) |

If test 18 fails on a long bus, split the bus or lengthen the Slow and Normal intervals before building more. If test 3 or 4 fails on the host, fall back to a dedicated serial gateway before building more. If tests 8 to 11 fail, reconsider the takeover design (for example a second Pi, or a serial gateway) before building the HMI further.

## 17. Non-functional targets (proposed)

| Item | Target |
|---|---|
| Points per site | Up to 1,000 |
| Live value refresh in UI | 1 to 5 seconds |
| Alarm detection to notification | Under 1 minute |
| HMI boot to usable | Under 60 seconds |
| HMI detects server failure | 30 seconds (decided) |
| Site uptime goal | Self-recovers from power loss with no manual steps |

All figures are proposals and should be adjusted.

## 18. Milestones

1. Repo, environment and this specification in place; decisions recorded.
2. Simulated Modbus device through to stored data and a trend on the web UI.
3. Pi HMI boots into the UI.
4. Real device on the bench (Modbus first, then BACnet/IP, then **native BACnet MS/TP on the host**, then MQTT). Work through the **bench-test plan in section 16**. Prove MS/TP stability early, because it is the highest technical risk.
5. Auto-discovery and point setup UI.
6. Alarms with email, SMS and ntfy push, plus the audit log.
7. Calendar schedules, controller schedule writing and override timers.
8. BACnet router between MS/TP and BACnet/IP.
9. Graphics editor and symbol library with custom symbols and SVG import.
10. Visual logic editor and logic runtime.
11. HMI standby takeover and failback, proven on the bench.
11a. Standalone mode (Pi only): install option, light backend profile, basic screens, logger, configuration export and import.
12. Remote access over Tailscale with per-site accounts, ACLs and remote updates.
12a. Haystack API (read-only) and Haystack model export (5.12). The Haystack data model itself is built from milestone 2 onwards.
13. Pilot site.
13b. **Documentation (decided: written at the end; written 8 Nov 2026 in `docs/guide/`, Markdown in the repository, installer and support guides separate from plain-language customer guides):** an installation guide (development machine, site server, Pi HMI, MS/TP service), a settings reference (environment variables, config file, site and security settings), a users and logins guide (first admin, roles, 2FA, lockout, kiosks, allowed networks, password reset, audit log), and commissioning and operating guides. Until then each milestone spec keeps a complete "how to set up and run" section, and `docs/guide-notes.md` lists what the guide must cover.
14. **Later (not scheduled):** in-house ESP32 I/O module (section 20, F1). No BMS code is planned for it; it is added as a standard device through a template.

## 19. Decisions register

### Decided
| Decision | Choice |
|---|---|
| Audience | Border to Border clients first, possibly sold on to others |
| Building systems in v1 | HVAC/refrigeration, power metering, solar/battery, lighting and general I/O |
| Protocols | Modbus RTU/TCP, BACnet/IP, native BACnet MS/TP, MQTT, vendor APIs |
| Architecture | Standalone site servers. **No cloud portal.** |
| Hosting | Server on each site (Proxmox, LXC containers, no Docker) |
| MS/TP stack location | `bacnet-stack` on the Proxmox host |
| BACnet router | Yes, in v1 |
| Remote access | Dashboard over Tailscale |
| Tailnets and support access | **One tailnet per customer** (one tag per site, a generated policy with tests); each site server is **shared into the support tailnet** (decision 030, milestone 12a) |
| Site server containers | **Two LXC containers**: `bms-app` (backend, web app, Mosquitto) and `bms-db` (PostgreSQL 16 + TimescaleDB), on a private bridge; **Tailscale and `tailscale serve` on the Proxmox host**. Replaces the five-container layout in section 11 (decision 030) |
| HTTPS for remote users | `tailscale serve` in the `bms-tailscale` container; `bms-web` is plain HTTP only |
| User accounts | Per site server, roles per site |
| Control logic | Mixed: device-controlled and BMS-controlled; authored in a visual block editor |
| Schedules | Calendar-based; BMS plus written to controllers |
| Data model | Site > Equipment > Point, plus tags; rooms are spaces that can hold points directly; optional location, **part of** (equipment inside equipment) and **feeds** (air, water, electricity, refrigerant) links (5.2) |
| Point naming | `<room>_<equipment>_<function>`, unique on the site; on a room change the BMS offers to rename with a preview, IDs stay the same and old names remain as aliases (5.2) |
| Point types | sensor, sp, cmd (Haystack), each with a Haystack `kind`: Number, Bool, or Str with `enum` for multistate; configuration points are flagged, not a separate type (5.2) |
| Tag vocabulary | Project Haystack 4 standard library plus a house def library for missing meanings; picked from a menu; versioned files in the repository; site admins can add site-only custom tags, marked custom and not used by templates (5.2) |
| Haystack compatibility | Pinned to haystack-defs v4.0.0 (not Xeto); tag sets and house library in Appendix A. Data model is Haystack 4 (site, space, equip, point, refs); read-only Haystack HTTP API with SCRAM login, off by default; no pointWrite in v1 (5.12) |
| Haystack API logins | **API client accounts only** (viewer rights, no web or HMI login); people's accounts cannot use the API (decision 032, milestone 12a-Haystack) |
| Discover screen | Scan per network (BACnet/IP, MS/TP, Modbus TCP and RTU), results listed live, click or multi-select to add; template suggested from vendor, model and object signature; Modbus devices without an ID need a template picked by hand (5.1.3) |
| Scan during HMI takeover | Allowed with a warning and confirmation; not blocked (5.1.3) |
| Add several devices | Add selected adds several discovered devices in one go; each is tested and failures stay in the list (5.1.3) |
| Logic editor | Function block diagram in the browser with edit and live modes, blocks bound to template roles, fixed-cycle runtime, per-output fail-safe (default release to device), checks and versioned publish (5.3.1) |
| Equipment templates | Equipment is made from reusable templates (point roles, tags, limits, alarms, graphic, logic); instances only change finer detail; template edits apply to all instances except overridden settings; templates are versioned (5.1.2) |
| Multi-equipment logic | One-off site diagrams that pick points from any equipment; no reusable multi-equipment templates in v1 (5.3.1) |
| Template graphics | Copied per placement; copies edited freely and not updated by template changes; stay bound to the instance by role; Replace from template available (5.1.2) |
| Template roll-out | Side-by-side difference, then update all, a group or one instance at a time; instances keep their version until updated; template logic follows the same roll-out (5.1.2, 5.3.1) |
| Template overrides | An override on an instance always wins over a template change; overrides are listed and can be reset one at a time |
| Starter equipment templates | Indoor unit, AHU, room, pump, fan, exhaust fan, ERV, light circuit, power meter |
| Commissioning | Auto-discovery plus UI |
| HMI offline | View and command only |
| HMI on server failure | Automatic standby takeover (after heartbeat loss and a clear bus) through its own BACnet and Modbus connections, manual failback, manual control only (no BMS logic or schedules) |
| HMI login | View open, PIN or login to command |
| HMI screen | 7 inch touch, 1024 x 600 landscape; mockups in section 6.3 |
| Pi hardware and OS | Raspberry Pi 4 or 5 with 64-bit Raspberry Pi OS only; 32-bit OS and 32-bit-only boards not supported (13) |
| Alarms | Email, SMS gateway API, ntfy push |
| Schedule master | The BMS (overwrites controller schedules, flags local changes) |
| Login | Local accounts per site, optional 2FA |
| HMI heartbeat timeout | 30 seconds before takeover |
| HMI takeover trigger | Automatic after heartbeat loss and a clear bus (60 s of Modbus silence, no server token on MS/TP); manual failback |
| HMI placement and MS/TP addresses | Pi away from the server, wired as a daisy-chain node; MAC 0 server, MAC 1 Pi, both reserved |
| HMI failback | Operator confirms after the server has been healthy for a few minutes |
| Retention | 30 days full, 1 year hourly, 5 years daily |
| Backup | Local by default (Proxmox vzdump); optional off-site config backup per site |
| Updates | Triggered by you over Tailscale, snapshot and rollback |
| Reporting v1 | Live dashboards, trends and CSV export; no separate energy reports in v1 (5.7) |
| UI | Phone-first for overview, alarms and the graphics tabs; desktop-first for trends; editors and setup screens desktop or tablet (6.1). Light/dark switch, branding per site, built-in graphics editor |
| Stack | Python/FastAPI, PostgreSQL + TimescaleDB, Mosquitto, React + TypeScript, bacnet-stack, React Flow for the logic editor; SQLAlchemy 2 + Alembic for database access and migrations; uPlot for trend charts |
| Development setup | Native PostgreSQL + TimescaleDB on developer machines; no Docker anywhere |
| History storage | A row on every poll for logged points, plus one on each status change; no change-of-value storage |
| Graphics editing | Web app only; HMI shows graphics, view only |
| Static elements and colour rules | Pipes and ducts take colour from a symbol state, value bands or proportional fill; set per element in the web app |
| Tap to control | Tapping a symbol on a process diagram opens a control panel; PIN or login required; follows 5.6 command rules |
| Room shape | Rectangles only (squares included) in v1 |
| System graphics | Process diagrams and rooms are one graphic type (system graphics); rooms are squares with live values and a colour rule |
| Hand mode timer | Operator picks 1, 2 or 8 hours, default 2 hours; site can change the default and choices |
| Standalone overrides after power loss | Return to Auto; hand state is not saved to the SD card |
| Commissioning | 4-step guided procedure in the web app: add device and test; create equipment from a template in one editable table (points, grouping, room, names, tags); verify and go live with template defaults shown as a checklist; optional place on graphics (5.1.1) |
| Room assignment | Equipment and points carry an optional location; a point inherits its equipment's room unless set directly |
| Graphics tabs, both modes | Whole-site graphic on a Site tab plus a tab per room or section; full mode keeps its bottom bar of functions |
| Standalone navigation | Tabs: a Site tab with a whole-site graphic, then a tab per room or section; bottom bar for Overrides, Schedules and Log |
| Standalone logging | Memory only, no writes to the SD card during normal running; config saved to the card only on change; optional scheduled export of the RAM log to a USB stick left plugged in |
| Deployment modes | Full mode (server plus Pi HMI) and Standalone mode (Pi only, for small sites), chosen at install; standalone can be upgraded by importing its configuration |
| Graphics symbols | Shipped library plus per-site custom symbols (draw or import SVG); states Off, On (animated), Fault, Offline, Manual |
| Standards | None formal for now |
| Testing | Simulated devices and a bench with real gear |

### Open
1. Commercial terms if sold on: software licence, support and warranty terms, component licence check, and whether BACnet testing or listing is needed.
2. (Decided: Pi is a daisy-chain node, MS/TP MAC 0 server and 1 Pi, automatic takeover after a clear-bus check, manual failback.) Still open: sites with more than about 4 serial buses need a different approach (for example a second Pi or a serial gateway).
3. Failback details: the exact health checks and the healthy period (proposed 5 minutes) before the operator can hand back.
4. Logic runtime design (block set, execution model, safety limits) and testing approach. The editor library is React Flow.
5. Which controllers support schedule objects (found on the bench).
6. BACnet network-number plan and router configuration per site.
7. Confirm MS/TP timing and router stability on the Proxmox host on the bench.
8. Off-site backup target and method (a machine you control over Tailscale, or customer-chosen storage).
9. Symbol library scope for v1 (the full set of equipment symbols) and the exact SVG import rules (size limit, allowed elements, animation features).
10. Whether the HMI reduces animation automatically on low frame rate, and how the editor previews it for the Pi.
11. Pipe and duct colour palette per pipe type (chilled water, hot water, refrigerant, supply air and so on) and whether a site can restyle them.
12. Which values a room can show (temperature, setpoint, humidity, damper position, occupancy and similar).
13. Which symbols are tappable by default, and whether the control panel is the same on the web app and the HMI.
14. Standalone mode limits: point count, protocols supported on a Pi, local history retention, and which alarms (ntfy, email) are included.
15. Standalone mode memory and storage: log window length and sample rate, the export interval and file layout, whether restore on boot is included, and the Pi model (a Pi 5 is proposed). Memory-only logging is decided; the read-only root filesystem is proposed.
16. Standalone logic limits: number of diagrams and blocks, cycle time on a Pi, and whether the touch screen should edit variables or logic beyond viewing (proposed: view state and variables only). (Decided: the block editor is available in the web app in standalone mode.)
17. (Decided, 5.2: pattern `<room>_<equipment>_<function>`; on a room change the BMS offers to rename with a preview, keeps a hidden permanent ID and keeps old names as aliases.)
18. Whether the commissioning table can be edited on the Pi touch screen in standalone mode, or the web app only (proposed: web app only).
19. How to assign rooms for gateway devices that give only numbers (for example Mitsubishi group numbers): name import from a spreadsheet, or typed in the wizard.
20. (Decided, 5.2: tags from the Haystack-style house list with site custom tags; relations are location, part of and feeds; point types are sensor, sp and cmd with a value kind.)
21. (Decided, 5.1.2: side-by-side difference, then update all, a group or one instance at a time; template logic follows the same roll-out; an override always wins.)
22. (Decided, 5.1.2: a template graphic is copied per placement, edited freely there, and not changed by later template edits; Replace from template is available.)
23. Which of the supported devices report enough identity over BACnet (vendor, model, object list) for an automatic template match, and Modbus device identification support on H1 and H2 (found on the bench).
24. Scan limits and timeouts for each bus type, how a scan on a live MS/TP trunk or Modbus RTU line is kept from disturbing control, (Decided: scanning during HMI takeover is warned about, not blocked.)
25. Execution cycle time and how timer state survives a restart. (The v1 block set is decided: compare, limit, scale, timer delays, astro time switch, PID, manual/auto, variables, and basic logic and point blocks.)
26. How the fail-safe is shown on the HMI and on graphics. (Decided: the default is release to device; a block can choose hold last value or a stated value.)
27. (Decided, 5.3.1: multi-equipment logic uses one-off site diagrams that pick points directly; reusable multi-equipment templates are not in v1.)
28. Whether the astro time switch is also offered in the schedules (5.4), and whether holidays or manual dates can override it. (It is available in standalone mode through the block editor.)
29. Published schema details: block type names, port names, versioning rules between schema versions, and whether a safe expression or code block is ever allowed (not in v1).
30. Whether snippets are stored per site, shared between sites by file, or both.
31. In-house ESP32 I/O module: protocol, I/O mix, stack, and compliance route (section 20, F1). Not scheduled.
32. Which networks may reach the web app on the LAN, how that is set in the settings, and whether the customer must sign off on credentials crossing their LAN in clear (section 8).
33. Whether a listen-only RS485 adapter is offered for whole-bus capture, and the buffer size and time limit of the traffic viewer on the Pi (5.9).
34. Standalone counters and the SD card: how often counters are written, and what is lost on power loss between writes (5.10). Which supported devices report their own run hours.
35. First-boot network: the exact fallback address and what happens if it clashes with something on the site network (3.1). (Decided: DHCP first, then a fixed fallback address.)
36. Exact default intervals for each speed class, the safe block size for each supported device, and how bus load is estimated (5.1.4). Proven on the bench.
37. BACnet server (5.11): device ID and network number plan, whether live point values are exposed as well as Schedule, Calendar and Trend Log, ReadRange support in the stack, and whether outside writes are ever allowed. (Decided: read-only in v1, off by default per point, for another BMS on a large site.)
38. Modbus serial-to-Ethernet gateways: which models are supported, Modbus TCP versus RTU-over-TCP framing, connection limits, timeouts, and bench-testing a gateway under server and Pi takeover. (Decided: gateways are supported as a connection type.)
39. Haystack details (5.12): `hisRead` and watch limits, the client library used for tests, and whether `pointWrite` is ever offered. (Decided: Haystack 4.0.0, not the newer Xeto format, with a read-only API; tag sets and house library in Appendix A.)

### Open item priority

Each open item above is sorted by when it must be settled. This is a first pass from the dependencies between items. Confirm it before build starts.

| When | Items | Why |
|---|---|---|
| **Before build starts** (they shape the database and data model) | None left (17, 20, 21, 22, 27 and the Appendix A tags are decided) | Changing these later means rewriting the schema and every saved template or diagram. |
| **During build, with the feature** | 3, 4, 6, 8, 9, 10, 11, 12, 13, 18, 19, 25, 26, 28, 29, 30, 32, 33, 34, 35, 37, 39 | Each is local to one feature or milestone and can be decided when that milestone starts. |
| **Proven on the bench** | 2, 5, 7, 14, 15, 16, 23, 24, 36, 38 | The answer comes from testing real gear or the Pi, not from a decision. |
| **Before selling or the first customer site** | 1 | Commercial and licence terms. Does not block building. |
| **Later** | 31 | In-house ESP32 module is not scheduled. |

## 20. Supported hardware

A running list of field hardware the BMS must support. The list records technical support only (comms, point map, limits and watch-outs), not where a device is used. Each device gets a built-in **device template** (point map, comms settings, units and scaling) so it can be added from the settings screen, using the tools in 5.1. Status values: **Datasheet only** (read from the manual, not tested), **Bench tested**, **In service**.

| ID | Device | Type | Protocols | Standalone mode | Status |
|---|---|---|---|---|---|
| H1 | CWT-MB328B(II) Modbus I/O module (variants -S and -SE) | Digital I/O, 8 inputs and 18 relay outputs | Modbus RTU (RS485), Modbus TCP (-SE) | Yes (Modbus RTU through a USB RS485 adapter, or Modbus TCP) | Datasheet only |
| H2 | Schneider Electric SXWRIOF16EM10001 (EasyLogic RP-IO-16E-M-24V) | BACnet MS/TP I/O expansion module, 16 universal inputs/outputs | BACnet MS/TP (RS485) | Yes (MS/TP through a USB RS485 adapter and `bacnet-stack` on the Pi) | Web specs only; datasheet and object list needed |
| H3 | Smart Temp SMT-IO (SMT-IO-GS, Modbus/BACnet I/O module) | I/O module: 8 universal inputs, 2 analog outputs, 10 relay outputs | BACnet MS/TP or Modbus RTU (RS485, chosen by DIP switch) | Yes (USB RS485 adapter) | BACnet object list read; Modbus map and baud rates not provided |
| H4 | Metz Connect BMT-AI8 (BTR Netcom), BACnet MS/TP analog input module | 8 configurable inputs (voltage or resistance, many temperature sensor types) | BACnet MS/TP (RS485) | Yes (USB RS485 adapter) | Datasheet read (4 pages); not bench tested |
| H5 | Mitsubishi Electric AE-C400E centralised controller (with optional EW-C50E expansion) | Centralised controller for Mitsubishi Electric air conditioning (City Multi, M-series, Mr Slim, Lossnay, e-Series) | BACnet (licence PIN required); M-NET to the units | Yes (BACnet/IP over Ethernet) | Brochure and article read; BACnet object list needed |
| H6 | Mitsubishi Electric BAC-HD150 BM adapter (BACnet gateway for M-NET) | Air conditioning to BACnet gateway: up to 50 indoor units per M-NET, 150 with PAC-YG50ECA expansion controllers | BACnet/IP, UDP 47808 (ANSI/ASHRAE 135-2004), 10BASE-T | Yes (LAN) | Instruction book read (sections 1 to 5, pages 1 to 16 of 40; M-NET setup sections not reviewed) |
| F1 | **Future:** in-house ESP32 I/O module (custom design) | Digital and analog I/O on an ESP32 dev board, designed and built in-house | Modbus RTU/TCP and BACnet (RTU, TCP or MS/TP to be chosen) | Yes, as any standard device | **Planned, not built.** Room left in the spec only |

### H1: CWT-MB328B(II) Modbus I/O module

Source: CWT (Comwintop) manual, 5 pages, supplied by Jason.

| Item | Detail |
|---|---|
| Variants | **-S:** RS485 + RS232, Modbus RTU. **-SE:** Ethernet + RS485 + RS232, Modbus TCP and Modbus RTU. |
| Inputs | 8 digital inputs, opto-isolated. ON is 5 to 30 VDC (6 mA at 24 VDC), OFF is 0 to 3 VDC. |
| Outputs | 18 normally-open relay outputs, 5 A at 250 VAC or 30 VDC (resistive). Response 10 ms or less. |
| Power | 7 to 35 VDC, about 2 W, reverse-polarity protected, isolated. |
| Mounting and environment | DIN rail or screw fixing, IP20, -40 to 85 °C, 5 to 95 % RH non-condensing. 158 x 111.5 x 38.5 mm. |
| RS485 / RS232 defaults | 9600 baud, no parity, 8 data bits, 1 stop bit, **slave ID 1**. Baud 1200 to 115200, parity even, odd or none, 1 or 2 stop bits. Isolated, with surge, ESD and overvoltage protection. |
| Ethernet (-SE) defaults | RJ45, 10/100 Mbps, **IP 192.168.1.1, port 502**, Modbus TCP and Modbus UDP. |
| Digital inputs map | DI0 to DI7 are **discrete inputs 10001 to 10008**, function code **02**. |
| Relay outputs map | DO0 to DO17 are **coils 00001 to 00018**, function codes **05** (write single) and **15** (write multiple). |

**Point template for the BMS (proposed):**
- 8 binary inputs: `DI0` to `DI7` (FC02, protocol addresses 0 to 7).
- 18 binary outputs: `DO0` to `DO17` (FC05 and FC15, protocol addresses 0 to 17), with command priority, hand/auto and override timer support from sections 5.6 and 6.4.
- The module keeps no schedules or logic of its own, so it is BMS-controlled only.

**Driver requirements this device adds:**
- The Modbus driver must support **discrete inputs (FC02)** and **coils (FC01 read if available, FC05 and FC15 write)**, not only holding and input registers.
- Handle the **address offset** correctly: the manual uses 1-based "00001" and "10001" style addresses, while the protocol address on the wire starts at 0. The template stores the wire address and shows the manual address.
- **Fail-safe output state:** the manual does not say what the relays do on communications loss or power-up. This must be tested on the bench and recorded. Until then, do not use these outputs for anything where a stuck-on relay is unsafe.

**Notes and watch-outs:**
- **Inputs are wet (voltage-sensing).** They need 5 to 30 VDC across them. A dry contact needs a supply wired through it. They are not for mains voltage.
- **Relays are resistive-rated.** Switch motors, compressors and large lights through contactors, and use the relay only to drive the coil.
- **Every unit ships as slave ID 1, and every -SE as 192.168.1.1.** Each unit must be given a unique ID or IP before it joins a bus or network. The commissioning screen should detect and warn about duplicates.
- Modbus RTU is single-master, so section 9's rules apply (the Pi HMI stays silent until takeover).
- The manual does not describe: reading back output state, a watchdog or timeout, pulse counting, or how to change the settings. Check the vendor's configuration tool and any extra document.

**Open questions for H1:**
- Can the coil state be read back (FC01) and does it match the physical relay?
- What do the relays do on loss of communications and at power-up?
- How are slave ID, baud rate and IP set (vendor tool, DIP switches, Modbus registers)?
- How many outputs can be on at once with the supply used (2 W is listed, relay coils add load)?

### H2: Schneider SXWRIOF16EM10001 (EasyLogic RP-IO-16E-M-24V)

Sources: Schneider Electric product page and installation sheet (the 02-50064 series, covering RP-IO-12A-M and RP-IO-16E-M). **The BACnet object list, baud rates and configuration tool are not in what I could read.** The product datasheet and instruction sheet 02-50064-02-en are still needed.

| Item | Detail |
|---|---|
| Role | I/O expansion for HVAC over **BACnet MS/TP** |
| I/O | **16 universal input/output channels** (Ub) on the 16E-M. No relay outputs. (The sister model SXWRIOF12AM10001 has 8 universal plus 4 solid-state relay outputs.) |
| Inputs | Universal inputs rated 24 VDC, 2.4 mA. The exact input types (voltage, current, thermistor, resistance, dry contact) are not stated in what I read. |
| Outputs | Voltage output 0 to 10 VDC into 2.4 kΩ or more, per channel when configured as an output |
| Power | 24 VAC (50/60 Hz, 7 VA) or 24 to 30 VDC (3 W) |
| Communications | One RS485 port on a 3-pole screw terminal |
| MS/TP address | Set on a **7-position DIP switch**, values 1, 2, 4, 8, 16, 32 and 64 (the sum is the address). Each device needs a unique address. |
| Environment | 0 to 50 °C, up to 95 % RH non-condensing, IP20 |
| Mounting | DIN rail or flat surface. On a vertical DIN rail the end clip SXWDINEND10001 is required. |
| Wiring | SELV/PELV devices only on the universal I/O. Screw terminal torque 0.56 to 0.79 N·m. |

**What this adds for the BMS:**
- The device is found by **Who-Is on the MS/TP trunk** (5.1) and its objects are read through the normal BACnet object and property tools. No special driver is needed if it follows standard BACnet. The device template stores its object names, units and the **input type configuration** for each channel.
- **Channel type must be set per channel** (analog input, digital input, voltage output). The BMS should read and show the configured type from the object properties and warn if a point is used as something it is not configured for. How the channel type is set (a Schneider tool, BACnet properties or the controller's own configuration) is **not yet known**.
- MAC addresses are set by DIP switch, so the commissioning screen must show duplicate MAC addresses clearly (the DIP value is also the device's address on the trunk).
- Baud rate and termination are not stated here. Set the trunk baud rate to match the other devices, and check the Schneider hardware reference guide for termination and bias.

**Open questions for H2:**
- BACnet object list: object types and instances for the 16 channels, units, priority array support on outputs, and whether COV is supported.
- How is the channel type configured, and with which tool? Can the BMS read or write it over BACnet?
- Supported MS/TP baud rates (and whether it auto-detects), and max master settings.
- Is it an MS/TP master or a slave-only node, and how many devices fit on the trunk with it?
- Output behaviour on loss of communications and at power-up.

### H3: Smart Temp SMT-IO (SMT-IO-GS)

Source: *Smart Temp SMT-IO Rev2 BACnet Object List V2.0* (2 pages, supplied by Jason), which includes the board layout sheet (REV 4, 20 May 2017, BACnet vendor ID 165). Smart Temp Australia P/L, Knoxfield, Victoria.

| Item | Detail |
|---|---|
| Inputs | **8 universal inputs.** Each input has a **hardware switch** that chooses between a **10K Type 2 thermistor or volt-free contact (Th)** and a **0 to 10 VDC input**. |
| Analog outputs | 2 outputs, 0 to 10 VDC (AO1, AO2) |
| Relay outputs | **10 relays**, 5 A each, NO, COM and NC contacts |
| Relay hand switches | Each relay has a **3-position hardware switch: On (forced on), Auto (controlled through the protocol), Off (forced off)** |
| Power | 24 VDC, 0.5 A |
| Communications | One RS485 port. **BACnet MS/TP or Modbus RTU, chosen by DIP switch 7** (On is BACnet, Off is Modbus). |
| Address | DIP switches 1 to 6 give the address in binary, **1 to 63** (0 is not used). Switch 8 is the **end-of-line resistor**. |
| Switching protocol | Changing from BACnet to Modbus requires writing 1 to **register 30, Restore Defaults**. |
| Status LED | Flashing means running mode |

**BACnet object list (MS/TP mode):**

| Type | Objects | Meaning | Access |
|---|---|---|---|
| AI | AI1 to AI8 | 10K thermistor temperature, -40 to 89 °C (-40 to 192 °F) | Read |
| BI | BI1 to BI8 | Volt-free contact inputs, 1 on, 0 off | Read (listed R/W) |
| AV | AV1 to AV8 | 0 to 10 V inputs, 0 to 100 % | Read |
| AO | AO1, AO2 | 0 to 10 V outputs, 0 to 100 % | Read/Write |
| BO | BO1 to BO10 | Relay outputs, 1 on, 0 off | Read/Write |
| AV | AV9 | **Device instance** (165XXX) | Read/Write |
| AV | AV10 | AI COV increment (listed as not in use) | Read/Write |
| AV | AV11 to AV18 | **Delay for digital inputs** BI1 to BI8, 0 to 3600 s | Read/Write |
| AV | AV19 to AV28 | **Delay for digital outputs** BO1 to BO10, 0 to 3600 s | Read/Write |
| BV | BV1 | Celsius (1 on, 0 off; default 0 is the C scale) | Read/Write |
| BV | BV2 | Restore default values | Read/Write |

**What this adds for the BMS:**
- **Each physical input shows up as up to three objects** (AI, BI and AV with the same number), but only the one that matches the **hardware switch** carries real data. The device template must record the **input type per channel** (the BMS cannot read the switch), and the point setup should create only the matching point.
- **Hand/Off/Auto switches on the relays cannot be read.** A relay forced On or Off at the board ignores the BMS. The BMS should show the relay's last commanded state, ideally paired with a status input (such as a contactor auxiliary contact) to detect a forced output. Record this in the device notes.
- **Settings can be configured from the BMS**, as 5.1 requires: the **device instance (AV9)**, the **input and output delays (AV11 to AV28)** and the **Celsius/Fahrenheit scale (BV1)** are writable. Put them in the template as configuration points with a confirm step. Changing AV9 changes the device's identity on the trunk, so it needs an extra warning. Keep **BV2 (Restore Defaults)** behind an extra confirm too, since it resets the device.
- **Dual protocol:** the same hardware can run MS/TP or Modbus RTU, so the template library needs a template for each. The Modbus register map is **not in the document** (only register 30), so the Modbus template needs the vendor's Modbus map.
- **Address and termination:** the address comes from DIP switches 1 to 6 (so up to 63 on one trunk). Show duplicate addresses, and make the end-of-line setting (switch 8) part of the commissioning checklist for the last device on a trunk.
- Temperature values are in °C by default, so the BMS should read BV1 first and convert if it is set to °F.

**Open questions for H3:**
- Supported MS/TP baud rates, max master setting and whether it is a master or a slave-only node.
- The Modbus RTU register map (and what register 30 does beyond restore defaults).
- Do the BO and AO objects support a BACnet priority array and relinquish default? What do the outputs do on loss of communications and at power-up?
- Is a BI read-only, or does writing it do something (the list shows R/W)?
- What is the exact meaning of the output delays (delay on make, delay on break, or both)?

### H4: Metz Connect BMT-AI8 (BTR Netcom GmbH)

Source: *BMT-AI8 BACnet MS/TP analog input module* instruction sheet and software description (4 pages, supplied by Jason), manufacturer METZ CONNECT GmbH (the BACnet vendor name is "BTR Netcom GmbH").

| Item | Detail |
|---|---|
| Function | 8 individually configurable inputs for resistance or voltage: passive and active temperature sensors, valve and damper positions, and similar |
| Voltage input | 0 to 10 VDC, resolution 10 mV, error about ±100 mV |
| Resistance input | 40 Ω to 4 MΩ |
| Sensor supply | Jumper chooses 15 VDC (terminals S) for active sensors, or 24 V AC/DC. Pull-up options of 2 kΩ to 5 V. |
| Power | 20 to 28 VAC/DC (24 V), 65 mA AC or 25 mA DC. Up to **15 modules** side by side on one supply connection (2 A limit) using the jumper plug. |
| Communications | One RS485 BACnet MS/TP port, **9600 to 115200 baud** (factory 9600). Terminate with **120 Ω** at the bus ends. |
| Address | Two hex rotary switches (x10 and x1), **00 to F9**. At or below Max_Master it is an **MS/TP master**; above it, a **slave**. |
| Baud rate | Set with the same switches: x10 to E, x1 to A to F for 9600, 19200, 38400, 57600, 76800 or 115200, then x10 to F for 1 second. Both LEDs blink when it is stored. |
| LEDs | Green for operation and bus activity, red for error |
| Environment | -5 to 55 °C (storage -20 to 70 °C), housing IP40, terminals IP20 |
| Mounting | TH35 DIN rail, 50 x 70 x 65 mm, 104 g |

**BACnet identity and services:** vendor ID **421**, model "BMT-AI8", firmware 1.2, BACnet protocol revision 12. Default device instance is **421000 + network address**. It supports ReadProperty, WriteProperty, **SubscribeCOV (up to 10 subscriptions on AI 1 to 8)**, Who-Is, Who-Has, DeviceCommunicationControl and ReinitializeDevice. No segmentation, max APDU 480.

**Objects (49 in total):** Device; **Analog Input 1 to 8** (measured value, units and range from the matching multistate value); **Analog Value 1 to 20** (the user-defined interpolation table, X and Y values); **Analog Value 21 to 28** (per-input **offset** added to the measured value); **Multistate Value 1 to 8** (measurement range for each input); **Multistate Value 9** (user-defined range type); **Group 1 to 3** (read several inputs at once).

**Input types selectable per channel (Multistate Value 1 to 8):** 0 to 10 V as a percentage or in volts (each with a pull-up variant), resistance in ohms, a user-defined range, and standard temperature sensors **PT100, PT500, PT1000, Ni1000 (TC5000 and TC6180), Balco 500, KTY81-110, KTY81-210, NTC 1k8, NTC 5k, NTC 10k, NTC 20k and LM235Z**. Temperatures are in °C.

**What this adds for the BMS:**
- **Everything is configured over BACnet,** with no switches or special tool for the input types. This fits the settings-screen requirement in 5.1: the template exposes **measurement range per input** (Multistate Value 1 to 8, stored in the device's EEPROM), **offset per input** (AV 21 to 28), **COV increment**, **object names** and **max master and max info frames** (Device object). Writes use the same confirm step and audit log as other configuration points.
- **Read the configured range first.** The BMS reads the Multistate Value for each channel and shows the sensor type and units next to each input, so a wrongly configured input is obvious.
- **Out_Of_Service:** an Analog Input's Present_Value is writable when Out_Of_Service is true. The BMS must flag any input in this state, because it is a simulated value.
- **User-defined sensors:** a custom curve is entered as up to 10 X/Y pairs (for example temperature against resistance) in AV 1 to 20, with MV 9 choosing linear or exponential interpolation. The template builder can offer a "write custom curve" helper.
- **COV support** means the BMS can subscribe to the inputs instead of polling them quickly, which saves trunk bandwidth.
- **Trunk setup:** the baud rate is set by the switch procedure on the device, not over BACnet, so commissioning checks the rate matches the trunk. Duplicate addresses must be flagged. The 120 Ω termination is external.
- Input-only, with no outputs, so there is no output fail-safe question.

**Open questions for H4:**
- Group object membership details (the sheet shows groups of all eight inputs and two groups of four).
- How quickly the inputs update, and the filtering applied.
- Whether the BMS needs to write the Device object's `Max_Master` for a mixed trunk, and what value suits the trunk.

### H5: Mitsubishi Electric AE-C400E

Sources: Mitsubishi Electric Australia brochure (April 2026) and a trade article. **The BACnet object list, BACnet/IP versus MS/TP detail and any Modbus support are not in what I could read.** The vendor's BACnet interface document is still needed.

| Item | Detail |
|---|---|
| Role | Centralised controller for Mitsubishi Electric air conditioning. It talks to the units over **M-NET** and presents them to the BMS. |
| Capacity | 50 indoor units standalone, up to 400 with the **EW-C50E** expansion module (blocks of 50). Also up to 4 PI controllers. |
| Equipment | City Multi, M-series, Mr Slim, Lossnay, e-Series chillers, and general equipment through a DIDO or AI adaptor (PAC-YG66DCA). Climaveneta and RC Group chillers need a dedicated adaptor. Ecodan heat pumps were listed as not yet available. |
| BMS interface | **BACnet**, an optional feature that needs a **BACnet licence PIN**. Exposes start/stop, operating mode, temperature, alarm status and energy data. |
| Network | LAN (Ethernet). The article also shows a built-in IoT gateway to the vendor's cloud platform. Port count is not stated. |
| Display and local control | 12.1 inch capacitive touchscreen. Built-in web server for browser access. USB-C for downloading system data. |
| Power | 1 phase, 240 V, 50 Hz |
| Size | AE-C400E 306 x 71.8 x 211 mm. EW-C50E 185 x 60.3 x 278 mm. |
| Controls and monitors | Control: on/off, mode, setpoint, fan speed, air direction, permit/prohibit, filter sign reset. Monitor: those plus fault codes and room temperature. |
| Built-in schedules | Daily (24 events), weekly (5 patterns, 24 events a day), annual holidays (50 days over 24 months, 5 patterns). |
| Other functions | Night setback, optimised start, automatic setpoint adjustment, load shedding, energy monitoring, and tenant billing (needs a charge licence PIN) |

**What this adds for the BMS:**
- **Interface is BACnet/IP over the device network** (to be confirmed from the vendor document). The BMS is a BACnet client: it discovers the controller, reads the objects for each indoor unit and writes on/off, mode, setpoint, fan speed and permit/prohibit. This works in both full and standalone mode.
- **The BACnet licence PIN is a cost and an install step.** The commissioning checklist should record it. Without it, the BMS cannot reach the units.
- **Schedule ownership needs a decision.** The BMS is the schedule master (5.4), but the AE-C400E has its own daily, weekly and annual schedules. Either the BMS leaves the AE-C400E schedules switched off, or it reads and flags them (as it does for controller schedules). **[Open]**
- **Local control exists.** The touchscreen, web server and permit/prohibit setting can change units outside the BMS. The BMS should read the controller's actual state back and flag when it differs from what it commanded.
- **Cloud:** the controller can connect to the vendor's cloud platform. The BMS does not use the cloud. Whether the customer keeps that link on is their decision, but it is another path that can change the units.
- **Capacity:** one controller can present up to 400 units, so the BMS device template must handle hundreds of BACnet objects from one device. Auto-discovery and bulk point creation (5.1) matter here.
- **Fault codes:** the controller reports fault codes. The BMS should map them to alarms (5.5) using the vendor's code list. **[Assumed]**

**Open questions for H5:**
- The BACnet interface document: object types and instances per unit, supported services (COV, priority array), BACnet/IP port, and number of objects.
- Is Modbus or another interface offered on this model?
- Which of the control points can the BMS write, and does a BMS write override a local touchscreen change?
- How does the controller behave when BACnet is licensed but the BMS goes offline?
- What does the energy data cover, and what extra adaptors does it need?

### H6: Mitsubishi Electric BAC-HD150 BM adapter

Source: BAC-HD150 Instruction Book. **Likely reference for H5:** the AE-C400E's BACnet add-on is expected to follow the same Mitsubishi object model. **[Assumed, not confirmed for the AE-C400E]**

| Item | Detail |
|---|---|
| Role | Converts M-NET to BACnet/IP. Monitors and operates units; it does not do scheduling. |
| Capacity | 50 indoor units on one M-NET. With PAC-YG50ECA expansion controllers (up to 3, plus an AG-150A for setup), 150 units. |
| Supported equipment | City Multi (S, Y, HP, R2, WY, WR2), Lossnay, OA processing unit, Air To Water booster and HEX units. A-control (Mr. Slim) needs an adapter, K-control needs a converter. AK-control and room air conditioners are not supported. |
| Network | BACnet/IP on **LAN1**, default 192.168.1.254. **LAN2** (default 192.168.200.212) is for expansion controllers and the Setting Tool. UDP port 47808. APDU up to 1024 octets. |
| Start-up | Takes up to about 10 minutes (INITIAL mode up to 5 minutes) before BACnet is ready after power-up. Offline mode stops BACnet. Power failure needs a UPS if control must continue. |
| Configuration | A vendor Setting Tool, via LAN2. Online and Offline modes. The BMS cannot set this up. |

**Object model.** Object ID = type + instance, where the instance is a 6-digit number: M-NET number (01 to 03) + group number (01 to 50) + member number. One group is one or more indoor units. Instance format is `MM GG NN`, for example group 5 on M-NET 1 on/off setup is BO 010501.

| Member | Object | Meaning | Writable |
|---|---|---|---|
| 01 | BO | On/Off Setup (Stop/Run) | Yes |
| 02 | BI | On/Off State | No |
| 03 | BI | Alarm Signal (Normal/Error) | No |
| 04 | MI | Error Code (01 normal to 09 system error, categories only) | No |
| 05 / 06 | MO / MI | Operating mode setup / state (Cool, Heat, Fan, Auto, Dry) | Setup only |
| 07 / 08 | MO / MI | Fan speed setup / state (Low, High, optional Mid 1 and Mid 2) | Setup only |
| 09 | AI | Room temperature (water temperature for Air To Water) | No |
| 10 | AV | Set temperature (set water temperature for Air To Water) | Yes |
| 11 / 12 | BI / BV | Filter sign / filter sign reset | Reset only |
| 13 to 16 | BV | Prohibit on/off, mode, filter reset, set temperature (Permit/Prohibit) | Yes |
| 20 | BI | M-NET communication state | No |
| 21 and 999921 | BV | System forced off (per M-NET and all) | Yes |
| 22 / 23 | MO / MI | Air direction setup / state | Setup only |
| 35 / 36 | MO / MI | Ventilation mode setup / state (Lossnay) | Setup only |
| 37 / 38 | MO / MI | Air To Water mode setup / state | Setup only |
| xx9981 | BI | Expansion controller communication state | No |

Set temperature ranges depend on the model and mode. For a standard indoor unit: cooling 19 to 30 °C, heating 17 to 28 °C, auto 19 to 28 °C, dry 19 to 30 °C. Mid-temperature models go down to 14 °C in cooling. Air To Water ranges differ and some modes are invalid.

**BACnet services.** The device answers Who-Is/I-Am, Who-Has, ReadProperty, ReadPropertyMultiple, WriteProperty, WritePropertyMultiple, SubscribeCOV, and Device Communication Control. It sends confirmed and unconfirmed COV notifications and event notifications. **COV and event notifications are switched on in the Setting Tool, per object, with a choice of confirmed or unconfirmed.** It accepts time synchronisation. Not supported: Schedule, Calendar, Trend Log, Create/Delete Object, ReadRange, SubscribeCOVProperty, Acknowledge Alarm, Get Alarm Summary.

**What this adds for the BMS:**
- **Use ReadPropertyMultiple polling as the default and COV as an option.** COV needs a Setting Tool step per object, so the BMS template should poll unless an installer confirms COV is enabled and the BMS is listed as a recipient. **[Assumed]**
- **Discovery is by group.** The BMS discovers the device with Who-Is, then reads the known member numbers for each group 1 to 50. The template must build points from the instance-number pattern, not from scanning every object.
- **Per-group sample template:** about 20 objects per group, so 1,000 objects for a full 50-group device. Bulk creation (5.1) is required.
- **Error codes are coarse** (nine categories). The detailed fault code is only on the Mitsubishi side, so alarm text in the BMS should say "Error category N, check the controller".
- **Write rules.** The BMS writes On/Off, mode, fan speed, set temperature and prohibit flags. Writing an out-of-range set temperature should be blocked by the write limits in 5.6, using the model ranges above.
- **No BACnet schedules.** Scheduling stays in the BMS (5.4), which removes the schedule conflict from H5 *if* the AE-C400E's own schedules are not used. The Mitsubishi side still has local remote controllers that can change units, so the BMS should read state back and flag differences.
- **Slow start-up.** After a power cut the BMS should show these points as Offline for up to 10 minutes rather than raising a communication alarm storm.
- **IP clash risk.** Factory addresses can clash with other devices on site. The commissioning checklist should record the LAN1 address and that the unit was first powered with LAN cables disconnected.

**Open questions for H6 and H5:**
- Does the AE-C400E BACnet add-on use the same object IDs and member numbers as this adapter?
- Is the BAC-HD150 supplied as a separate product here, or does the AE-C400E replace it on the site?
- Group-to-room mapping: the adapter gives only group numbers. The BMS uses the commissioning procedure (5.1.1), one equipment per group, with names typed or imported from a spreadsheet.
- Which COV and event notifications should the installer switch on in the Setting Tool?
- Which units are on each site (K-control or A-control units limit the available points)?

### F1: Future in-house ESP32 I/O module **[Assumed]**
A later stage may design and build custom electronics around an ESP32 dev board, with inputs and outputs, that speak BACnet or Modbus. This is **not planned work yet**. The spec only makes room for it, so nothing built now has to change.

**Principle:** the module is just another device. It talks standard Modbus RTU/TCP or BACnet (MS/TP or IP) and is added through the same device template, discover and equipment template steps (5.1, 5.1.1 to 5.1.3). **No BMS code changes are needed for it**, and it must never need a special case.

**Room left in the spec now:**
- **Open device interface.** The BMS accepts any device that is described by a device template (5.1). The in-house module gets a built-in template like the other hardware, and the template format is documented and exportable, so the module's point map can be written alongside its firmware.
- **Identity for Discover (5.1.3).** The module should report its vendor and model so Discover can match its template: the Modbus device identification request on Modbus, and vendor name, model name and object list on BACnet.
- **Configurable address over the bus.** Slave ID, baud rate or device ID and MAC should be changeable through configuration points (5.1), with the confirm step, so no DIP switches or laptop are needed.
- **Defined safe outputs.** Outputs should go to a defined state on loss of communication (a watchdog), with the fail-safe state and timeout set as configuration points. This matches the logic fail-safe in 5.3.1.
- **Point types.** Digital inputs and outputs, analog inputs (voltage, current, resistance and temperature sensors) and analog outputs, with scaling and units described in the template (5.1) and not in the BMS.
- **Mixed on one bus.** The module shares RS485 buses with other devices (5.1: serial ports are never shared between protocols), so addresses and timing must follow the same duplicate and clash checks.

**Not in scope of the BMS (for the hardware stage):** hardware design, firmware, power supply and enclosure design, electrical safety and EMC compliance, radio compliance for the ESP32 module, labelling, and any firmware update method. The BMS does not update firmware in v1.

**Open questions for F1:**
- Which protocol first: Modbus RTU, Modbus TCP, BACnet MS/TP or BACnet/IP? Which BACnet stack runs on the ESP32?
- Which I/O mix (number of digital, analog and relay channels) and which input types?
- Isolation, protection and ratings, and the compliance route for selling or installing it in Australia.
- Whether the ESP32 module needs its own configuration web page or only the BMS configuration points.
- Whether the module reports health (uptime, supply voltage, restarts) as points.

## Appendix A: Haystack tag sets (decided)

Reviewed and decided on 1 Nov 2026 (open item 39). Tags checked against **haystack-defs v4.0.0** (`ph` and `phIoT`). Tags starting `devine` are house tags (A.4). New templates add rows here as they are built. Templates (5.1.2) use these as their suggested tags.

### A.1 Pinned release
- **haystack-defs v4.0.0**, git tag `v4.0.0` of `github.com/Project-Haystack/haystack-defs` (build dated 7 July 2025). Libraries used: `ph` and `phIoT`. The defs are under the **Academic Free License 3.0**, recorded in the component licence check (section 1).
- Haystack 4 terms used in this spec: **supply air is `discharge`**; water in and out of equipment is `entering` and `leaving`; there is **no `fault` tag** (a device fault point is an `alarm` point, and a Bad value is shown as `curStatus` `fault`, 5.2); there is **no `status` tag** (a run status is a `run sensor` point); `navName` is not used.

### A.2 Equipment and space tags

| Template (5.1.2) | Entity tags | Links (5.2) |
|---|---|---|
| Indoor unit (Mitsubishi H5, H6) | `equip`, `fcu` | `spaceRef` to its room; `refrigRef` to the outdoor unit when it is modelled |
| AHU | `equip`, `ahu` | Child fans and coils with `equipRef`; `airRef` from the AHU on each room it serves |
| Room (space template, decided) | `space`, `room` (not an equip; its points carry `spaceRef` and no `equipRef`) | `siteRef` |
| Pump | `equip`, `pump`, `motor` | `chilledWaterRef` or `hotWaterRef` on the equipment it feeds |
| Fan | `equip`, `fan`, `motor` | `equipRef` to the AHU when it is part of one |
| Exhaust fan | `equip`, `fan`, `motor`, `exhaust` | `spaceRef` to the room it serves |
| ERV (including Lossnay) | `equip`, `heatExchanger` (decided; Haystack 4 has no ERV tag, and no house tag is added) | `airRef` to the rooms it serves |
| Light circuit | `equip`, `lighting` | `elecRef` to its board or meter |
| Power meter | `equip`, `ac`, `elec`, `meter`, plus `siteMeter` (main) or `submeter` | `elecRef` to the meter upstream |
| Generic valve / damper | `equip`, `valve` or `damper`, plus `actuator` | `equipRef` to the equipment it belongs to |

### A.3 Point tags by role (examples)

| Template | Role | Point tags | Kind and unit |
|---|---|---|---|
| Indoor unit | `room_temp` | `zone air temp sensor` | Number, °C |
| | `setpoint` | `zone air temp sp writable` | Number, °C |
| | `on_off_cmd` | `run cmd writable` | Bool (Off, On) |
| | `on_off_state` | `run sensor` | Bool (Off, On) |
| | `alarm` | `alarm sensor` | Bool (Normal, Error) |
| | `error_code` | `devineErrorCode sensor` | Str enum (the nine categories in H6) |
| | `mode_cmd` / `mode_state` | `hvacMode cmd writable` / `hvacMode sensor` | Str enum (Cool, Heat, Fan, Auto, Dry) |
| | `fan_speed_cmd` / `fan_speed_state` | `fan speed cmd writable` / `fan speed sensor` | Str enum (Low, Mid, High) |
| | `filter_sign` | `filter alarm sensor` | Bool |
| | `prohibit_*` | `devineProhibit cmd writable` | Bool |
| | `comms_state` | `devineComms sensor` | Bool |
| AHU | `supply_air_temp` | `discharge air temp sensor` | Number, °C |
| | `supply_air_sp` | `discharge air temp sp writable` | Number, °C |
| | `return_air_temp` | `return air temp sensor` | Number, °C |
| | `outside_air_temp` | `outside air temp sensor` | Number, °C |
| | `cooling_valve` | `cool valve cmd writable` | Number, % |
| | `oa_damper` | `outside air damper cmd writable` | Number, % |
| | `filter_alarm` | `filter alarm sensor` | Bool |
| Room | `room_temp`, `humidity`, `co2` | `zone air temp sensor`, `zone air humidity sensor`, `zone air co2 sensor` | °C, %RH, ppm |
| | `occupied` | `occupied sensor` | Bool |
| Pump / fan / exhaust fan | `run_cmd`, `run_state` | `run cmd writable`, `run sensor` | Bool |
| | `speed` | `speed cmd writable` | Number, % |
| | `alarm` | `alarm sensor` | Bool |
| Light circuit | `on_off_cmd`, `on_off_state` | `run cmd writable`, `run sensor` | Bool |
| Power meter | `power`, `energy` | `elec power sensor`, `elec energy sensor` | kW, kWh |
| | `import_energy`, `export_energy` (solar) | `elec energy import sensor`, `elec energy export sensor` | kWh |
| | `voltage`, `current`, `pf` | `elec volt sensor`, `elec current sensor`, `pf sensor` | V, A, no unit |

Every point also carries `point`, `siteRef`, `equipRef` (or `spaceRef` for room points), `kind`, `unit` where numeric, and `his` when logged (5.7).

### A.4 House library (`devine`)

| Tag | Kind | Used for |
|---|---|---|
| `devineName` | Str | The BMS point name `<room>_<equipment>_<function>` (5.2) |
| `devineErrorCode` | Marker | Point carrying a device error code or category |
| `devineProhibit` | Marker | Mitsubishi permit/prohibit flags (H6 members 13 to 16) |
| `devineComms` | Marker | Communication state reported by a gateway (for example M-NET) |
| `devineConfig` | Marker | Device configuration point with the extra confirm step (5.1) |
| `devineHand` | Marker | Hand mode or override flag (5.6) |
| `devineRunHours`, `devineStarts` | Marker | Maintenance counter points (5.10) |
| `devineTemplate` | Ref | The equipment template and version an instance uses (5.1.2) |

Site custom tags (5.2) are not part of this library.
