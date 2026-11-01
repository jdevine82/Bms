# Devine BMS backend

Python 3.12 site backend. Milestone 2 spec: `../docs/specs/m2-simulated-modbus-to-trend.md`.

This first part has the site configuration format, the database schema and migrations,
the Modbus value decoder, the Haystack tag libraries, and the simulated power meter.
Polling, the API and the web app come next.

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

## Tests

```bash
uv run pytest
```

The database tests run only when `BMS_TEST_DATABASE_URL` points at a database they may wipe (see `.env.example`).
