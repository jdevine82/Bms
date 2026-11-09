# Installing a development machine

For Devine BMS staff who build, run and test the software. A Linux machine (or WSL) is assumed.

## What you need

- **Python 3.12** through [uv](https://docs.astral.sh/uv/). uv installs the right Python and the pinned packages.
- **Node 22** (LTS) and npm, for the web app.
- **PostgreSQL 16**, with **TimescaleDB** if you want history to behave as on a site. Without TimescaleDB, set
  `BMS_ALLOW_PLAIN_POSTGRES=true` (a development machine only, never a site).
- Optional, for the MS/TP service: git, make and a C compiler (`sudo apt install git build-essential`).
- Optional, for the MQTT tests: Mosquitto (`sudo apt install mosquitto`).

## 1. The backend

```bash
cd backend
uv sync
```

## 2. The databases

Pick your own password. `bms` is the development database and `bms_test` an empty one the tests may wipe.

```bash
sudo -u postgres psql -c "CREATE ROLE bms LOGIN PASSWORD 'your-password'"
```

```bash
sudo -u postgres psql -c "CREATE DATABASE bms OWNER bms"
```

```bash
sudo -u postgres psql -c "CREATE DATABASE bms_test OWNER bms"
```

With TimescaleDB, also run this for each database:

```bash
sudo -u postgres psql -d bms -c "CREATE EXTENSION IF NOT EXISTS timescaledb"
```

The HMI takeover test needs a second empty database:

```bash
sudo -u postgres createdb -O bms bms_test_pi
```

## 3. Settings

Copy `backend/.env.example` to `backend/.env` and fill in the password. `.env` is never committed. The variables are
listed in the [settings reference](settings-reference.md).

## 4. The tables

```bash
cd backend
uv run alembic upgrade head
```

## 5. The web app

```bash
cd web
npm ci
npm run build
```

## 6. Run it with a simulated device

In one terminal, a simulated power meter:

```bash
cd backend
uv run python ../simulators/modbus_meter.py --port 5020
```

In another, the backend serving the built web app:

```bash
cd backend
BMS_WEB_DIST=../web/dist uv run uvicorn devine_bms.api.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The first time, the page asks you to create the first admin. This page is only offered to a
browser on the same machine. The API is described at http://127.0.0.1:8000/docs.

The first start imports `config/site.json` if it exists (copy one of the `config/site.example*.json` files). After that,
the database is the master copy of the setup.

Other simulators are in `simulators/`:
- the Modbus meter: `--framing rtu --units meter:1,io:2` for an RS485 line, `--mode down|exception|frozen` for faults;
- BACnet;
- MQTT.

## 7. Tests

Backend tests (the database tests need `BMS_TEST_DATABASE_URL`, and the takeover test `BMS_TEST_PI_DATABASE_URL`):

```bash
cd backend
BMS_TEST_DATABASE_URL=postgresql+asyncpg://bms:your-password@localhost/bms_test uv run pytest
```

Browser tests run against a running backend, for example:

```bash
cd web
BMS_URL=http://127.0.0.1:8000 npm run test:e2e
```

Each suite (`test:setup`, `test:auth`, `test:alarms`, `test:commands`, `test:schedules`, `test:graphics`,
`test:graphics-editor`, `test:logic`, `test:logic-templates`, `test:standalone`; also `tests/takeover.mjs`,
`tests/updates.mjs` and `tests/haystack.mjs`) says at its top which simulators and settings it needs. Most expect a
fresh database loaded from `config/site.example-setup.json`.

## 8. The MS/TP service (optional)

```bash
cd mstp
./build.sh --with-test-tools
```

Its tests use two linked pseudo-terminals, so no RS485 hardware is needed:

```bash
cd backend
BMS_MSTP_BIN=../mstp/build/bms-mstp BMS_BACSERV_BIN=../mstp/build/bacserv uv run pytest tests/test_mstp.py
```

## Command-line tools

| Command (from `backend/`) | What it does |
|---|---|
| `uv run python -m devine_bms.setup.cli export > site.json` | Exports the whole site setup. |
| `uv run python -m devine_bms.setup.cli import site.json --check` | Checks a setup file; without `--check` it imports it. |
| `uv run python -m devine_bms.setup.cli password <username>` | Sets a new password and unlocks the account (the way back in). |
| `uv run python -m devine_bms.config.write_schema` | Rewrites the published JSON Schema after `config/model.py` changes. |
| `uv run python -m devine_bms.run` | Polls the devices without the API (soak tests). |
