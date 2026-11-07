# Notes for the user and installation guide

The full guide is written at the end of the project (main spec 18, item 13b). Until then, this
list collects what it must cover and where the facts are now, so nothing is lost. Each
milestone adds to it.

## Installation

- Development machine: Python 3.12 and uv, Node and npm, PostgreSQL 16 (TimescaleDB on sites; `BMS_ALLOW_PLAIN_POSTGRES=true` only on a dev machine), migrations with `uv run alembic upgrade head`. Facts: `docs/decisions/016-native-dev-setup.md`, `docs/specs/m2-simulated-modbus-to-trend.md`.
- Site server (manual steps until the packaging of milestone 11): database, backend service, built web app (`BMS_WEB_DIST`), listen address (`BMS_LISTEN_HOST`, default 127.0.0.1), Tailscale and `tailscale serve` for HTTPS (decision 010).
- Pi HMI kiosk: `hmi/README.md` (provision.sh, 64-bit Raspberry Pi OS Lite, cage).
- BACnet MS/TP service: `mstp/README.md` (build.sh, systemd template, udev rules in `deploy/udev`); version 0.2.0 adds WRITE and 0.3.0 the BACnet router (rebuild and restart the service when updating). Router set-up: the network-number plan, the trunk's labelled IP address, `BMS_MSTP_ROUTE_ARGS`, checking with a BACnet tool (`mstp/README.md`, "BACnet router").
- MQTT broker (Mosquitto): `docs/specs/m4d-mqtt.md`.

## Settings reference

- Environment variables (`backend/.env.example`, `backend/src/devine_bms/settings.py`): `BMS_DATABASE_URL`, `BMS_CONFIG_PATH`, `BMS_SITE_NAME`, `BMS_LISTEN_HOST`, `BMS_LISTEN_PORT`, `BMS_LOG_LEVEL`, `BMS_WEB_DIST`, `BMS_ALLOW_PLAIN_POSTGRES`; secrets such as MQTT broker passwords through the variable named in `passwordEnv`.
- The config file `devine-bms-config/0` and its JSON Schema (`config/schema/`), examples in `config/`. The database is the master copy; the file is import and export (decision 021).
- Security settings (Setup → Security): shortest password, idle time-out, lockout count and time, 2FA for admins, HMI kiosk addresses, allowed networks (`docs/specs/m6-logins-alarms.md`).
- Alarm delivery settings (Setup → Alarm delivery): channels per level, SMTP (`BMS_SMTP_PASSWORD`), ClickSend (`BMS_CLICKSEND_API_KEY`), ntfy server and token, recipients and quiet hours, test messages (`docs/specs/m6-logins-alarms.md`, "Setting up delivery on a site").
- Alarm definitions: point alarms (high, low, state, stale; deadband, delay, level, message), device offline alarms (only where set), template defaults.

## Standalone mode (milestone 11a)

- What standalone is and is not (main spec 3.1): the Pi is the whole system, about 50 points, no standby, memory-only log.
- Installing: the `BMSCONF` partition, `provision.sh --mode standalone`, first boot with the setup code and QR code, the fallback address 192.168.50.10 (`hmi/README.md`, `docs/specs/m11a-standalone.md`).
- What survives a power cut (the state file) and what does not (the log, alarms, hand modes return to Auto); the USB export and its files; Download CSV; Setup → Standalone (network, log window, export interval, Save now).
- Updating the software (turning the overlay off and on); upgrading to full mode by importing the state file on the server (Setup → Site → Import takes it as it is).

## Users and logins

- First admin on the first-run page, from a browser on the server itself only.
- Roles (viewer, operator, admin) and what each can do (main spec 7).
- Adding people, 2FA with an authenticator app, lost phone (admin removes 2FA), lockout and unlock.
- Forgotten admin password: `uv run python -m devine_bms.setup.cli password <username>` on the server.
- HMI kiosks by address; allowed networks; passwords cross the LAN unencrypted, so keep the web app on the device or management network (main spec 8).
- HMI PINs: set on My account, removed by an admin; only on listed kiosks; 5 minutes idle.
- The audit log: what it records, filters, CSV, kept for 1 year.

## Commissioning

- Discover, the 4-step wizard, the mapping table and CSV, device and equipment templates, test reads (`docs/specs/m5-setup.md`).
- Export and import of a whole setup (web app and `setup.cli`).
- Graphics: starting graphics from rooms and equipment (tags decide the symbols), and that saving stops them following setup changes (`docs/specs/m9-graphics.md`, "How to set up graphics on a site"). The Equinor symbol licence and credit.
- The graphics editor: palette and tools, grid, moving and resizing, pipes and ducts by corners, binding points, colour rules (follows, bands with hysteresis, fill), backgrounds, Check and Save, keyboard shortcuts, tab order (`docs/specs/m9-graphics.md`, "How to make a site's graphics").
- Site symbols: drawing (Off, On and both layers, animation), importing from Inkscape (layers named Off and On, `currentColor`), what the cleaning removes and why, limits (64 KB symbol, 1 MB background).

## Operating

- Points and trends, CSV export of history (milestone 2), the HMI (milestone 3).
- Commands: the Command button, set and hand mode, override times, release to auto, the hand-mode badge, write limits, confirm prompts; on the HMI, the PIN pad and Lock now (`docs/specs/m7-commands-schedules.md`).
- Schedules: occupancy, the weekly pattern, holiday calendars, one-off exceptions and which wins, what a schedule applies to, occupancy values per point, hand mode during a schedule, the controller copy (write, check, keep the controller's times).
- Graphics: the Site and room tabs, what each state looks like (Offline dashed, Fault flashing with !, Hand badge, On spinning), room colours, tapping a room or a symbol; on the HMI, the PIN first (`docs/specs/m9-graphics.md`).
- Control logic (milestone 10; the editor: "How to use the editor" in `docs/specs/m10-logic.md`): the Logic page and editor, Edit and Live modes, the test strip, import mapping and keys; template logic (role mode, publishing a template version, roll-out, each instance's switch, an equipment diagram replacing it); variables published as points (the BMS logic device, HMI changes with the PIN); BMS- or device-controlled on the equipment panel; snippets; what BMS-controlled means, drafts and publishing, the fail-safe of each output (release, hold, value) and when it applies, hand mode beating logic, one writer per point, variables (constant and memory, site and diagram), test runs, versions and restore, import and its warnings (`docs/specs/m10-logic.md`).
- Alarms: the header count, acknowledging with comments, history, the notifications log; what Info, Warning and Critical mean and who is told; why a Bad value does not raise a limit alarm.

## Bench and troubleshooting

- Bench tools: Modbus, BACnet, MS/TP and MQTT tools named in the milestone 4 specs; simulators in `simulators/`.
- Value quality (Good, Stale, Bad) and what each reason means (main spec 5.2.1).
