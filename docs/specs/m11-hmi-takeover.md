# Milestone 11: HMI standby takeover and failback

Status: **in progress** (7 Nov 2026). Main spec: 9 (offline and failure behaviour), 6.2 (HMI), 5.6 (commands), 16 (bench tests 10 and takeover), 18 (milestone 11).

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How it is split | **One part.** |
| 2 | How the Pi and the server trust each other | **A shared standby token** in `BMS_STANDBY_TOKEN` on both (never in config or the repo), sent as the `X-BMS-Standby-Token` header on the standby endpoints only. The server's install makes it; it is copied to the Pi once. |
| 3 | How the kiosk moves between the server and the Pi (proposed) | The kiosk opens the server's HMI with `?standby=http://localhost:8000`. While the server cannot be reached, the HMI asks the Pi whether it has taken over and, if so, goes to the Pi's own HMI; after failback the Pi's HMI goes back to the server. The Pi's state endpoint allows this cross-origin request (including Chrome's private-network check). |
| 4 | Bus-clear checks (proposed) | The Pi listens without sending: on a **Modbus RTU** line, no bytes for 60 seconds; on an **MS/TP** trunk, no frames from MAC 0 (the server) for 60 seconds. Modbus TCP, BACnet/IP and serial gateways cannot be listened to: they count as clear once the heartbeat is lost (the server is gone). |

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

- Server: `BMS_STANDBY_TOKEN`, `BMS_HMI_URLS` (comma-separated, for example `http://192.168.10.30:8000`).
- Pi: `BMS_MODE=standby`, `BMS_SERVER_URL`, `BMS_STANDBY_TOKEN`, `BMS_HMI_NAME`, `BMS_STANDBY_CACHE` (the cache file), `BMS_TAKEOVER_AFTER_S` (30), `BMS_BUS_QUIET_S` (60), `BMS_FAILBACK_AFTER_S` (300), `BMS_MSTP_START_CMD` / `BMS_MSTP_STOP_CMD` (start and stop the Pi's MS/TP service as MAC 1).
