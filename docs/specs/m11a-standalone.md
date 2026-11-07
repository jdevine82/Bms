# Milestone 11a: Standalone mode (Pi only)

Status: **in progress** (7 Nov 2026). Main spec: 3.1 (deployment modes), 6.5 (standalone tabs and bottom bar), 5.10 (counters), 7 (accounts), 16 (bench tests 16 and 19), 18 (milestone 11a), 19 open items 14 to 16.

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
- **Point limit:** about 50 points (open item 14): the setup checks warn above 50.
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
