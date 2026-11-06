"""Shared fixtures for the setup tests (milestone 5A): a fresh database and a running backend."""

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from devine_bms.api.app import create_app
from devine_bms.config.sync import sync_config
from devine_bms.config.validate import parse_config
from devine_bms.db.session import make_sessionmaker
from devine_bms.runtime import Runtime
from devine_bms.storage.postgres import PostgresStorage
from helpers import TEST_INTERVALS, example_data
from simulators.modbus_meter import MeterSimulator

URL = os.environ.get("BMS_TEST_DATABASE_URL")
ADMIN = {"username": "admin", "dis": "Test admin", "password": "correct horse battery"}
BACKEND = Path(__file__).resolve().parents[1]


async def fresh_database():
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", URL)
    cfg.attributes["configure_logger"] = False
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return engine


@pytest.fixture
async def stack():
    """A simulated meter, the example site in the database, a running backend and an API client."""
    sim = MeterSimulator(port=0, update_interval_s=0.05)
    await sim.start()
    async for value in running_stack(example_data(sim.port), extra={"sim": sim}):
        yield value
    await sim.stop()


async def running_stack(data: dict, extra: dict | None = None):
    """The site `data` in a fresh database, a running backend and an API client."""
    engine = await fresh_database()
    sessions = make_sessionmaker(engine)
    config, _ = parse_config(data)
    async with sessions() as session, session.begin():
        await sync_config(session, config)

    async def factory():
        runtime = Runtime(PostgresStorage(sessions), "dev_site", intervals=TEST_INTERVALS, stale_check_s=0.05,
                          flush_interval_s=0.1)

        async def cleanup():
            pass

        return runtime, cleanup

    app = create_app(factory, alarm_tick_s=0.1, override_check_s=0.2, schedule_tick_s=0.2, logic_tick_s=0.2)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://bms") as client:
            # Milestone 6A: every route needs a login. The client is the site's first admin.
            first = await client.post("/api/auth/first-admin", json=ADMIN)
            assert first.status_code == 200, first.text
            yield {"client": client, "app": app, "sessions": sessions, **(extra or {})}
    await engine.dispose()


