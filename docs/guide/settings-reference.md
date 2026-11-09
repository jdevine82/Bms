# Settings reference

For installers and support. There are four places settings live:

1. **Environment variables** for each machine: addresses, modes and every secret. On a site server they are in
   `/etc/devine-bms/bms.env` inside `bms-app`; on a Pi with the backend, in `/etc/devine-bms/standalone.env`; on a
   development machine, in `backend/.env`. After changing them, restart the backend:
   - on a site server: `pct exec 202 -- systemctl restart bms-backend`;
   - on a Pi: `sudo systemctl restart bms-backend`.
2. **The site setup**: devices, points, rooms, equipment, templates, alarms, graphics, logic snippets. It is kept in the
   database, edited in Setup, and exported and imported as one file.
3. **Site settings in the web app** that are not part of the setup file: security, alarm delivery, override times,
   standalone settings. Importing a setup file never changes them.
4. **Schedules and holiday calendars**, edited by operators on the Schedules page.

Secrets are never put in the setup file, the web app or the repository. Where a secret is needed, the setup names an
environment variable that holds it (for example an MQTT broker's `passwordEnv`).

## Environment variables (backend)

All start with `BMS_`. Defaults in brackets.

### Every machine

| Variable | Meaning |
|---|---|
| `BMS_DATABASE_URL` | The database, `postgresql+asyncpg://bms:<password>@<host>:5432/bms`. Holds a secret. |
| `BMS_SITE_NAME` | Which site to run if the database holds several. A new server with this set and no config file starts with an empty site. |
| `BMS_CONFIG_PATH` [`../config/site.json`] | A setup file imported only when the database has no site yet. |
| `BMS_WEB_DIST` | The built web app to serve (`/opt/devine-bms/current/web/dist` on a site). Empty with the Vite dev server. |
| `BMS_LISTEN_HOST` [`127.0.0.1`], `BMS_LISTEN_PORT` [`8000`] | Used by the development run commands; the site services pass `--host 0.0.0.0 --port 80` themselves. |
| `BMS_TRUSTED_PROXIES` | Proxies whose `X-Forwarded-For` and Tailscale identity are believed, besides loopback (`10.77.0.1` on a site server). |
| `BMS_LOG_LEVEL` [`INFO`] | Log detail. |
| `BMS_ALLOW_PLAIN_POSTGRES` [false] | History in a plain table without TimescaleDB. Development and bench only. |
| `BMS_MODE` [`full`] | `full` (site server), `standalone` (the Pi is the BMS) or `standby` (a Pi that can take over). |

### Alarm delivery secrets (names chosen in Setup → Alarm delivery)

| Variable | Meaning |
|---|---|
| `BMS_SMTP_PASSWORD` | The email account's password. |
| `BMS_CLICKSEND_API_KEY` | The ClickSend API key, for SMS. |
| (your name) | An ntfy token, if a self-hosted ntfy server needs one. |
| `BMS_MQTT_PASSWORD` | On a site server, the site Mosquitto's password for user `bms`; name it as the broker's `passwordEnv`. |
| `BMS_MQTT_USERS_CMD` | On a site server, the helper for device logins on the site broker: `sudo -n /usr/local/sbin/bms-mqtt-users`. Empty elsewhere (no Device logins panel). |

### Standalone (`BMS_MODE=standalone`; set by `provision.sh`)

| Variable | Meaning |
|---|---|
| `BMS_STATE_FILE` | The state file on the `BMSCONF` partition. |
| `BMS_STATE_RW_CMD`, `BMS_STATE_RO_CMD` | Remount the partition writable and read-only around a save. |
| `BMS_USB_PATH`, `BMS_USB_MOUNT_CMD`, `BMS_USB_UMOUNT_CMD` | The USB export. |
| `BMS_NET_CMD` | The Pi's network helper (Setup → Standalone). |

### HMI takeover (milestone 11)

| Variable | Where | Meaning |
|---|---|---|
| `BMS_STANDBY_TOKEN` | Server and Pi | The shared token (a secret, at least 20 characters). |
| `BMS_HMI_URLS` | Server | The standby HMIs, comma-separated, for example `http://192.168.10.30`. |
| `BMS_SERVER_URL` | Pi | The site server. |
| `BMS_HMI_NAME` [`hmi`] | Pi | This HMI's name in alarms and the audit log. |
| `BMS_STANDBY_CACHE` | Pi | The cached setup on the `BMSCONF` partition. |
| `BMS_PI_PORTS` | Pi | The Pi's adapters by bus name: `rs485_1=/dev/ttyBMS-rs485-1,trunk1=/dev/ttyBMS-trunk1`. |
| `BMS_HEARTBEAT_S` [5] | Both | How often the Pi asks the server, and the server asks the HMIs. |
| `BMS_TAKEOVER_AFTER_S` [30] | Pi | How long without the server before taking over. |
| `BMS_BUS_QUIET_S` [60] | Pi | How long each serial line must be quiet first. |
| `BMS_FAILBACK_AFTER_S` [300] | Pi | How long the server must be healthy before control can be handed back. |
| `BMS_MSTP_START_CMD`, `BMS_MSTP_STOP_CMD` | Pi | Start and stop the Pi's own `bms-mstp` (MAC 1) on takeover and failback. |

### Updates (milestone 12b)

| Variable | Where | Meaning |
|---|---|---|
| `BMS_UPDATER_URL` | Site server | The host's updater agent, `http://10.77.0.1:8701`. |
| `BMS_UPDATER_TOKEN` | Site server and host | The updater's token (a secret). |
| `BMS_RELEASE_DIR` [`/opt/devine-bms/releases`] | Site server | Where `bms-app` keeps releases, for the HMIs. |
| `BMS_UPDATE_DIR` | Pi | The updates folder on the `BMSCONF` partition. |
| `BMS_PI_UPDATE_CMD` | Pi | `sudo -n /usr/local/sbin/bms-pi-update stage`. |

## Other files

| File | Machine | Contents |
|---|---|---|
| `/etc/devine-bms/updater.env` | Proxmox host | `BMS_UPDATER_TOKEN`, `BMS_UPDATER_LISTEN` (`10.77.0.1:8701`), `BMS_APP_CT`, `BMS_DB_CT`, `BMS_APP_URL`. Mode 0600. |
| `/etc/devine-bms/mstp-<trunk>.env` | Host (or Pi) | One MS/TP trunk: `BACNET_IFACE`, `BACNET_MSTP_BAUD`, `BACNET_MSTP_MAC` (0 server, 1 Pi), `BACNET_MAX_MASTER`, `BACNET_MAX_INFO_FRAMES`, `BMS_MSTP_LISTEN`, `BMS_MSTP_INSTANCE`, `BMS_MSTP_APDU_TIMEOUT`, `BMS_MSTP_APDU_RETRIES`, `BMS_MSTP_ROUTE_ARGS`. See `mstp/mstp-trunk.env.example`. |
| `/etc/devine-bms/hmi.env` | Pi | `BMS_SERVER_URL` for the kiosk, and `BMS_STANDBY_URL` on a standby Pi. |
| `/var/lib/devine-bms/updater/state.json` | Host | The updater's last update, its steps and its history. |
| `/var/lib/devine-bms/config/` | Pi | `site-state.json` (standalone), `standby-cache.json` (standby), `updates/last-update.json`. |

## The setup file (`devine-bms-config/0`)

One JSON file with the whole setup:
- the site and its BACnet settings;
- rooms (`spaces`) and equipment, with `parent` and `feeds`;
- buses, devices and points, with their addresses, tags, alarms, write limits and occupancy values;
- device and equipment templates;
- graphics and site symbols;
- logic snippets.

Its JSON Schema is `config/schema/devine-bms-config-0.schema.json`, and examples are in `config/`.
- Export and import it in Setup → Site, or with `setup.cli export` and `setup.cli import`.
- An import replaces the whole setup: anything not in the file is disabled (points and devices keep their history).
- Logic diagrams use their own format (`devine-bms-logic/1`), imported and exported on the Logic page.

## Settings in the web app

### Setup → Security (admins)

| Setting | Default | Range |
|---|---|---|
| Shortest password | 10 | 10 to 128 |
| Sign out after idle | 30 minutes | 5 to 720 |
| Lock after failed logins | 5 | 3 to 20 |
| Lock for | 10 minutes | 1 to 1440 |
| Require 2FA for admins | off | |
| Haystack API on | off | Read-only; API client accounts only. |
| HMI kiosks | none | Addresses that see the site without a login (view only). |
| Allowed networks | any | If set, logins only from these networks and the server itself. |

### Setup → Alarm delivery (admins)

- **Channels per level.** Info: on screen only. Warning: email and ntfy. Critical: email, ntfy and SMS (no SMS on a
  standalone Pi).
- **Email (SMTP):** host, port, TLS mode, user name, sender address, and the name of the password variable.
- **SMS (ClickSend):** the user name and the name of the API key variable.
- **ntfy:** the server (default `https://ntfy.sh`) and an optional token variable.
- **Recipients:** name, email, phone, ntfy topic, which levels, and quiet hours for Warnings.
- Test buttons.

### Setup → Site (admins)

Display name, time zone, latitude and longitude, floor area, BACnet settings, **override times** (the hand-mode choices, default 1, 2 or 8 hours with 2 as
the default), the setup file export and import, and the Haystack model export.

### Setup → Standalone (a standalone Pi)

The log window (default 72 hours), the USB export (on, every 60 minutes), Save now, and the network.

### Setup → Updates

See [Updates](updates.md).
