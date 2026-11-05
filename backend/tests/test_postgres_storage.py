"""PostgresStorage and the runtime end to end with a real database (milestone 2 spec 9, check 2).

Skipped unless BMS_TEST_DATABASE_URL is set (see test_db_sync.py).
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from devine_bms.config.sync import sync_config
from devine_bms.config.validate import parse_config
from devine_bms.db.session import make_sessionmaker
from devine_bms.runtime import Runtime
from devine_bms.storage.postgres import PostgresStorage
from devine_bms.values import HisRow, Status
from helpers import TEST_INTERVALS, example_data
from simulators.modbus_meter import MeterSimulator

URL = os.environ.get("BMS_TEST_DATABASE_URL")
BACKEND = Path(__file__).resolve().parents[1]
if not URL:
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


@pytest.fixture
async def sessions():
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", URL)
    cfg.attributes["configure_logger"] = False
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield make_sessionmaker(engine)
    await engine.dispose()


async def synced(sessions, port):
    config, _ = parse_config(example_data(port))
    async with sessions() as session, session.begin():
        await sync_config(session, config)
    return config


async def test_load_runtime_config(sessions):
    await synced(sessions, 5999)
    rc = await PostgresStorage(sessions).load_runtime_config("dev_site")
    assert len(rc.devices) == 1 and rc.devices[0].connection.port == 5999
    assert {p.name for p in rc.points} >= {"site_meter1_power", "site_meter1_import_energy"}
    assert rc.site_tz == "Sydney"


async def test_unknown_site(sessions):
    with pytest.raises(LookupError):
        await PostgresStorage(sessions).load_runtime_config("nope")


async def test_write_and_read_history(sessions):
    await synced(sessions, 5999)
    storage = PostgresStorage(sessions)
    point = (await storage.load_runtime_config("dev_site")).points[0]
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [
        HisRow(point.id, t0, 1.5, None, Status.OK, None),
        HisRow(point.id, t0 + timedelta(seconds=5), None, None, Status.DOWN, "no answer"),
    ]
    await storage.write_history(rows)
    await storage.write_history(rows[:1])  # duplicate (point, time) is ignored
    read = await storage.read_history(point.id, t0, t0 + timedelta(minutes=1))
    assert read == rows
    assert await storage.read_history(point.id, t0 + timedelta(seconds=1), t0 + timedelta(minutes=1)) == rows[1:]


async def test_runtime_stores_history_in_postgres(sessions):
    sim = MeterSimulator(port=0, update_interval_s=0.05)
    await sim.start()
    try:
        await synced(sessions, sim.port)
        storage = PostgresStorage(sessions)
        runtime = Runtime(storage, "dev_site", intervals=TEST_INTERVALS, stale_check_s=0.05, flush_interval_s=0.1)
        await runtime.start()
        await asyncio.sleep(1.0)
        await runtime.stop()
    finally:
        await sim.stop()
    power = next(p for p in runtime.config.points if p.name == "site_meter1_power")
    now = datetime.now(UTC)
    rows = await storage.read_history(power.id, now - timedelta(minutes=1), now + timedelta(seconds=1))
    assert len(rows) >= 5
    assert all(r.status == Status.OK for r in rows)


async def test_bacnet_devices_round_trip(sessions):
    """BACnet settings, connections and addresses survive the database (milestone 4b)."""
    import json as _json
    from pathlib import Path as _Path

    data = _json.loads((_Path(__file__).resolve().parents[2] / "config" / "site.example-bacnet.json").read_text())
    config, _ = parse_config(data)
    async with sessions() as session, session.begin():
        await sync_config(session, config)
    rc = await PostgresStorage(sessions).load_runtime_config(config.site.name)
    assert rc.bacnet.device_instance == config.bacnet.device_instance
    device = rc.devices[0]
    assert device.protocol == "bacnet_ip" and device.connection.cov is True
    mode = next(p for p in rc.points if p.name.endswith("_mode"))
    assert mode.address.object_type == "multiStateValue" and mode.enum == ("Cool", "Heat", "Fan")


async def test_mstp_devices_are_linked_to_their_trunk(sessions):
    """An MS/TP device must reach the runtime with its trunk, or it is never polled (fix in 4d)."""
    import json as _json
    from pathlib import Path as _Path

    data = _json.loads((_Path(__file__).resolve().parents[2] / "config" / "site.example-mstp.json").read_text())
    config, _ = parse_config(data)
    async with sessions() as session, session.begin():
        await sync_config(session, config)
    rc = await PostgresStorage(sessions).load_runtime_config(config.site.name)
    trunk = rc.buses[0]
    assert trunk.settings.type == "bacnet_mstp"
    assert rc.devices[0].bus_id == trunk.id
    assert rc.devices[0].connection.mac == 5


async def test_mqtt_devices_round_trip(sessions):
    import json as _json
    from pathlib import Path as _Path

    data = _json.loads((_Path(__file__).resolve().parents[2] / "config" / "site.example-mqtt.json").read_text())
    config, _ = parse_config(data)
    async with sessions() as session, session.begin():
        await sync_config(session, config)
    rc = await PostgresStorage(sessions).load_runtime_config(config.site.name)
    broker = rc.buses[0]
    assert broker.settings.type == "mqtt_broker" and broker.settings.password_env == "BMS_MQTT_PASSWORD"
    assert {d.bus_id for d in rc.devices} == {broker.id}
    power = next(p for p in rc.points if p.name == "plant_plug1_power")
    assert power.address.topic == "tele/plug1/SENSOR" and power.address.json_path == "ENERGY.Power"
