# Troubleshooting

For installers and support.

## Value quality

Every value has a quality (main spec 5.2.1). Only Good values are shown as live numbers or used by alarms and logic.

| Shown | Meaning | Usual cause |
|---|---|---|
| **Good** | A fresh, valid reading. | |
| **Stale** | No new reading in time (3 times the poll interval by default). | Polling is falling behind: too many points on a slow line, or a busy MS/TP trunk. A device that keeps answering with the same number stays Good. |
| **Bad: down** | The device did not answer, or the connection failed. Every point on that device goes Bad together. | Wiring, power, wrong address or baud rate, a gateway down, the broker lost. It retries by itself (1 s, doubling to 30 s). |
| **Bad: fault** | The device answered with an error, or a value that cannot be decoded. | A wrong register or object, the wrong data type or byte order, a Modbus exception. |
| **Disabled** | The device is switched off in the setup. | |
| **Waiting / not read yet** | Nothing read since the start. | The first poll has not happened, or the bus is held (below). |

On a device's page, **Test connection** shows the raw request and answer for each point.

## Logs

| Machine | Command |
|---|---|
| Site server, backend | `pct exec 202 -- journalctl -u bms-backend -b` |
| Site server, database | `pct exec 201 -- journalctl -u postgresql@16-main -b` |
| Site server, Mosquitto | `pct exec 202 -- journalctl -u mosquitto -b` |
| Host, MS/TP trunk | `journalctl -u bms-mstp@<trunk>` |
| Host, updater | `journalctl -u bms-updater` |
| Host, Tailscale | `tailscale status` and `tailscale serve status` |
| Pi, kiosk | `journalctl -u bms-kiosk -b` |
| Pi, backend | `journalctl -u bms-backend -u bms-pgram -b` |
| Pi, updates | `journalctl -u bms-pi-update` |

`GET /api/health` lists each connection and device with polls and failures, MS/TP trunk counters, and (`held`) why
nothing is being polled.

## Getting back in

**The only admin forgot their password.** On the site server:

```bash
pct enter 202
```

Then, inside the container:

```bash
cd /opt/devine-bms/current/backend
```

```bash
set -a; . /etc/devine-bms/bms.env; set +a
```

```bash
../venv/bin/python -m devine_bms.setup.cli password <username>
```

It asks for the new password, unlocks the account and records it in the audit log. On a development machine, run
`uv run python -m devine_bms.setup.cli password <username>` in `backend/`.

**A locked account.** After 5 failed logins an account locks for 10 minutes. An admin can unlock it on Setup → Users.

**"Logins are not allowed from this network".** The address is not in Setup → Security → Allowed networks. Log in from
the server itself, or from a listed network, and fix the list. Remote people come from `100.64.0.0/10`.

**"Too many login attempts from this address".** Wait a minute.

**Lost phone (2FA).** An admin removes 2FA on Setup → Users; the person sets it up again.

## Common problems

| Problem | Look at |
|---|---|
| The touch screen says "The site server cannot be reached" | The server address in `/etc/devine-bms/hmi.env` on the Pi; the network; whether `bms-backend` runs. It recovers by itself. |
| The touch screen asks for a login | The Pi's address is not in Setup → Security → HMI kiosks. |
| A Number point cannot be commanded | It has no write limits (Mapping table, "No write limits"). |
| A command is refused with "the HMI … is in direct mode" | A standby HMI holds the site. Hand control back on its Server screen once the server is healthy. |
| Nothing is polled; the web app shows a red banner | As above: an HMI is in direct mode. |
| MS/TP devices go Bad together | `STATS` on the service shows lost tokens and CRC errors: wiring, termination, baud rate, or a duplicate MAC. MAC 0 and 1 are reserved. |
| MQTT points Bad (down) | The broker connection: the password variable (`passwordEnv`) is not set in `bms.env`, or the backend was not restarted after setting it. |
| Alarm emails or SMS not arriving | Setup → Alarm delivery → Test; the delivery log on the Alarms page; the password or key variable in `bms.env`. Failed sends retry for 24 hours. |
| Standby HMI: "bus not clear" | Something still polls a serial line the HMI shares: the server is still running, or another master is on the line. |
| Standby HMI: "the server refused the cache" | `BMS_STANDBY_TOKEN` differs between the server and the Pi. |
| Standby HMI: `serverOk: false` | The Pi cannot reach `BMS_SERVER_URL` (address or firewall). |
| Standalone: the touch screen warns about USB | The stick is missing or full. The next export catches up once it is back. |
| Haystack tool: 404 | The API is switched off (Setup → Security). |
| Haystack tool: 401 or 403 | Wrong password, a person's account (only API client accounts work), or the account is locked. |
| Haystack tool: 429 | More than 30 requests in 10 seconds. |
| Haystack tool: "is not logged" | The point is not logged (no `his`). |
| An update failed | [Updates](updates.md), "If something goes wrong". |

## Bench tools

| Tool | Command (from `backend/`) |
|---|---|
| BACnet/IP Who-Is | `uv run python -m devine_bms.drivers.bacnet.tools whois --local 192.168.10.5/24` |
| BACnet/IP read | `uv run python -m devine_bms.drivers.bacnet.tools read --local 192.168.10.5/24 --device 192.168.10.20 --object analog-input,1` |
| MS/TP service by hand | `printf 'PING p1\nSTATS s1\nWHOIS w1 -1 -1 3000\n' \| nc -q 5 10.77.0.1 47900` |
| MQTT topics on a broker | `uv run python -m devine_bms.drivers.mqtt.tools listen --host 192.168.10.5 --seconds 30` |
| A device template's map | `uv run python -m devine_bms.templates.device show <template>` |
| Simulated devices | `simulators/` (Modbus meter and I/O, BACnet, MQTT); see each file's top. |

These tools only read. Discover and Test connection in the web app do the same jobs on a running site.
