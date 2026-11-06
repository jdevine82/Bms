# Notes for the user and installation guide

The full guide is written at the end of the project (main spec 18, item 13b). Until then, this
list collects what it must cover and where the facts are now, so nothing is lost. Each
milestone adds to it.

## Installation

- Development machine: Python 3.12 and uv, Node and npm, PostgreSQL 16 (TimescaleDB on sites; `BMS_ALLOW_PLAIN_POSTGRES=true` only on a dev machine), migrations with `uv run alembic upgrade head`. Facts: `docs/decisions/016-native-dev-setup.md`, `docs/specs/m2-simulated-modbus-to-trend.md`.
- Site server (manual steps until the packaging of milestone 11): database, backend service, built web app (`BMS_WEB_DIST`), listen address (`BMS_LISTEN_HOST`, default 127.0.0.1), Tailscale and `tailscale serve` for HTTPS (decision 010).
- Pi HMI kiosk: `hmi/README.md` (provision.sh, 64-bit Raspberry Pi OS Lite, cage).
- BACnet MS/TP service: `mstp/README.md` (build.sh, systemd template, udev rules in `deploy/udev`).
- MQTT broker (Mosquitto): `docs/specs/m4d-mqtt.md`.

## Settings reference

- Environment variables (`backend/.env.example`, `backend/src/devine_bms/settings.py`): `BMS_DATABASE_URL`, `BMS_CONFIG_PATH`, `BMS_SITE_NAME`, `BMS_LISTEN_HOST`, `BMS_LISTEN_PORT`, `BMS_LOG_LEVEL`, `BMS_WEB_DIST`, `BMS_ALLOW_PLAIN_POSTGRES`; secrets such as MQTT broker passwords through the variable named in `passwordEnv`.
- The config file `devine-bms-config/0` and its JSON Schema (`config/schema/`), examples in `config/`. The database is the master copy; the file is import and export (decision 021).
- Security settings (Setup → Security): shortest password, idle time-out, lockout count and time, 2FA for admins, HMI kiosk addresses, allowed networks (`docs/specs/m6-logins-alarms.md`).
- Alarm delivery settings (Setup → Alarm delivery): channels per level, SMTP (`BMS_SMTP_PASSWORD`), ClickSend (`BMS_CLICKSEND_API_KEY`), ntfy server and token, recipients and quiet hours, test messages (`docs/specs/m6-logins-alarms.md`, "Setting up delivery on a site").
- Alarm definitions: point alarms (high, low, state, stale; deadband, delay, level, message), device offline alarms (only where set), template defaults.

## Users and logins

- First admin on the first-run page, from a browser on the server itself only.
- Roles (viewer, operator, admin) and what each can do (main spec 7).
- Adding people, 2FA with an authenticator app, lost phone (admin removes 2FA), lockout and unlock.
- Forgotten admin password: `uv run python -m devine_bms.setup.cli password <username>` on the server.
- HMI kiosks by address; allowed networks; passwords cross the LAN unencrypted, so keep the web app on the device or management network (main spec 8).
- The audit log: what it records, filters, CSV, kept for 1 year.

## Commissioning

- Discover, the 4-step wizard, the mapping table and CSV, device and equipment templates, test reads (`docs/specs/m5-setup.md`).
- Export and import of a whole setup (web app and `setup.cli`).

## Operating

- Points and trends, CSV export of history (milestone 2), the HMI (milestone 3).
- Alarms: the header count, acknowledging with comments, history, the notifications log; what Info, Warning and Critical mean and who is told; why a Bad value does not raise a limit alarm.

## Bench and troubleshooting

- Bench tools: Modbus, BACnet, MS/TP and MQTT tools named in the milestone 4 specs; simulators in `simulators/`.
- Value quality (Good, Stale, Bad) and what each reason means (main spec 5.2.1).
