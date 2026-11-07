# Milestone 11: HMI standby takeover and failback

Status: **built** (7 Nov 2026). Main spec: 9 (offline and failure behaviour), 6.2 (HMI), 5.6 (commands), 16 (bench tests 10 and takeover), 18 (milestone 11).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **One part.** |
| 2 | How the Pi and the server trust each other | **A shared standby token** in `BMS_STANDBY_TOKEN` on both (never in config or the repo), sent as the `X-BMS-Standby-Token` header on the standby endpoints only. The server's install makes it; it is copied to the Pi once. |
| 3 | How the kiosk moves between the server and the Pi (proposed; built as proposed) | The kiosk opens the server's HMI with `?standby=http://localhost` (the Pi's own backend, port 80). While the server cannot be reached, the HMI asks the Pi whether it has taken over and, if so, goes to the Pi's own HMI; after failback the Pi's HMI goes back to the server. The Pi's state endpoint allows this cross-origin request (including Chrome's private-network check). |
| 4 | Bus-clear checks (proposed; built as proposed) | The Pi listens without sending: on a **Modbus RTU** line, no bytes for 60 seconds; on an **MS/TP** trunk, no frames from MAC 0 (the server) for 60 seconds. Modbus TCP, BACnet/IP and serial gateways cannot be listened to: they count as clear once the heartbeat is lost (the server is gone). |

## The parts

### On the Pi (`BMS_MODE=standby`)

- The same backend as the server, with PostgreSQL in RAM (as in standalone) and a **cache file** on the config partition: the server's setup (the export), the people who have an HMI PIN (user name, name, role and the PIN's hash only) and the alarm delivery settings. It is fetched from the server every minute while the server is healthy and written to the card only when it changes. At boot the cache is loaded, so the Pi can take over even if the server is down when the Pi starts.
- **Standby:** the Pi does not poll, write, or talk on any bus. It sends nothing on the serial lines and does not join the MS/TP token ring; it listens (decision 4). Logic and schedules never run on the Pi.
- **Heartbeat:** every 5 seconds the Pi asks the server for `/api/health`. After **30 seconds** without an answer it takes over, if every serial bus is clear. A bus that is not clear stays silent, shows a warning and raises an alarm; the Pi keeps checking.
- **Taking over (direct mode):** the Pi starts polling with its own connections (its USB RS485 adapters, MS/TP as MAC 1, its Ethernet link for BACnet/IP and Modbus TCP), raises a **Critical** alarm "Direct mode: the HMI has taken over", and the HMI shows a **direct mode** banner. People can view and command (with their PIN); hand mode works as on the server. MQTT and vendor-API devices are not covered.
- **The server is back:** the banner says so and shows how long it has been healthy. After **5 minutes** (setting) **Hand control back** is offered; an operator taps it (PIN). The HMI never hands back by itself.
- **Handing back (failback):** the Pi releases what it set (hand modes back to Auto; BACnet priority 8 released), stops polling and closes its connections (MS/TP leaves the token ring), sends its audit log for the direct-mode period to the server (marked as from the HMI), clears its alarm, and returns to standby. The kiosk goes back to the server's HMI.

### On the server

- **One writer at a time:** the server asks each HMI (`BMS_HMI_URLS`) every 5 seconds whether it is in direct mode, and once before it starts polling. While an HMI is in direct mode the server does not poll or write anything (commands are refused with the reason), logic and schedules wait, and every page shows a banner; it starts again when the HMI hands back.
- Endpoints with the standby token: `GET /api/standby/cache` (the setup, PIN people, delivery settings, and a revision), `POST /api/standby/audit` (the HMI's direct-mode entries).

### Endpoints on the Pi

- `GET /api/standby/state` (no login; read-only; cross-origin allowed): `standby` or `direct`, the server's health and since when, the buses and whether each is clear, when control can be handed back.
- `POST /api/standby/failback` (an operator; on the kiosk with the PIN).

## Settings

- Both: `BMS_HEARTBEAT_S` (5): how often the Pi asks the server, and the server the HMIs.
- Server: `BMS_STANDBY_TOKEN`, `BMS_HMI_URLS` (comma-separated, for example `http://192.168.10.30`).
- Pi: `BMS_MODE=standby`, `BMS_SERVER_URL`, `BMS_STANDBY_TOKEN`, `BMS_HMI_NAME`, `BMS_STANDBY_CACHE` (the cache file), `BMS_TAKEOVER_AFTER_S` (30), `BMS_BUS_QUIET_S` (60), `BMS_FAILBACK_AFTER_S` (300), `BMS_MSTP_START_CMD` / `BMS_MSTP_STOP_CMD` (start and stop the Pi's MS/TP service as MAC 1), `BMS_PI_PORTS` (the Pi's own adapters by bus name, for example `rs485_1=/dev/ttyBMS-rs485-1,trunk1=/dev/ttyBMS-trunk1`; a bus not listed keeps the server's port name).

## What was built

- **Backend, `standby/`:**
  - `listen.py`: `SerialListener` (a thread per serial bus; never writes); the MS/TP frame-header parser with the bacnet-stack header CRC.
  - `hmi.py`: `StandbyService` (heartbeat, cache refresh, takeover, failback, audit sync); `localise` gives the Pi its own ports and MAC 1 on every trunk, and drops the router (the server's job).
  - `server.py`: `HmiGate`, the cache and audit endpoints.
  - `boot.py`: the cache file, or the server the first time (retrying), into the RAM database.
- **Runtime:** `hold(why)` / `resume()`. A held runtime does not poll and closes every connection. Writes are refused with the reason, except logic variables.
  - Logic and schedules skip their cycles while held.
  - The alarm engine judges only system alarms while held, so point alarms neither raise nor clear on values that are not being read.
  - System alarms (`set_system`) are used for "direct mode" (Critical) and "bus not clear" (Warning).
- **On the Pi:**
  - Logic and schedules are never started, and logic's fail-safe at stop is skipped (an HMI has no logic outputs).
  - `/api/mode` says `standby`.
  - `127.0.0.1` is a kiosk, and there is no SMS.
- **`/api/health`** has `held` (why nothing is polled, or null). The web app shows a banner from it while an HMI holds the site.
- **Screens:**
  - The server's HMI with `?standby=` asks the Pi while the server cannot be reached, and opens the Pi's HMI once it is in direct mode.
  - The Pi's HMI shows the Direct mode chip and banner. Its bottom bar is Overview, Graphics, Setpoints, Overrides and Server; the Server screen has the server's state and Hand control back (PIN).
  - In standby, with the server answering, the Pi's HMI returns to the server's HMI.
  - `waiting.html` also asks the Pi.
- **Pi install:** `provision.sh --server … --standby [--name] [--ports]` installs the backend as in standalone (PostgreSQL in RAM, the config partition, read-only root) with `BMS_MODE=standby`, and the kiosk opens with `standby=http://localhost`.
- **Tests:**
  - `tests/test_standby.py`: listening on pseudo-terminals, the CRC vector, `localise`, and the whole round with a real server and a real Pi on two ports and two databases.
  - `web/tests/takeover.mjs`: kiosk to the Pi and back, the banners, the PIN, the audit log and the release.

Not covered (as in the main spec):
- MQTT and vendor-API devices.
- The Pi's history and alarms during direct mode stay on the Pi; only its audit log is sent to the server.
- On the server, values read before the takeover show as Stale while the HMI holds the site.

## How to set up

1. **Make the token** on the server: `openssl rand -base64 32`. Put it in the server's environment as `BMS_STANDBY_TOKEN` (never in the config file or the repository).
2. **The Pi:**
   - You need a `BMSCONF` partition on the card, as for standalone (see `hmi/README.md`), and a built web app in the copy of the repository.
   - Run `sudo ./provision.sh --server http://<server>:8000 --standby --name hmi-plant --ports "rs485_1=/dev/ttyBMS-rs485-1"` and paste the token when asked.
   - With MS/TP trunks, install `bms-mstp` on the Pi as MAC 1 (`mstp/README.md`, not enabled at boot). Set `BMS_MSTP_START_CMD` and `BMS_MSTP_STOP_CMD` in `/etc/devine-bms/standalone.env`, and allow those two commands in sudoers.
3. **The server:** set `BMS_HMI_URLS=http://<pi address>` and restart. Set an HMI PIN for each person who may hand control back; the Pi picks the people up within a minute.
4. **Check:**
   - `http://<pi address>/api/standby/state` says `standby`, with `serverOk: true` and a `cacheRevision`.
   - Each serial bus is listed and `clear` is false while the server polls it.
   - Stop the server: after 30 seconds the screen moves to the Pi's HMI in direct mode.
   - Start the server: the web app shows the banner. After 5 minutes, Hand control back on the screen.
