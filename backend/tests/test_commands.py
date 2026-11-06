"""Commands, write limits, hand mode with override timers, roles and the HMI PIN
(main spec 5.6 and 7; milestone 7A).

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import asyncio
import os
import struct
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update

from devine_bms.commands.service import CommandService
from devine_bms.db import models as db
from helpers import example_data
from setup_helpers import running_stack
from simulators.modbus_bus import IoModuleModel, MeterModel, ModbusSimulator

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


def site(port: int, sim_port: int) -> dict:
    data = example_data(sim_port)
    data["buses"] = [{"name": "rs485_1", "dis": "Line 1", "type": "modbus_rtu_over_tcp", "host": "127.0.0.1",
                      "port": port, "timeoutS": 0.5, "retries": 0}]
    data["equips"] += [{"name": "lights1", "dis": "Lights", "tags": {"lighting": True}},
                       {"name": "meter2", "dis": "Meter 2", "tags": {"elec": True, "meter": True}}]
    data["devices"] += [{"name": "io1", "dis": "I/O", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 2}},
                        {"name": "meter2", "dis": "Meter 2", "protocol": "modbus",
                         "connection": {"bus": "rs485_1", "unitId": 1}}]
    data["points"] += [
        {"name": "site_lights1_relay", "dis": "Lights relay", "equip": "lights1", "device": "io1", "type": "cmd",
         "kind": "Bool", "pollClass": "fast", "tags": {"light": True},
         "address": {"objectType": "coil", "address": 3, "dataType": "bit"}},
        {"name": "site_meter2_limit", "dis": "Demand limit", "equip": "meter2", "device": "meter2", "type": "sp",
         "kind": "Number", "unit": "kW", "pollClass": "fast", "writeLimits": {"min": 10, "max": 200},
         "tags": {"elec": True, "power": True},
         "address": {"objectType": "holdingRegister", "address": 20, "dataType": "float32"}},
        {"name": "site_meter2_nolimit", "dis": "Unlimited", "equip": "meter2", "device": "meter2", "type": "sp",
         "kind": "Number", "pollClass": "fast", "tags": {"elec": True},
         "address": {"objectType": "holdingRegister", "address": 22, "dataType": "int16"}},
    ]
    return data


async def bus_stack():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    return sim


async def pid(client, name):
    return next(p["id"] for p in (await client.get("/api/points")).json() if p["name"] == name)


def limit_value(sim) -> float:
    return struct.unpack(">f", struct.pack(">HH", *sim.units[1].writable[0:2]))[0]


async def wait_for(check, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while not await check():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.1)


async def test_set_limits_confirm_and_audit():
    sim = await bus_stack()
    try:
        async for s in running_stack(site(sim.port, 5099)):
            c = s["client"]
            points = {p["name"]: p for p in (await c.get("/api/points")).json()}
            assert points["site_meter2_limit"]["writable"] and points["site_meter2_limit"]["writeLimits"] == {"min": 10, "max": 200}
            assert not points["site_meter1_power"]["writable"]
            limit, relay = points["site_meter2_limit"]["id"], points["site_lights1_relay"]["id"]

            r = (await c.post(f"/api/points/{limit}/command", json={"value": 120})).json()
            assert r["ok"] and r["value"] == 120.0 and limit_value(sim) == 120.0
            assert r["readBack"]["status"] == "ok" and r["readBack"]["value"] == 120.0

            high = await c.post(f"/api/points/{limit}/command", json={"value": 250})
            assert high.status_code == 422 and high.json()["code"] == "limits" and limit_value(sim) == 120.0
            none = await c.post(f"/api/points/{points['site_meter2_nolimit']['id']}/command", json={"value": 5})
            assert none.status_code == 422 and none.json()["code"] == "no-limits"
            sensor = await c.post(f"/api/points/{points['site_meter1_power']['id']}/command", json={"value": 5})
            assert sensor.status_code == 422

            unconfirmed = await c.post(f"/api/points/{relay}/command", json={"value": True})
            assert unconfirmed.status_code == 409 and unconfirmed.json()["code"] == "confirm"
            assert (await c.post(f"/api/points/{relay}/command", json={"value": True, "confirm": True})).json()["ok"]
            assert sim.units[2].coils[3] is True

            log = (await c.get("/api/audit?action=command")).json()
            assert {e["summary"] for e in log} >= {"set site_meter2_limit to 120", "set site_lights1_relay to on"}
            entry = next(e for e in log if e["name"] == "site_meter2_limit")
            assert entry["user"] == "admin" and entry["after"]["value"] == 120.0
    finally:
        await sim.stop()


async def test_hand_mode_times_out_back_to_the_previous_value():
    sim = await bus_stack()
    try:
        async for s in running_stack(site(sim.port, 5099)):
            c = s["client"]
            relay = await pid(c, "site_lights1_relay")
            await asyncio.sleep(0.5)  # a poll has read the relay (off)
            bad_time = await c.post(f"/api/points/{relay}/command", json={"value": True, "mode": "hand", "hours": 3,
                                                                          "confirm": True})
            assert bad_time.status_code == 422  # not one of the choices 1, 2, 8
            r = (await c.post(f"/api/points/{relay}/command", json={"value": True, "mode": "hand", "confirm": True})).json()
            assert r["override"]["setBy"] == "admin" and sim.units[2].coils[3] is True
            ends = datetime.fromisoformat(r["override"]["endsAt"])
            assert timedelta(hours=1.9) < ends - datetime.now(UTC) <= timedelta(hours=2)  # the default, 2 hours
            listed = (await c.get("/api/overrides")).json()
            assert listed[0]["point"] == "site_lights1_relay"
            point = next(p for p in (await c.get("/api/points")).json() if p["name"] == "site_lights1_relay")
            assert point["override"]["value"] is True

            # A new service, as after a restart, loads the override from the database.
            fresh = CommandService(None, s["sessions"], "dev_site", check_s=3600)
            await fresh.start()
            assert str(list(fresh.overrides)[0]) == relay and fresh.overrides[list(fresh.overrides)[0]]["value"] is True
            await fresh.stop()

            async with s["sessions"]() as session:
                await session.execute(update(db.PointOverride).values(ends_at=datetime.now(UTC) - timedelta(seconds=1)))
                await session.commit()

            async def ended():
                return not (await c.get("/api/overrides")).json()
            await wait_for(ended)
            assert sim.units[2].coils[3] is False  # back to the value before the override
            log = (await c.get("/api/audit?action=release")).json()
            assert log[0]["user"] == "override timer" and "override time ended" in log[0]["summary"]
    finally:
        await sim.stop()


async def test_release_to_auto_and_set_ends_hand_mode():
    sim = await bus_stack()
    try:
        async for s in running_stack(site(sim.port, 5099)):
            c = s["client"]
            limit = await pid(c, "site_meter2_limit")
            await c.post(f"/api/points/{limit}/command", json={"value": 50})
            # Straight on, before the next poll: hand mode must still return to 50, not a stale value.
            await c.post(f"/api/points/{limit}/command", json={"value": 150, "mode": "hand", "hours": 1, "confirm": True})
            await c.post(f"/api/points/{limit}/command", json={"value": 160, "mode": "hand", "hours": 8, "confirm": True})
            assert limit_value(sim) == 160.0
            released = (await c.post(f"/api/points/{limit}/release")).json()
            assert released["ok"] and limit_value(sim) == 50.0  # the value from before the first override
            assert (await c.get("/api/overrides")).json() == []
            not_hand = await c.post(f"/api/points/{limit}/release")
            assert not_hand.status_code == 422

            await c.post(f"/api/points/{limit}/command", json={"value": 150, "mode": "hand", "hours": 1, "confirm": True})
            await c.post(f"/api/points/{limit}/command", json={"value": 99})  # a lasting change ends hand mode
            assert (await c.get("/api/overrides")).json() == [] and limit_value(sim) == 99.0
    finally:
        await sim.stop()


async def test_override_choices_are_a_site_setting():
    sim = await bus_stack()
    try:
        async for s in running_stack(site(sim.port, 5099)):
            c = s["client"]
            assert (await c.get("/api/command-settings")).json() == {"choicesH": [1, 2, 8], "defaultH": 2}
            assert (await c.put("/api/command-settings", json={"choicesH": [0.5, 4], "defaultH": 2})).status_code == 422
            assert (await c.put("/api/command-settings", json={"choicesH": [0.5, 4], "defaultH": 4})).status_code == 200
            relay = await pid(c, "site_lights1_relay")
            r = (await c.post(f"/api/points/{relay}/command", json={"value": True, "mode": "hand", "confirm": True})).json()
            ends = datetime.fromisoformat(r["override"]["endsAt"])
            assert timedelta(hours=3.9) < ends - datetime.now(UTC) <= timedelta(hours=4)
    finally:
        await sim.stop()


def client_at(app, ip):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 1)), base_url="http://bms")


async def test_roles_kiosk_and_hmi_pin():
    sim = await bus_stack()
    try:
        async for s in running_stack(site(sim.port, 5099)):
            admin, app = s["client"], s["app"]
            limit = await pid(admin, "site_meter2_limit")
            for name, role in (("vic", "viewer"), ("ola", "operator")):
                await admin.post("/api/users", json={"username": name, "dis": name.title(), "role": role,
                                                     "password": "a long enough pw"})
            await admin.put("/api/auth/settings", json={"kioskAddresses": ["10.0.0.50"]})
            async with client_at(app, "10.0.0.7") as viewer:
                await viewer.post("/api/auth/login", json={"username": "vic", "password": "a long enough pw"})
                assert (await viewer.post(f"/api/points/{limit}/command", json={"value": 20})).status_code == 403
            async with client_at(app, "10.0.0.8") as op:
                await op.post("/api/auth/login", json={"username": "ola", "password": "a long enough pw"})
                assert (await op.post(f"/api/points/{limit}/command", json={"value": 20})).json()["ok"]
                assert (await op.post("/api/auth/me/pin", json={"pin": "1111", "password": "a long enough pw"})).status_code == 422
                assert (await op.post("/api/auth/me/pin", json={"pin": "4826", "password": "wrong"})).status_code == 403
                assert (await op.post("/api/auth/me/pin", json={"pin": "4826", "password": "a long enough pw"})).status_code == 200
                assert (await op.get("/api/auth/pin-users")).status_code == 403  # not a kiosk
            async with client_at(app, "10.0.0.50") as kiosk:
                assert (await kiosk.post(f"/api/points/{limit}/command", json={"value": 30})).status_code == 403
                assert (await kiosk.get("/api/auth/pin-users")).json() == [{"username": "ola", "dis": "Ola"}]
                assert (await kiosk.post("/api/auth/pin", json={"username": "ola", "pin": "0000"})).status_code == 401
                ok = await kiosk.post("/api/auth/pin", json={"username": "ola", "pin": "4826"})
                assert ok.status_code == 200
                me = (await kiosk.get("/api/auth/me")).json()
                assert me["username"] == "ola" and me["pinSession"] is True
                assert (await kiosk.post(f"/api/points/{limit}/command", json={"value": 30})).json()["ok"]
                async with s["sessions"]() as session:  # 5 minutes idle: back to view only
                    await session.execute(update(db.UserSession).where(db.UserSession.kind == "pin")
                                          .values(last_seen=datetime.now(UTC) - timedelta(minutes=6)))
                    await session.commit()
                assert (await kiosk.get("/api/auth/me")).json()["kiosk"] is True
            async with client_at(app, "10.0.0.9") as elsewhere:
                assert (await elsewhere.post("/api/auth/pin", json={"username": "ola", "pin": "4826"})).status_code == 403
            log = (await admin.get("/api/audit?action=command")).json()
            assert [e["user"] for e in log][:2] == ["ola", "ola"]
    finally:
        await sim.stop()
