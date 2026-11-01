"""Migration and config sync against a real PostgreSQL database.

Set BMS_TEST_DATABASE_URL to an empty database the tests may wipe, for example
postgresql+asyncpg://bms:<password>@localhost/bms_test. Skipped when it is not set.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from devine_bms.config.sync import sync_config
from devine_bms.config.validate import parse_config
from devine_bms.db import models as db
from devine_bms.db.session import make_sessionmaker

pytestmark = pytest.mark.db
URL = os.environ.get("BMS_TEST_DATABASE_URL")
BACKEND = Path(__file__).resolve().parents[1]
EXAMPLE = BACKEND.parent / "config" / "site.example.json"

if not URL:
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)


@pytest.fixture
async def engine():
    eng = create_async_engine(URL)
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    alembic_cfg = Config(str(BACKEND / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", URL)
    alembic_cfg.attributes["configure_logger"] = False
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    yield eng
    await eng.dispose()


def example():
    return json.loads(EXAMPLE.read_text())


async def count(session, table):
    return await session.scalar(select(func.count()).select_from(table))


async def test_sync_creates_everything(engine):
    config, _ = parse_config(example())
    async with make_sessionmaker(engine)() as session, session.begin():
        report = await sync_config(session, config)
    assert report.created == {"site": 1, "equip": 1, "device": 1, "point": 5}
    async with make_sessionmaker(engine)() as session:
        assert await count(session, db.Point) == 5
        point = await session.scalar(select(db.Point).where(db.Point.name == "site_meter1_import_energy"))
        assert point.address["wordOrder"] == "low_first"
        assert point.tags == {"elec": True, "energy": True, "import": True}
        assert point.kind == "Number" and point.unit == "kWh" and point.poll_class == "slow"


async def test_second_sync_changes_nothing(engine):
    config, _ = parse_config(example())
    async with make_sessionmaker(engine)() as session, session.begin():
        await sync_config(session, config)
    async with make_sessionmaker(engine)() as session, session.begin():
        report = await sync_config(session, config)
    assert report.created == {} and report.updated == {}


async def test_ids_are_kept_and_removed_points_are_disabled(engine):
    data = example()
    config, _ = parse_config(data)
    async with make_sessionmaker(engine)() as session, session.begin():
        await sync_config(session, config)
        first_id = (await session.scalar(select(db.Point).where(db.Point.name == "site_meter1_power"))).id

    data["points"] = [p for p in data["points"] if p["name"] != "site_meter1_pf"]
    data["points"][0]["dis"] = "Power (renamed display)"
    config, _ = parse_config(data)
    async with make_sessionmaker(engine)() as session, session.begin():
        report = await sync_config(session, config)
    assert report.disabled == {"point": ["site_meter1_pf"]}
    assert report.updated == {"point": 1}

    async with make_sessionmaker(engine)() as session:
        power = await session.scalar(select(db.Point).where(db.Point.name == "site_meter1_power"))
        pf = await session.scalar(select(db.Point).where(db.Point.name == "site_meter1_pf"))
        assert power.id == first_id
        assert pf.enabled is False


async def test_history_table_accepts_rows(engine):
    config, _ = parse_config(example())
    async with make_sessionmaker(engine)() as session, session.begin():
        await sync_config(session, config)
        point = await session.scalar(select(db.Point).where(db.Point.name == "site_meter1_power"))
        session.add(db.PointHis(point_id=point.id, ts=func.now(), num=12.3, status="ok"))
        session.add(db.PointHis(point_id=point.id, ts=func.now() + text("interval '1 second'"),
                                status="down", reason="timeout"))
    async with make_sessionmaker(engine)() as session:
        assert await count(session, db.PointHis) == 2
