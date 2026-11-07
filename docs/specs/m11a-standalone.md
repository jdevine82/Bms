# Milestone 11a: Standalone mode (Pi only)

Status: **built**, bench tests 16 and 19 to do (7 Nov 2026). Main spec: 3.1 (deployment modes), 6.5 (standalone tabs and bottom bar), 5.10 (counters), 7 (accounts), 16 (bench tests 16 and 19), 18 (milestone 11a), 19 open items 14 to 16.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How standalone stores things | **PostgreSQL in RAM plus a state file.** PostgreSQL runs on the Pi with its data in RAM (tmpfs). At boot it is filled from the **state file** on the config partition; after each saved change the state file is rewritten (after 2 seconds of quiet, atomically). History stays in RAM. The same backend code runs in both modes (main spec 3.1 "one codebase"). |
| 2 | Log window and USB export | **72 hours** at **one sample a minute** per logged point; USB export **every hour**. Both are settings. |
| 3 | How it is split | **One part.** |

## The state file

- `site-state.json` on the config partition: `{"schema": "devine-bms-state/1", "savedAt", "config": <the setup in devine-bms-config/0, the same as an export>, "tables": {...}, "standalone": {...}}`.
- **`config`** is the setup exactly as an export, so the file can be imported into a full-mode server (the upgrade path, main spec 3.1).
- **`tables`** holds everything else a site keeps through a power cut: accounts (passwords and 2FA as hashes and secrets, PINs as hashes), security settings, alarm delivery settings and recipients, schedules and holiday calendars, logic diagrams, versions, instance switches, constants, device templates; and the setup tables themselves, so a restore gives the same IDs.
- **Not kept** (RAM only, main spec 3.1): live values, the log, active alarms and their history, the audit log, login sessions, hand-mode overrides (they return to Auto after a power loss), and logic memory variables between daily saves (below).
- **When it is written:** after any saved change to the kept tables (2 seconds after the last one, so a burst is one write), when standalone settings change, once a day (to keep logic memory variables), and on a clean shutdown. Memory variables written by logic every cycle do not trigger a write. The write is atomic (a temporary file, flushed, then renamed); on the Pi the partition is remounted writable just for the write (`BMS_STATE_RW_CMD`, `BMS_STATE_RO_CMD`).
- **At boot**, if the database is empty: the state file is restored; if there is none, the config file is imported (as in full mode); if there is neither, an empty site is made so the setup can start in the web app.

## The light profile (`BMS_MODE=standalone`)

- **Slow** points are polled every 10 minutes instead of 5 (main spec 5.1.4).
- **The log:** the history table holds the last 72 hours (setting), pruned every 10 minutes; a point is logged at most once a minute (and on every quality change). Trends and CSV downloads read it as usual.
- **Point limit:** about 50 points (open item 14): Setup → Standalone shows the count against it and warns above 50.
- **Alarms:** on-screen, ntfy and email; SMS is not offered.
- **After a power loss** hand-mode overrides return to Auto: nothing remembers them, and at start-up the BMS releases its operator priority (8) on writable BACnet points.

## USB export (main spec 3.1)

- Every hour (setting), the new log rows since the last export are appended to CSV files on a USB stick: one file per month (`2026-11.csv`), a header row, local site time, one row per minute and one column per logged point (`name (unit)`). A file whose columns no longer match (points added or removed) is continued as `2026-11-2.csv`.
- The stick is mounted only for the write (`BMS_USB_MOUNT_CMD` / `BMS_USB_UMOUNT_CMD`) at `BMS_USB_PATH`, so pulling it out between writes is safe. A missing, full or failing stick gives a clear warning on the screens; the next export catches up on anything still in the RAM window.
- **Export now**, the last export time and result, and the next time are on the HMI Log screen and in Setup → Standalone.
- **Download CSV** gives the same layout for the last 1, 6, 24 or 72 hours.

## First boot and setup (main spec 3.1 "Setup and access")

- With no admin account, the touch screen shows a **setup page**: the Pi's addresses, a QR code to the web app and a **one-time setup code** (6 digits, made at start-up, kept in RAM).
- The installer opens the address from a laptop or phone, enters the code, and creates the first admin. The code stops working once an admin exists. (From the Pi itself the code is not needed, as in full mode.)
- **Network settings** (DHCP or a fixed address, gateway and DNS) are changed in Setup → Standalone, which runs the Pi's network helper (`BMS_NET_CMD`). First-boot DHCP with the 192.168.50.10 fallback is done by the install.
- The site's time zone and name are set on Setup → Site as in full mode.

## Standalone HMI (main spec 6.5)

- **Status strip:** a **Standalone** label, the USB export status (last export or a warning), the alarm count and the lock state.
- **Bottom bar:** **Graphics**, **Overrides**, **Schedules**, **Log**.
  - **Overrides:** setpoints with their allowed range (the PIN to change), and the active hand modes with the time left and **Back to Auto**.
  - **Schedules:** today's state and the weekly times (as in full mode).
  - **Log:** a chart of temperatures and power from the RAM log (1, 6, 24 or 72 hours), the USB export status, **Export now** and **Download CSV**.

## The Pi install (`hmi/provision.sh --mode standalone`)

- Installs PostgreSQL (data in RAM, made at every boot), the backend with its web app, and the MS/TP service; the kiosk shows `http://localhost:8000/hmi`.
- **Read-only root** (Raspberry Pi OS overlay file system) with system logs in RAM; the state file on a partition labelled `BMSCONF`, mounted read-only and remounted writable only to save.
- **First-boot network:** DHCP for 30 seconds, then the fixed fallback 192.168.50.10/24.
- Optional Tailscale is not set up by the script (later).

## What was built

- **Backend** (`backend/src/devine_bms/standalone/`): `state.py` (the state file: dump, atomic write with the remount commands, restore into an empty database, and the keeper that saves 2 seconds after a change to a kept table, once a day and on stop; failed-login counters and logic memory values do not trigger a save), `boot.py` (at start-up: restore, else import the config file, else an empty site; the light profile: Slow every 10 minutes, the log one sample a minute), `service.py` (settings, the log window pruned every 10 minutes, the USB export, the CSV table, the setup code, the network helper), `api/standalone_api.py`.
- **Settings** (`BMS_MODE=standalone`, `BMS_STATE_FILE`, `BMS_STATE_RW_CMD`, `BMS_STATE_RO_CMD`, `BMS_USB_PATH`, `BMS_USB_MOUNT_CMD`, `BMS_USB_UMOUNT_CMD`, `BMS_NET_CMD`).
- **API:** `GET /api/mode` (public); `GET /api/standalone/setup-info` (the Pi itself only, while there are no accounts); `GET /api/standalone/status`; `PUT /api/standalone/settings`; `POST /api/standalone/export`; `POST /api/standalone/save`; `GET /api/standalone/log.csv?hours=`; `GET` and `PUT /api/standalone/network`. `POST /api/auth/first-admin` takes `setupCode` (away from the Pi); `/api/auth/state` says when it is needed.
- **Other changes:** the history writer can log a point at most once per interval (and every quality change); the Pi's own screen is a kiosk in standalone; alarm delivery offers no SMS in standalone (the defaults drop it, a save with it is refused); after start-up the operator priority (8) is released on writable BACnet points (hand modes return to Auto).
- **Web app:** the first-run page takes the setup code; **Setup → Standalone** (points against the limit, log window, USB export with Export now and Download, the state file's last save with Save now, the network); the delivery settings hide SMS.
- **HMI:** the first-boot **setup page** (addresses, QR code, the code; it moves on once the admin exists); in standalone the status strip (Standalone, the USB export status, the alarm count, the lock) and the bottom bar **Graphics, Overrides, Schedules, Log**.
- **Pi install** (`hmi/provision.sh --mode standalone [--no-readonly]`, `hmi/standalone/`): PostgreSQL made in RAM at every boot (`bms-pgram`), the backend on port 80 (`bms-backend.service`, migrations before start), the root helpers allowed through sudo (`bms-state-rw`, `bms-usb`, `bms-net`), the settings file, the `BMSCONF` partition mounted read-only, the NetworkManager profile, logs in RAM and the overlay root. `hmi/README.md` has the steps and the bench checks.

### Differences from the main spec

- **First-boot network:** the fallback 192.168.50.10/24 is a second address alongside DHCP from the start, rather than only after 30 seconds without DHCP; it is dropped when the network is set in Setup → Standalone. It is simpler and the installer can always reach the Pi there; a site whose LAN already uses 192.168.50.10 should set the network first.
- **The web app is on port 80** in standalone (`http://<pi address>/`), so the address shown is short.
- **Tailscale on the Pi** is not set up by the install yet.

## Tests

- Backend (`tests/test_standalone.py`): a state file importing into a full-mode server (the upgrade path: Setup → Site → Import accepts it); the state file saved after changes (setup, a user, a schedule, a variable, a logic draft) with the config as an export and without the log, alarms, audit log, sessions or overrides; a failed login does not write; standalone settings kept; a power cut (an empty database) restores the site, accounts, schedules, logic, constants and settings; an empty Pi gets an empty site and a setup code (only the Pi is given it; wrong and missing codes refused; it stops once used); the log thinned to one sample a minute but keeping quality changes; the window pruned; the USB export (monthly file, header, one row a minute, only new rows next time, a second file when the points change, a missing stick reported); the CSV download; the network helper (show, a refused gateway, static, DHCP); full mode answers `mode: full` and has no standalone API; no SMS in standalone.
- Browser (`web/tests/standalone.mjs`, against a standalone backend): the Pi's setup page with code and QR code; a wrong code refused and the right one making the admin from a laptop; the Pi's screen moving on to the standalone bottom bar and status strip; Overrides with the hand mode, Back to Auto after the PIN; Log with Export now and Download CSV; Setup → Standalone (settings, Save now, network); no SMS; a power cut (the database emptied and restarted) bringing back the account, the point and the settings, with nothing in hand mode.
- Not tested here: the Pi install itself (`provision.sh`, the overlay, the partition, NetworkManager, USB mounting) and bench tests 16 and 19; the scripts pass ShellCheck.
