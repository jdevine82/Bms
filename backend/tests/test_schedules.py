"""Occupancy schedules, holiday calendars and controller copies (main spec 5.4; milestone 7B).

The model tests need nothing; the rest need BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import asyncio
import os
import struct
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from devine_bms.schedules import bacnet as sb
from devine_bms.schedules.model import CalendarIn, ControllerTarget, ScheduleIn, next_change, state_at

SYDNEY = ZoneInfo("Australia/Sydney")


# ---------- the rule, without a database ----------

def office(**extra):
    return ScheduleIn.model_validate({"name": "office", "dis": "Office", "weekly": {
        d: [{"start": "07:00", "end": "18:00"}] for d in ("mon", "tue", "wed", "thu", "fri")}, **extra})


def test_one_off_beats_holiday_beats_week():
    cal = {"hols": CalendarIn(name="hols", dis="Holidays", dates=[{"start": "2026-12-24", "end": "2026-12-26", "dis": "Xmas"}])}
    s = office(calendars=["hols"], exceptions=[{"start": "2026-12-24T09:00", "end": "2026-12-24T12:00",
                                                "occupied": True, "dis": "stocktake"}])
    assert state_at(s, cal, datetime(2026, 12, 23, 10)) == (True, "weekly")
    assert state_at(s, cal, datetime(2026, 12, 25, 10)) == (False, "holiday: Xmas")
    assert state_at(s, cal, datetime(2026, 12, 24, 10)) == (True, "exception: stocktake")
    assert state_at(s, cal, datetime(2026, 12, 24, 13)) == (False, "holiday: Xmas")
    assert state_at(s, cal, datetime(2026, 12, 26, 10))[0] is False  # the range is inclusive
    assert next_change(s, cal, datetime(2026, 12, 24, 13)) == (datetime(2026, 12, 28, 7, 0), True)


def test_whole_day_and_validation():
    s = ScheduleIn.model_validate({"name": "plant", "dis": "Plant", "weekly": {"sat": [{"start": "00:00", "end": "24:00"}]}})
    assert state_at(s, {}, datetime(2026, 11, 7, 23, 59))[0] is True  # Saturday
    assert state_at(s, {}, datetime(2026, 11, 8, 0, 0))[0] is False
    for bad in ({"weekly": {"mon": [{"start": "09:00", "end": "08:00"}]}},
                {"weekly": {"mon": [{"start": "07:00", "end": "12:00"}, {"start": "11:00", "end": "13:00"}]}},
                {"weekly": {"monday": []}},
                {"exceptions": [{"start": "2026-11-07T10:00", "end": "2026-11-07T09:00", "occupied": True}]}):
        with pytest.raises(ValueError):
            ScheduleIn.model_validate({"name": "x", "dis": "X", **bad})


def test_controller_plan_replaces_whole_days():
    cal = {"hols": CalendarIn(name="hols", dis="H", dates=[{"start": "2026-12-25"}])}
    s = office(calendars=["hols"], exceptions=[{"start": "2026-12-23T18:00", "end": "2026-12-23T21:00", "occupied": True}])
    target = ControllerTarget(device="ctrl", instance=1)
    p = sb.plan(s, cal, target, today=datetime(2026, 12, 1).date())
    assert p["weekly"][0] == [("07:00", 1), ("18:00", 0)] and p["weekly"][5] == []
    assert p["exceptions"]["2026-12-25"] == [("00:00", 0)]
    assert p["exceptions"]["2026-12-23"] == [("00:00", 0), ("07:00", 1), ("21:00", 0)]  # the evening extended
    assert "2026-11-30" not in p["exceptions"]  # past days are not written


# ---------- running, with a database ----------

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)

from helpers import example_data  # noqa: E402
from setup_helpers import running_stack  # noqa: E402
from simulators.modbus_bus import IoModuleModel, MeterModel, ModbusSimulator  # noqa: E402
import test_bacnet as tb  # noqa: E402


def site(port: int) -> dict:
    data = example_data(5099)
    data["buses"] = [{"name": "rs485_1", "dis": "Line 1", "type": "modbus_rtu_over_tcp", "host": "127.0.0.1",
                      "port": port, "timeoutS": 0.5, "retries": 0}]
    data["equips"] += [{"name": "lights1", "dis": "Lights", "tags": {"lighting": True}},
                       {"name": "meter2", "dis": "Meter 2", "tags": {"elec": True, "meter": True}}]
    data["devices"] += [{"name": "io1", "dis": "I/O", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 2}},
                        {"name": "meter2", "dis": "Meter 2", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 1}}]
    data["points"] += [
        {"name": "site_lights1_relay", "dis": "Lights relay", "equip": "lights1", "device": "io1", "type": "cmd",
         "kind": "Bool", "pollClass": "fast", "tags": {"light": True}, "occupancy": {"occupied": True, "unoccupied": False},
         "address": {"objectType": "coil", "address": 3, "dataType": "bit"}},
        {"name": "site_meter2_limit", "dis": "Demand limit", "equip": "meter2", "device": "meter2", "type": "sp",
         "kind": "Number", "unit": "kW", "pollClass": "fast", "writeLimits": {"min": 10, "max": 200},
         "occupancy": {"occupied": 150, "unoccupied": 50}, "tags": {"elec": True, "power": True},
         "address": {"objectType": "holdingRegister", "address": 20, "dataType": "float32"}},
    ]
    return data


def now_local() -> datetime:
    return datetime.now(SYDNEY).replace(tzinfo=None, second=0, microsecond=0)


def around_now(occupied: bool) -> dict:
    n = now_local()
    return {"start": (n - timedelta(hours=1)).isoformat(timespec="minutes"),
            "end": (n + timedelta(hours=1)).isoformat(timespec="minutes"), "occupied": occupied}


ALWAYS = {d: [{"start": "00:00", "end": "24:00"}] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


async def wait_for(check, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.1)


def limit_of(sim) -> float:
    return struct.unpack(">f", struct.pack(">HH", *sim.units[1].writable[0:2]))[0]


async def test_occupancy_writes_points_and_exceptions_switch_them():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    try:
        async for s in running_stack(site(sim.port)):
            c = s["client"]
            bad = await c.put("/api/setup/points/site_meter2_limit", params={"check": "true"}, json={
                **(await c.get("/api/setup/points/site_meter2_limit")).json(), "occupancy": {"occupied": 500, "unoccupied": 50}})
            assert bad.status_code == 422 and "write limits" in bad.text
            made = await c.post("/api/schedules", json={"name": "office", "dis": "Office hours", "weekly": ALWAYS,
                                                        "equips": ["lights1"], "tags": ["meter"]})
            assert made.status_code == 200, made.text
            body = made.json()
            assert body["now"] == {"occupied": True, "why": "weekly"}
            assert set(body["equipsResolved"]) == {"lights1", "meter1", "meter2"}  # by name and by tag
            await wait_for(lambda: sim.units[2].coils[3] is True and limit_of(sim) == 150.0)

            changed = await c.put("/api/schedules/office", json={**body, "exceptions": [around_now(False) | {"dis": "closed today"}]})
            assert changed.json()["now"] == {"occupied": False, "why": "exception: closed today"}
            await wait_for(lambda: sim.units[2].coils[3] is False and limit_of(sim) == 50.0)
            log = (await c.get("/api/audit?action=schedule-run")).json()
            assert any("Unoccupied (exception: closed today); wrote 2 points" in e["summary"] for e in log)

            other = await c.post("/api/schedules", json={"name": "lights", "dis": "Lights", "equips": ["lights1"]})
            assert other.status_code == 422 and "already follows schedule office" in other.json()["error"]
    finally:
        await sim.stop()


async def test_hand_mode_wins_and_ends_at_the_schedules_value():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    try:
        async for s in running_stack(site(sim.port)):
            c = s["client"]
            body = (await c.post("/api/schedules", json={"name": "office", "dis": "Office", "weekly": ALWAYS,
                                                         "equips": ["meter2"]})).json()
            await wait_for(lambda: limit_of(sim) == 150.0)
            limit = next(p["id"] for p in (await c.get("/api/points")).json() if p["name"] == "site_meter2_limit")
            await c.post(f"/api/points/{limit}/command", json={"value": 99, "mode": "hand", "hours": 1, "confirm": True})
            await c.put("/api/schedules/office", json={**body, "exceptions": [around_now(False)]})
            await asyncio.sleep(0.8)
            assert limit_of(sim) == 99.0  # hand mode is left alone
            await c.post(f"/api/points/{limit}/release")
            assert limit_of(sim) == 50.0  # ends at the schedule's value, not the value from before (150)
            log = (await c.get("/api/audit?action=release")).json()
            assert "wrote the schedule's value 50" in log[0]["summary"]
    finally:
        await sim.stop()


async def test_holiday_calendars_and_roles():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    try:
        async for s in running_stack(site(sim.port)):
            c, app = s["client"], s["app"]
            today = now_local().date().isoformat()
            assert (await c.put("/api/holiday-calendars/closures", json={
                "name": "closures", "dis": "Closures", "dates": [{"start": today, "dis": "Shutdown"}]})).status_code == 200
            body = (await c.post("/api/schedules", json={"name": "office", "dis": "Office", "weekly": ALWAYS,
                                                         "calendars": ["closures"], "equips": ["lights1"]})).json()
            assert body["now"] == {"occupied": False, "why": "holiday: Shutdown"}
            assert (await c.delete("/api/holiday-calendars/closures")).status_code == 422  # in use

            for name, role in (("vic", "viewer"), ("ola", "operator")):
                await c.post("/api/users", json={"username": name, "dis": name, "role": role, "password": "a long enough pw"})
            client = lambda ip: httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 1)), base_url="http://bms")  # noqa: E731
            async with client("10.0.0.7") as viewer, client("10.0.0.8") as operator:
                await viewer.post("/api/auth/login", json={"username": "vic", "password": "a long enough pw"})
                await operator.post("/api/auth/login", json={"username": "ola", "password": "a long enough pw"})
                assert len((await viewer.get("/api/schedules")).json()) == 1
                assert (await viewer.put("/api/schedules/office", json=body)).status_code == 403
                ok = await operator.put("/api/schedules/office", json={**body, "calendars": []})
                assert ok.json()["now"]["occupied"] is True  # operators edit everything (decision 7)
            log = (await c.get("/api/audit?action=schedule")).json()
            assert log[0]["user"] == "ola" and log[0]["summary"] == "changed schedule office"
    finally:
        await sim.stop()


async def test_controller_copy_push_check_and_accept():
    sim = tb.BacnetSimulator(port=tb.free_udp_port(), update_interval_s=0.2)
    await sim.start()
    data = example_data(5099)
    data["bacnet"] = {"localAddress": "127.0.0.1/8", "port": tb.free_udp_port(), "deviceInstance": 3999001}
    data["equips"].append({"name": "ahu1", "dis": "AHU 1", "tags": {"ahu": True}})
    data["devices"].append({"name": "ctrl", "dis": "Controller", "protocol": "bacnet_ip",
                            "connection": {"address": sim.endpoint, "deviceInstance": sim.instance, "timeoutS": 1}})
    try:
        async for s in running_stack(data):
            c = s["client"]
            later = (now_local() + timedelta(days=30)).date()  # inside the year written to the controller
            body = {"name": "office", "dis": "Office", "weekly": {"mon": [{"start": "07:00", "end": "18:00"}]},
                    "exceptions": [{"start": f"{later}T00:00", "end": f"{later + timedelta(days=1)}T00:00",
                                    "occupied": False}],
                    "equips": ["ahu1"], "controller": {"device": "ctrl", "instance": 1}}
            made = await c.post("/api/schedules", json=body)
            assert made.status_code == 200, made.text
            pushed = (await c.post("/api/schedules/office/controller/push")).json()
            assert pushed["inSync"] is True
            weekly = sim.schedule.weeklySchedule
            assert [(str(tv.time)[:5], tv.value.get_value()) for tv in weekly[0].daySchedule] == [("07:00", 1), ("18:00", 0)]
            assert len(sim.schedule.exceptionSchedule) == 1
            checked = (await c.post("/api/schedules/office/controller/check")).json()
            assert checked["inSync"] is True and checked["differences"] == []

            # Someone changes Monday on the controller itself.
            from bacpypes3.basetypes import DailySchedule, TimeValue
            from bacpypes3.primitivedata import Time, Unsigned
            weekly[0] = DailySchedule(daySchedule=[TimeValue(time=Time("06:30:00"), value=Unsigned(1)),
                                                   TimeValue(time=Time("19:00:00"), value=Unsigned(0))])
            changed = (await c.post("/api/schedules/office/controller/check")).json()
            assert changed["inSync"] is False
            assert changed["differences"] == ["mon: the BMS has 07:00=1, 18:00=0, the controller has 06:30=1, 19:00=0"]
            accepted = (await c.post("/api/schedules/office/controller/accept")).json()
            assert accepted["weekly"]["mon"] == [{"start": "06:30", "end": "19:00"}]
            assert (await c.post("/api/schedules/office/controller/check")).json()["inSync"] is True

            mstp = await c.put("/api/schedules/office", json={**body, "controller": {"device": "sim_meter", "instance": 1}})
            assert mstp.status_code == 422 and "BACnet/IP" in mstp.json()["error"]
    finally:
        await sim.stop()
