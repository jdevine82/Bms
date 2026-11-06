"""Setup API (milestone 5A): the database as master, checked changes, live reload, guard, change log.

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import asyncio
import copy
import json
import os
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from devine_bms.api.app import create_app
from devine_bms.config.sync import sync_config
from devine_bms.config.validate import parse_config
from devine_bms.db import models as db
from devine_bms.db.session import make_sessionmaker
from devine_bms.runtime import Runtime
from devine_bms.settings import Settings
from devine_bms.setup.startup import StartupError, prepare_site
from devine_bms.storage.postgres import PostgresStorage
from devine_bms.values import Status
from helpers import example_data
from setup_helpers import BACKEND, fresh_database, stack  # noqa: F401 (stack is a fixture)

URL = os.environ.get("BMS_TEST_DATABASE_URL")
if not URL:
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


async def wait_for(condition, timeout=6.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not await condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


async def point_named(client, name):
    for p in (await client.get("/api/points")).json():
        if p["name"] == name:
            return p
    return None


async def test_export_matches_the_site(stack):
    data = (await stack["client"].get("/api/setup/config")).json()
    assert data["schema"] == "devine-bms-config/0" and data["site"]["name"] == "dev_site"
    assert sorted(p["name"] for p in data["points"])[0] == "site_meter1_current"
    assert (await stack["client"].get("/api/setup/points/site_meter1_power")).json()["address"]["address"] == 0


async def test_change_a_point_goes_live_and_is_logged(stack):
    client = stack["client"]
    point = (await client.get("/api/setup/points/site_meter1_power")).json()
    point["dis"] = "Main power (renamed display)"
    result = (await client.put("/api/setup/points/site_meter1_power", json=point)).json()
    assert result["ok"] and result["applied"] and result["report"]["updated"] == {"point": 1}
    assert (await point_named(client, "site_meter1_power"))["dis"] == "Main power (renamed display)"
    log = (await client.get("/api/setup/changes")).json()
    assert log[0]["summary"] == "changed point site_meter1_power" and log[0]["client"] == "127.0.0.1"
    assert log[0]["before"]["dis"] == "Main meter power"


async def test_a_new_point_is_polled_without_a_restart(stack):
    client = stack["client"]
    new = {"dis": "Meter power (again)", "equip": "meter1", "device": "sim_meter", "type": "sensor",
           "kind": "Number", "unit": "kW", "tags": {"elec": True, "power": True}, "pollClass": "fast",
           "address": {"objectType": "holdingRegister", "address": 0, "dataType": "float32"}}
    result = (await client.put("/api/setup/points/site_meter1_power_copy", json=new)).json()
    assert result["applied"] and result["report"]["created"] == {"point": 1}

    async def good():
        p = await point_named(client, "site_meter1_power_copy")
        return p is not None and p["cur"]["status"] == "ok"
    await wait_for(good)


async def test_values_survive_a_reload(stack):
    client = stack["client"]

    async def power_good():
        return (await point_named(client, "site_meter1_power"))["cur"]["status"] == "ok"
    await wait_for(power_good)
    point = (await client.get("/api/setup/points/site_meter1_voltage")).json()
    point["dis"] = "Voltage"
    await client.put("/api/setup/points/site_meter1_voltage", json=point)
    assert (await point_named(client, "site_meter1_power"))["cur"]["status"] == "ok"  # not blanked by the reload


async def test_a_change_that_fails_the_checks_is_refused(stack):
    client = stack["client"]
    point = (await client.get("/api/setup/points/site_meter1_power")).json()
    point["device"] = "no_such_device"
    response = await client.put("/api/setup/points/site_meter1_power", json=point)
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False and any("no_such_device" in i["message"] for i in body["issues"])
    assert (await client.get("/api/setup/points/site_meter1_power")).json()["device"] == "sim_meter"


async def test_check_only_does_not_save(stack):
    client = stack["client"]
    point = (await client.get("/api/setup/points/site_meter1_power")).json()
    point["dis"] = "Only checked"
    result = (await client.put("/api/setup/points/site_meter1_power?check=true", json=point)).json()
    assert result == {"ok": True, "applied": False, "warnings": []}
    assert (await client.get("/api/setup/points/site_meter1_power")).json()["dis"] == "Main meter power"


async def test_delete_keeps_history(stack):
    client, sessions = stack["client"], stack["sessions"]

    async def has_history():
        async with sessions() as s:
            return await s.scalar(select(func.count()).select_from(db.PointHis)) > 0
    await wait_for(has_history)
    result = (await client.delete("/api/setup/points/site_meter1_pf")).json()
    assert result["applied"] and result["report"]["disabled"] == {"point": ["site_meter1_pf"]}
    assert (await client.get("/api/setup/points/site_meter1_pf")).status_code == 404
    assert await point_named(client, "site_meter1_pf") is None
    async with sessions() as s:
        row = await s.scalar(select(db.Point).where(db.Point.name == "site_meter1_pf"))
        assert row.enabled is False


async def test_cannot_remove_equipment_still_in_use(stack):
    response = await stack["client"].delete("/api/setup/equips/meter1")
    assert response.status_code == 422
    assert any("equip 'meter1' does not exist" in i["message"] for i in response.json()["issues"])


async def test_rename_a_point_keeps_its_id_and_old_name(stack):
    client, sessions = stack["client"], stack["sessions"]
    before = await point_named(client, "site_meter1_power")
    result = (await client.post("/api/setup/points/site_meter1_power/rename",
                                json={"newName": "site_meter1_active_power"})).json()
    assert result["applied"]
    after = await point_named(client, "site_meter1_active_power")
    assert after["id"] == before["id"]
    async with sessions() as s:
        alias = await s.scalar(select(db.PointAlias).where(db.PointAlias.name == "site_meter1_power"))
        assert str(alias.point_id) == before["id"]
    clash = await client.post("/api/setup/points/site_meter1_voltage/rename",
                              json={"newName": "site_meter1_power"})  # the old name is now an alias
    assert clash.status_code == 422 and "old name of point 'site_meter1_active_power'" in clash.json()["error"]
    new_point = (await client.get("/api/setup/points/site_meter1_voltage")).json()
    assert (await client.put("/api/setup/points/site_meter1_power", json={**new_point, "name": None} | {
        "name": "site_meter1_power"})).status_code == 422
    back = await client.post("/api/setup/points/site_meter1_active_power/rename",
                             json={"newName": "site_meter1_power"})  # its own old name: allowed
    assert back.json()["applied"]
    assert (await point_named(client, "site_meter1_power"))["id"] == before["id"]


async def test_rename_equipment_moves_its_points(stack):
    client = stack["client"]
    result = (await client.post("/api/setup/equips/meter1/rename", json={"newName": "main_meter"})).json()
    assert result["applied"]
    points = (await client.get("/api/setup/points")).json()
    assert {p["equip"] for p in points} == {"main_meter"}
    assert result["warnings"]  # point names no longer follow <room>_<equipment>_<function>


async def test_setup_needs_an_admin_login(stack):
    """Milestone 6A replaced the localhost-only guard: setup needs the admin role, from anywhere."""
    app = stack["app"]
    point = (await stack["client"].get("/api/setup/points/site_meter1_power")).json()
    remote = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.168.1.50", 50000)),
                               base_url="http://bms")
    async with remote:
        assert (await remote.get("/api/setup/config")).status_code == 401
        assert (await remote.put("/api/setup/points/site_meter1_power", json=point)).status_code == 401
        login = await remote.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery"})
        assert login.status_code == 200
        assert (await remote.put("/api/setup/points/site_meter1_power", json=point)).json()["applied"]
    entry = (await stack["client"].get("/api/setup/changes?limit=1")).json()[0]
    assert entry["user"] == "admin" and entry["client"] == "192.168.1.50"


async def test_import_a_whole_setup(stack):
    client = stack["client"]
    data = (await client.get("/api/setup/config")).json()
    data["points"] = [p for p in data["points"] if p["name"] != "site_meter1_current"]
    data["spaces"].append({"name": "plant", "dis": "Plant room", "tags": {}})
    result = (await client.put("/api/setup/config", json=data)).json()
    assert result["applied"] and result["report"]["disabled"] == {"point": ["site_meter1_current"]}
    assert result["report"]["created"] == {"space": 1}
    other = copy.deepcopy(data)
    other["site"]["name"] = "another_site"
    assert (await client.put("/api/setup/config", json=other)).status_code == 422


async def test_unknown_kind(stack):
    assert (await stack["client"].put("/api/setup/widgets/x", json={})).status_code == 404


async def test_startup_uses_the_database_and_imports_only_when_empty(tmp_path):
    engine = await fresh_database()
    sessions = make_sessionmaker(engine)
    try:
        site_file = tmp_path / "site.json"
        site_file.write_text(json.dumps(example_data(5020)))
        settings = Settings(config_path=str(site_file), database_url=URL)
        assert await prepare_site(sessions, settings) == "dev_site"  # empty database: imported
        async with sessions() as s:
            assert await s.scalar(select(func.count()).select_from(db.Point)) == 5
            change = await s.scalar(select(db.SetupChange))
            assert change.client == "start-up" and change.action == "import"

        changed = example_data(5020)
        changed["points"] = changed["points"][:1]
        site_file.write_text(json.dumps(changed))
        assert await prepare_site(sessions, settings) == "dev_site"  # the database wins; the file is not read
        async with sessions() as s:
            assert await s.scalar(select(func.count()).select_from(db.Point).where(db.Point.enabled)) == 5

        with pytest.raises(StartupError):
            await prepare_site(sessions, Settings(config_path=str(site_file), database_url=URL, site_name="nope"))
    finally:
        await engine.dispose()


async def test_startup_without_site_or_file(tmp_path):
    engine = await fresh_database()
    try:
        with pytest.raises(StartupError, match="no config file"):
            await prepare_site(make_sessionmaker(engine),
                               Settings(config_path=str(tmp_path / "missing.json"), database_url=URL))
    finally:
        await engine.dispose()




async def test_cli_export_and_import(tmp_path):
    import subprocess
    import sys

    engine = await fresh_database()
    await engine.dispose()
    env = {**os.environ, "BMS_DATABASE_URL": URL, "BMS_CONFIG_PATH": str(tmp_path / "none.json")}
    site_file = tmp_path / "site.json"
    site_file.write_text(json.dumps(example_data(5020)))

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "devine_bms.setup.cli", *args], env=env, cwd=BACKEND,
                              capture_output=True, text=True, timeout=60)

    first = cli("import", str(site_file))
    assert first.returncode == 0 and "as a new site (dev_site)" in first.stdout
    exported = cli("export")
    assert exported.returncode == 0 and len(json.loads(exported.stdout)["points"]) == 5
    data = json.loads(exported.stdout)
    data["points"] = data["points"][:4]
    site_file.write_text(json.dumps(data))
    second = cli("import", str(site_file))
    assert second.returncode == 0 and json.loads(second.stdout)["report"]["disabled"]
    bad = cli("import", "--check", str(BACKEND.parent / "config" / "site.example-rtu.json"))
    assert bad.returncode == 1 and "not 'dev_site'" in bad.stderr
    log = cli("changes")
    assert "imported a whole site setup" in log.stdout and "cli:" in log.stdout
