"""Maintenance counters (main spec 5.10): counter points made automatically, run hours and starts
from a real run input (the I/O simulator, polled over RTU), time that cannot be seen not counted,
service reminders and Service done, device-reported hours, saving and restart, the export, and
renames. Needs BMS_TEST_DATABASE_URL for the stack tests."""

from __future__ import annotations

import asyncio
import os

import pytest

from devine_bms.counters.derive import DEVICE, derive
from devine_bms.setup.service import _rename_in


def doc(run_tagged: bool = True) -> dict:
    return {
        "equips": [{"name": "ahu1", "dis": "AHU 1", "space": "plant"}, {"name": "meter1", "dis": "Meter"}],
        "devices": [{"name": "io1", "dis": "I/O", "protocol": "modbus", "connection": {"bus": "b", "unitId": 2}}],
        "points": [{"name": "plant_ahu1_run", "dis": "AHU run", "equip": "ahu1", "device": "io1", "type": "sensor",
                    "kind": "Bool", "tags": {"run": True} if run_tagged else {}, "address": {}},
                   {"name": "site_meter1_power", "dis": "kW", "equip": "meter1", "device": "io1", "type": "sensor",
                    "kind": "Number", "address": {}}],
    }


def test_counter_points_follow_the_run_point():
    data, changes = derive(doc())
    ours = [p for p in data["points"] if p["device"] == DEVICE]
    assert [p["name"] for p in ours] == ["plant_ahu1_run_hours", "plant_ahu1_starts"]
    assert ours[0]["unit"] == "h" and ours[0]["tags"] == {"devineRunHours": True} and ours[0]["his"]
    assert ours[1]["tags"] == {"devineStarts": True} and "unit" not in ours[1]
    assert ours[0]["address"] == {"counter": "run_hours", "equip": "ahu1"} and ours[0]["type"] == "sensor"
    assert [d for d in data["devices"] if d["name"] == DEVICE][0]["protocol"] == "counter"
    assert changes == ["added the counter point plant_ahu1_run_hours", "added the counter point plant_ahu1_starts"]
    # Idempotent, and a person's edits (display name, alarms) are kept.
    ours[0]["dis"] = "AHU 1 hours run"
    ours[0]["alarms"] = [{"type": "high", "limit": 5000, "level": "info"}]
    again, changes = derive(data)
    assert changes == [] and again == data
    # The run point goes: the counters and the device go.
    gone = [p for p in data["points"] if p["name"] != "plant_ahu1_run"]
    data2, changes = derive({**data, "points": gone})
    assert not any(p["device"] == DEVICE for p in data2["points"])
    assert not any(d["name"] == DEVICE for d in data2["devices"])
    assert changes == ["removed the counter point plant_ahu1_run_hours", "removed the counter point plant_ahu1_starts"]


def test_no_run_tag_no_counters_and_a_name_clash_is_left_alone():
    data, changes = derive(doc(run_tagged=False))
    assert not any(p["device"] == DEVICE for p in data["points"]) and changes == []
    clash = doc()
    clash["points"].append({"name": "plant_ahu1_starts", "dis": "x", "equip": "ahu1", "device": "io1",
                            "type": "sensor", "kind": "Number", "address": {}})
    data, changes = derive(clash)
    assert "could not add plant_ahu1_starts: another point has that name" in changes
    assert [p["name"] for p in data["points"] if p["device"] == DEVICE] == ["plant_ahu1_run_hours"]


def test_an_equipment_rename_keeps_its_counter_points():
    data, _ = derive(doc())
    renamed = _rename_in(data, "equips", "ahu1", "ahu_main")
    after, changes = derive(renamed)
    assert changes == []  # the same points (and history), now counting ahu_main
    assert {p["address"]["equip"] for p in after["points"] if p["device"] == DEVICE} == {"ahu_main"}


# ---------- the whole thing, on a running backend ----------

class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def _poll_until(check, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


@pytest.mark.db
async def test_counting_service_and_restart():
    if not os.environ.get("BMS_TEST_DATABASE_URL"):
        pytest.skip("BMS_TEST_DATABASE_URL is not set")
    from devine_bms.counters.engine import CounterEngine
    from devine_bms.values import Status
    from setup_helpers import running_stack
    from test_commands import bus_stack, site

    sim = await bus_stack()
    data = site(sim.port, 1)
    data["points"].append({"name": "site_lights1_run_state", "dis": "Lights on", "equip": "lights1", "device": "io1",
                           "type": "sensor", "kind": "Bool", "pollClass": "fast", "tags": {"run": True},
                           "address": {"objectType": "coil", "address": 5, "dataType": "bit"}})
    next(e for e in data["equips"] if e["name"] == "lights1")["service"] = {"hours": 1, "starts": 3, "level": "warning"}
    io = sim.units[2]
    try:
        async for s in running_stack(data):
            c, app = s["client"], s["app"]
            svc = app.state.services
            # The counter points were made at start-up, on the BMS counters device.
            names = {p["name"]: p for p in (await c.get("/api/points")).json()}
            assert "site_lights1_run_hours" in names and "site_lights1_starts" in names
            assert names["site_lights1_run_hours"]["unit"] == "h" and not names["site_lights1_run_hours"]["writable"]
            # Drive the engine by hand with a clock we move.
            eng: CounterEngine = svc["counters"]
            eng._task.cancel()
            clock = Clock()
            eng.clock = clock
            rt = svc["runtime"]
            run = next(p for p in rt.config.points if p.name == "site_lights1_run_state")

            async def state_is(v):
                io.coils[5] = v
                await _poll_until(lambda: (lv := rt.cache.get(run.id)) is not None and lv.status == Status.OK
                                  and lv.value is v)

            await state_is(False)
            await eng.tick()
            await state_is(True)
            for _ in range(61):  # an hour on, in one-minute ticks
                await eng.tick()
                clock.t += 60
            st = (await c.get("/api/counters?equip=lights1")).json()[0]
            assert st["starts"] == 1 and 0.99 <= st["hours"] <= 1.02 and st["running"]
            assert st["due"] and st["service"] == {"hours": 1.0, "starts": 3, "level": "warning"}
            # The counter points carry the values.
            hours_pt = next(p for p in rt.config.points if p.name == "site_lights1_run_hours")
            assert abs(rt.cache.get(hours_pt.id).value - st["hours"]) < 0.02
            # The reminder is a Warning alarm.
            await _poll_until(lambda: True, 0.3)
            await asyncio.sleep(0.5)
            alarms = (await c.get("/api/alarms")).json()
            assert any(a["message"].startswith("Service due: lights1, 1 hours") and a["level"] == "warning"
                       for a in alarms), alarms
            # Off, on, off, on: two more starts.
            for v in (False, True, False, True):
                await state_is(v)
                await eng.tick()
                clock.t += 1
            assert (await c.get("/api/counters?equip=lights1")).json()[0]["starts"] == 3
            # Stale or Bad time is not counted, and marks the counters estimated.
            before = (await c.get("/api/counters?equip=lights1")).json()[0]
            rt.tracker.bad(run, Status.DOWN, "test")
            for _ in range(3):
                clock.t += 60
                await eng.tick()
            after = (await c.get("/api/counters?equip=lights1")).json()[0]
            assert abs(after["hours"] - before["hours"]) < 0.02 and after["estimated"] and after["unseenHours"] >= 0.04
            # Service done (admins): since-service counts restart, totals do not; the alarm clears.
            r = await c.post("/api/counters/lights1/service-done")
            assert r.status_code == 200, r.text
            done = r.json()
            assert done["sinceService"] == {"hours": 0.0, "starts": 0} and done["hours"] == after["hours"]
            assert not done["due"] and done["servicedBy"] == "admin"
            await asyncio.sleep(0.5)
            assert not any(a["message"].startswith("Service due: lights1") and a["active"]
                           for a in (await c.get("/api/alarms")).json())
            audit = (await c.get("/api/audit?action=service-done")).json()
            assert audit and "lights1" in audit[0]["summary"]
            # The export carries the counts; the setup revision does not change with them.
            cfg = (await c.get("/api/setup/config"))
            exported = next(x for x in cfg.json()["counters"] if x["equip"] == "lights1")
            assert exported["starts"] == 3 and exported["servicedBy"] == "admin"
            rev = cfg.headers["x-setup-revision"]
            clock.t += 60
            await eng.tick()
            await eng.save()
            assert (await c.get("/api/setup/config")).headers["x-setup-revision"] == rev
            # A restart (a new engine from the database) keeps the counts and the service mark.
            fresh = CounterEngine(rt, eng.sessions, rt.site_name)
            await fresh.reload()
            kept = fresh.status_of(fresh.equips["lights1"])
            assert kept["starts"] == 3 and kept["sinceService"]["starts"] == 0
            # A device that reports its own run hours is used instead.
            r = await c.put("/api/setup/points/site_lights1_hours_dev", json={
                "name": "site_lights1_hours_dev", "dis": "Hours (device)", "equip": "lights1", "device": "meter2",
                "type": "sensor", "kind": "Number", "unit": "h", "pollClass": "fast", "tags": {"devineRunHours": True},
                "address": {"objectType": "holdingRegister", "address": 0, "dataType": "uint16"}})
            assert r.status_code == 200, r.text
            await _poll_until(lambda: any(p.name == "site_lights1_hours_dev" for p in rt.config.points))
            dev = next(p for p in rt.config.points if p.name == "site_lights1_hours_dev")
            await _poll_until(lambda: (lv := rt.cache.get(dev.id)) is not None and lv.status == Status.OK)
            st = (await c.get("/api/counters?equip=lights1")).json()[0]
            assert st["hoursSource"] == "device" and st["hours"] == float(rt.cache.get(dev.id).value)
            # The run point goes: so do its counters (disabled, history kept).
            r = await c.delete("/api/setup/points/site_lights1_run_state")
            assert r.status_code == 200, r.text
            names = {p["name"] for p in (await c.get("/api/points")).json()}
            assert "site_lights1_run_hours" not in names
            assert (await c.get("/api/counters")).json() == []
            # Operators cannot mark a service done.
            r = await c.post("/api/users", json={"username": "op", "dis": "Op", "role": "operator",
                                                 "password": "a long enough pw"})
            assert r.status_code == 200
            import httpx

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("192.168.1.9", 5000)),
                                         base_url="http://bms") as op:
                await op.post("/api/auth/login", json={"username": "op", "password": "a long enough pw"})
                assert (await op.get("/api/counters")).status_code == 200  # anyone signed in can read them
                assert (await op.post("/api/counters/lights1/service-done")).status_code == 403
    finally:
        await sim.stop()
