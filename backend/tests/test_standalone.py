"""Standalone mode (milestone 11a, decision 028): the state file (saved on change, restored into
an empty database), what is not kept, the RAM log (one sample a minute, the window), the USB
export, CSV downloads, the first-boot setup code and the network helper.

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from devine_bms.poller.history_writer import HistoryWriter
from devine_bms.values import HisRow, Status

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db

PASSWORD = "correct horse battery"


async def standalone_stack(tmp_path, data: dict | None, first_admin: bool = True, client_ip: str = "127.0.0.1",
                           net_cmd: str = ""):
    """A standalone backend on a fresh database (PostgreSQL in RAM on the Pi)."""
    from devine_bms.api.app import create_app
    from devine_bms.db.session import make_sessionmaker
    from devine_bms.runtime import Runtime
    from devine_bms.settings import Settings
    from devine_bms.standalone.boot import make_keeper, prepare_standalone
    from devine_bms.storage.postgres import PostgresStorage
    from helpers import TEST_INTERVALS
    from setup_helpers import ADMIN, URL, fresh_database

    engine = await fresh_database()
    sessions = make_sessionmaker(engine)
    config = tmp_path / "site.json"
    if data is not None:
        config.write_text(json.dumps(data))
    (tmp_path / "usb").mkdir(exist_ok=True)
    settings = Settings(database_url=URL, mode="standalone", config_path=str(config),
                        state_file=str(tmp_path / "config" / "site-state.json"), usb_path=str(tmp_path / "usb"),
                        net_cmd=net_cmd)

    async def factory():
        site_name, standalone = await prepare_standalone(sessions, settings)
        runtime = Runtime(PostgresStorage(sessions), site_name, intervals=TEST_INTERVALS, stale_check_s=0.05,
                          flush_interval_s=0.1, history_min_interval_s=60)
        runtime.keeper = make_keeper(sessions, site_name, settings, standalone)
        runtime.keeper.debounce_s = 0.3
        runtime.settings = settings

        async def cleanup():
            pass

        return runtime, cleanup

    app = create_app(factory, alarm_tick_s=0.1, override_check_s=0.2, schedule_tick_s=0.2, logic_tick_s=0.2)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=(client_ip, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://bms") as client:
            if first_admin:
                r = await client.post("/api/auth/first-admin", json=ADMIN)
                assert r.status_code == 200, r.text
            yield {"client": client, "app": app, "sessions": sessions, "settings": settings,
                   "state": tmp_path / "config" / "site-state.json", "usb": tmp_path / "usb"}
    await engine.dispose()


async def wait_for(check, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while not await check():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.1)


# ---------- the state file ----------

async def test_the_state_file_is_saved_on_change_and_restored(tmp_path):
    from helpers import example_data

    async for s in standalone_stack(tmp_path, example_data(5099)):
        c = s["client"]
        assert (await c.get("/api/mode")).json() == {"mode": "standalone"}
        status = (await c.get("/api/standalone/status")).json()
        assert status["settings"] == {"logWindowH": 72, "usbEnabled": True, "usbIntervalMin": 60}

        # A setup change, a new user, a schedule and a logic diagram are kept...
        await c.put("/api/setup/spaces/office", json={"name": "office", "dis": "Office"})
        await c.post("/api/users", json={"username": "pat", "dis": "Pat", "role": "operator", "password": "pat's password 1"})
        await c.post("/api/schedules", json={"name": "hours", "dis": "Office hours", "weekly": {
            "mon": [{"start": "08:00", "end": "17:00"}]}})
        await c.put("/api/logic-variables/occ_sp", json={"type": "number", "kind": "constant", "value": 21})
        await c.put("/api/logic/demo/draft", json={"dis": "Demo", "blocks": []})

        async def saved():
            if not s["state"].exists():
                return False
            st = json.loads(s["state"].read_text())
            return any(sp["name"] == "office" for sp in st["config"]["spaces"]) and \
                any(r["name"] == "demo" for r in st["tables"]["logic_diagram"])
        await wait_for(saved)
        state = json.loads(s["state"].read_text())
        assert state["schema"] == "devine-bms-state/1" and state["config"]["schema"] == "devine-bms-config/0"
        assert {u["username"] for u in state["tables"]["app_user"]} == {"admin", "pat"}
        assert state["tables"]["schedule"][0]["name"] == "hours"
        assert state["tables"]["logic_variable"][0]["value"] == {"v": 21}
        assert "point_his" not in state["tables"] and "audit_log" not in state["tables"] \
            and "point_override" not in state["tables"] and "user_session" not in state["tables"]

        # ...but a failed login (only its counters change) does not write to the card.
        await asyncio.sleep(0.6)
        saves = (await c.get("/api/standalone/status")).json()["state"]["saves"]
        await c.post("/api/auth/login", json={"username": "pat", "password": "wrong password!"})
        await asyncio.sleep(0.8)
        assert (await c.get("/api/standalone/status")).json()["state"]["saves"] == saves

        # Standalone settings are kept too.
        r = (await c.put("/api/standalone/settings", json={"logWindowH": 24, "usbIntervalMin": 15})).json()
        assert r["settings"]["logWindowH"] == 24
        assert (await c.put("/api/standalone/settings", json={"logWindowH": 500})).status_code == 422
        await wait_for(lambda: _true(json.loads(s["state"].read_text())["standalone"].get("logWindowH") == 24))
    saved_state = (tmp_path / "config" / "site-state.json").read_text()

    # A power cut: the database (in RAM) is empty again. The state file brings the site back.
    (tmp_path / "site.json").unlink()
    async for s in standalone_stack(tmp_path, None, first_admin=False):
        c = s["client"]
        assert (await c.get("/api/auth/state")).json()["firstRun"] is False
        r = await c.post("/api/auth/login", json={"username": "pat", "password": "pat's password 1"})
        assert r.status_code == 200, r.text
        await c.post("/api/auth/logout")
        await c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        spaces = {x["name"] for x in (await c.get("/api/setup/spaces")).json()}
        assert "office" in spaces
        assert [x["name"] for x in (await c.get("/api/schedules")).json()] == ["hours"]
        assert any(d["name"] == "demo" for d in (await c.get("/api/logic")).json())
        assert {v["name"]: v["value"] for v in (await c.get("/api/logic-variables")).json()}["occ_sp"] == 21
        assert (await c.get("/api/standalone/status")).json()["settings"]["logWindowH"] == 24
    assert json.loads(saved_state)["tables"]["site"][0]["name"] == "dev_site"


async def _true(x):
    return bool(x)


async def test_an_empty_pi_gets_an_empty_site_and_a_setup_code(tmp_path):
    async for s in standalone_stack(tmp_path, None, first_admin=False, client_ip="192.168.50.20"):
        c = s["client"]
        st = (await c.get("/api/auth/state")).json()
        assert st["firstRun"] and not st["firstRunHere"] and st["setupCode"]
        # Only the Pi's own screen is given the code.
        assert (await c.get("/api/standalone/setup-info")).status_code == 403
        app = s["app"]
        local = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://bms")
        info = (await local.get("/api/standalone/setup-info")).json()
        assert info["setup"] and len(info["code"]) == 6
        body = {"username": "admin", "dis": "Installer", "password": PASSWORD}
        assert (await c.post("/api/auth/first-admin", json=body)).status_code == 403  # no code
        wrong = "000000" if info["code"] != "000000" else "111111"
        r = await c.post("/api/auth/first-admin", json={**body, "setupCode": wrong})
        assert r.status_code == 403 and "setup code" in r.json()["error"]
        r = await c.post("/api/auth/first-admin", json={**body, "setupCode": info["code"]})
        assert r.status_code == 200, r.text
        assert (await local.get("/api/standalone/setup-info")).json() == {"setup": False}
        site = (await c.get("/api/site")).json()
        assert site["name"] == "site" and site["dis"] == "New site"
        await local.aclose()


# ---------- the RAM log ----------

def test_the_log_keeps_one_sample_a_minute_and_every_quality_change():
    class Sink:
        rows: list = []

    w = HistoryWriter(Sink(), min_interval_s=60)
    pid = uuid.uuid4()
    t = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    for k in range(12):  # every 5 s for a minute
        w.add(HisRow(pid, t + timedelta(seconds=5 * k), float(k), None, Status.OK, None))
    w.add(HisRow(pid, t + timedelta(seconds=61), 1.0, None, Status.OK, None))
    w.add(HisRow(pid, t + timedelta(seconds=62), None, None, Status.DOWN, "offline"))  # a change: kept
    w.add(HisRow(pid, t + timedelta(seconds=63), None, None, Status.DOWN, "offline"))
    assert [(r.ts - t).total_seconds() for r in w.pending] == [0, 61, 62]


async def test_log_window_usb_export_and_csv(tmp_path):
    from sqlalchemy import insert, select

    from devine_bms.db import models as db
    from helpers import example_data

    async for s in standalone_stack(tmp_path, example_data(5099)):
        c = s["client"]
        async with s["sessions"]() as session:
            points = {p.name: p.id for p in (await session.scalars(select(db.Point))).all()}
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        rows = []
        for minutes_ago in range(5, 0, -1):
            ts = now - timedelta(minutes=minutes_ago)
            rows.append({"point_id": points["site_meter1_power"], "ts": ts, "num": 10.0 + minutes_ago, "str": None,
                         "status": "ok", "reason": None})
            rows.append({"point_id": points["site_meter1_voltage"], "ts": ts, "num": 230.0, "str": None,
                         "status": "ok", "reason": None})
        rows.append({"point_id": points["site_meter1_power"], "ts": now - timedelta(hours=100), "num": 1.0, "str": None,
                     "status": "ok", "reason": None})
        async with s["sessions"]() as session, session.begin():
            await session.execute(insert(db.PointHis.__table__), rows)

        # The window: rows older than 72 hours go.
        svc = s["app"].state.services["standalone"]
        assert await svc.prune() == 1

        # Export now: one file a month, one row a minute, one column a point.
        r = (await c.post("/api/standalone/export")).json()
        assert r["lastResult"] == "ok" and r["lastRows"] == 5, r
        month = now.astimezone(svc.tz).strftime("%Y-%m")
        lines = (s["usb"] / f"{month}.csv").read_text().splitlines()
        header = lines[0].split(",")
        assert header[0] == "time" and "site_meter1_power (kW)" in header and "site_meter1_voltage (V)" in header
        assert len(lines) == 6
        power = header.index("site_meter1_power (kW)")
        assert lines[1].split(",")[power] == "15"
        # The next export adds only what is new.
        async with s["sessions"]() as session, session.begin():
            await session.execute(insert(db.PointHis.__table__), [{
                "point_id": points["site_meter1_power"], "ts": now + timedelta(seconds=1), "num": 99.0, "str": None,
                "status": "ok", "reason": None}])
        assert (await c.post("/api/standalone/export")).json()["lastRows"] == 0  # the minute is not over yet
        assert len((s["usb"] / f"{month}.csv").read_text().splitlines()) == 6
        # Points changed: the month continues in a second file.
        await c.put("/api/setup/points/site_meter1_pf", json={**(await c.get("/api/setup/points/site_meter1_pf")).json(),
                                                              "his": False})
        svc.exported_until = None
        assert (await c.post("/api/standalone/export")).json()["lastResult"] == "ok"
        assert (s["usb"] / f"{month}-2.csv").exists()

        # A missing stick: a clear warning; the log is still there for the next export.
        for f in s["usb"].iterdir():
            f.unlink()
        s["usb"].rmdir()
        r = (await c.post("/api/standalone/export")).json()
        assert r["lastResult"] == "no USB stick"

        # Download CSV.
        text = (await c.get("/api/standalone/log.csv", params={"hours": 1})).text
        assert text.splitlines()[0].startswith("time,") and len(text.splitlines()) >= 6  # up to this minute


async def test_network_settings_use_the_pi_helper(tmp_path):
    from helpers import example_data

    script = tmp_path / "net.sh"
    log = tmp_path / "net.log"
    script.write_text(f"""#!/bin/sh
echo "$@" >> {log}
if [ "$1" = show ]; then echo '{{"mode": "dhcp", "address": "192.168.1.50/24"}}'; fi
""")
    script.chmod(0o755)
    async for s in standalone_stack(tmp_path, example_data(5099), net_cmd=str(script)):
        c = s["client"]
        got = (await c.get("/api/standalone/network")).json()
        assert got["managed"] and got["mode"] == "dhcp"
        bad = await c.put("/api/standalone/network", json={"mode": "static", "address": "192.168.1.20/24",
                                                           "gateway": "10.0.0.1"})
        assert bad.status_code == 422 and "same network" in bad.json()["error"]
        ok = (await c.put("/api/standalone/network", json={"mode": "static", "address": "192.168.1.20/24",
                                                           "gateway": "192.168.1.1", "dns": "1.1.1.1, 8.8.8.8"})).json()
        assert ok["ok"]
        assert (await c.put("/api/standalone/network", json={"mode": "dhcp"})).json()["ok"]
        assert log.read_text().splitlines() == ["show", "static 192.168.1.20/24 192.168.1.1 1.1.1.1,8.8.8.8", "dhcp"]


async def test_full_mode_has_no_standalone_api(tmp_path):
    from helpers import example_data
    from setup_helpers import running_stack

    async for s in running_stack(example_data(5099)):
        assert (await s["client"].get("/api/mode")).json() == {"mode": "full"}
        assert (await s["client"].get("/api/standalone/status")).status_code == 404


async def test_standalone_alarms_offer_no_sms(tmp_path):
    from helpers import example_data

    async for s in standalone_stack(tmp_path, example_data(5099)):
        c = s["client"]
        got = (await c.get("/api/alarm-settings")).json()["settings"]
        assert all("sms" not in chans for chans in got["channels"].values())
        got["channels"]["critical"] = ["ntfy", "sms"]
        r = await c.put("/api/alarm-settings", json=got)
        assert r.status_code == 422 and "standalone" in r.json()["error"]
