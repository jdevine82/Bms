# Devine BMS backend

Python 3.12 site backend. Milestone 2 spec: `../docs/specs/m2-simulated-modbus-to-trend.md`.

Done so far: the site configuration format, the database schema and migrations, the Haystack tag
libraries, the simulated power meter, Modbus TCP polling with block reads and speed classes, value
quality (Good, Bad, Stale), the live value cache, history storage, and the REST and WebSocket API.

## Developer setup (native install, no Docker)

1. **Python tools:** install [uv](https://docs.astral.sh/uv/). It installs Python 3.12 and the pinned packages from `uv.lock`:

   ```bash
   cd backend
   uv sync
   ```

2. **PostgreSQL 16 + TimescaleDB:** install both natively, following Timescale's install guide for your OS
   (https://docs.timescale.com/self-hosted/latest/install/). Then create the database user and databases
   (pick your own password):

   ```bash
   sudo -u postgres psql -c "CREATE ROLE bms LOGIN PASSWORD 'your-password'"
   sudo -u postgres psql -c "CREATE DATABASE bms OWNER bms"
   sudo -u postgres psql -c "CREATE DATABASE bms_test OWNER bms"
   sudo -u postgres psql -d bms -c "CREATE EXTENSION IF NOT EXISTS timescaledb"
   sudo -u postgres psql -d bms_test -c "CREATE EXTENSION IF NOT EXISTS timescaledb"
   ```

3. **Settings:** copy `.env.example` to `.env` and fill in the password.

4. **Create the tables:**

   ```bash
   uv run alembic upgrade head
   ```

   Without TimescaleDB this stops with an error. On a development machine only, you can set
   `BMS_ALLOW_PLAIN_POSTGRES=true` to keep history in a plain table with no retention policy.

## Site configuration

The site is described by one file in the format `devine-bms-config/0`
(JSON Schema: `../config/schema/devine-bms-config-0.schema.json`). Start from the example:

```bash
cp ../config/site.example.json ../config/site.json
```

Check a file:

```bash
uv run python -c "from devine_bms.config.validate import load_config; c, w = load_config('../config/site.json'); print('OK', len(c.points), 'points'); [print(i) for i in w]"
```

After changing `config/model.py`, refresh the published schema:

```bash
uv run python -m devine_bms.config.write_schema
```

## Simulated power meter

```bash
uv run python ../simulators/modbus_meter.py --port 5020
```

`--mode down | exception | frozen` starts it in a fault mode (see the file for details).

## Run the backend with the API and web app

```bash
cd ../web && npm install && npm run build && cd ../backend
uv run python ../simulators/modbus_meter.py --port 5020
```

In a second terminal:

```bash
BMS_WEB_DIST=../web/dist uv run uvicorn devine_bms.api.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The API is described at http://127.0.0.1:8000/docs. There are no logins yet,
so keep it on `127.0.0.1` (milestone 2 spec 2).

| Path | What it gives |
|---|---|
| `GET /api/health` | Backend status, devices, history writer |
| `GET /api/site` | The site, its rooms and equipment |
| `GET /api/points` | Points with live values; filters `equip`, `space`, `tag` (repeatable) |
| `GET /api/points/{id}` | One point |
| `GET /api/points/{id}/history?start=&end=` | History (UTC times; thinned above 10,000 rows) |
| `GET /api/points/{id}/history.csv?start=&end=` | The same as CSV in site local time |
| `WS /api/live` | Send `{"subscribe": "all"}` or a list of ids; get a snapshot, then each change |

## Run the backend without the API

Starts polling the devices in `config/site.json`, applies value quality, and stores history.

```bash
uv run python ../simulators/modbus_meter.py --port 5020
```

In a second terminal:

```bash
uv run python -m devine_bms.run
```

It syncs the config file into the database first, then logs one JSON line per connection change
and a status line every minute. Stop it with Ctrl+C.

**24-hour soak run (milestone 2 spec 9):** leave both running for 24 hours, then check the status
lines show `failures 0` and steady memory use.

## Tests

```bash
uv run pytest
```

The database tests run only when `BMS_TEST_DATABASE_URL` points at a database they may wipe (see `.env.example`).
