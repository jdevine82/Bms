"""Control logic (main spec 5.3.1 and 5.3.2; milestone 10A): the format, the checks, every v1
block, quality and fail-safes, and (with BMS_TEST_DATABASE_URL) the runtime and the API."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from devine_bms.logic.check import Site, SitePoint, SiteVariable, errors, plan
from devine_bms.logic.evaluate import Evaluator, Place
from devine_bms.logic.model import BLOCKS

T0 = datetime(2026, 11, 6, 2, 0, tzinfo=UTC)  # 13:00 in Sydney


def site() -> Site:
    return Site(
        points={
            "room_temp": SitePoint("room_temp", "Number"),
            "fan_cmd": SitePoint("fan_cmd", "Bool", writable=True, equip="ahu1"),
            "valve": SitePoint("valve", "Number", writable=True, equip="ahu1"),
            "fan_state": SitePoint("fan_state", "Bool"),
            "sched_point": SitePoint("sched_point", "Bool", writable=True, occupancy=True),
            "taken": SitePoint("taken", "Bool", writable=True),
        },
        variables={"occ_sp": SiteVariable("occ_sp", "number"), "latch": SiteVariable("latch", "bool", "memory")},
        schedules={"office_hours"}, equips={"ahu1"}, located=True, writers={"taken": "other_diagram"},
    )


def block(id_, type_, x=0, **settings):
    return {"id": id_, "type": type_, "settings": settings, "position": {"x": x, "y": 0}}


def wire(a, b):
    (fb, fp), (tb, tp) = a.split("."), b.split(".")
    return {"from": {"block": fb, "port": fp}, "to": {"block": tb, "port": tp}}


def cooling() -> dict:
    """Run the fan when the room is above the setpoint (with a deadband), after 5 s."""
    return {"schema": "devine-bms-logic/1", "name": "ahu1_cooling", "dis": "AHU 1 cooling", "scope": "equipment",
            "equip": "ahu1",
            "blocks": [block("temp", "point_read", point="room_temp", onBad="fallback", fallback=21.0),
                       block("sp", "variable_read", variable="occ_sp"),
                       block("hot", "compare", 1, op=">", deadband=0.5),
                       block("wait", "delay_on", 2, inputs={"time": 5}),
                       block("fan", "point_write", 3, point="fan_cmd", failsafe={"mode": "value", "value": False})],
            "wires": [wire("temp.out", "hot.a"), wire("sp.out", "hot.b"), wire("hot.out", "wait.in"),
                      wire("wait.out", "fan.in")]}


class Reader:
    def __init__(self, points=None, variables=None, schedules=None):
        self.points = points or {}
        self.variables = variables or {}
        self.schedules = schedules or {}

    def point(self, name):
        return self.points.get(name)

    def variable(self, name):
        return self.variables.get(name)

    def schedule(self, name):
        return self.schedules.get(name)


def runner(diagram, place=None):
    p, issues = plan(diagram, site())
    assert p is not None and not errors(issues), [i.message for i in errors(issues)]
    return Evaluator(p, place or Place(ZoneInfo("Australia/Sydney"), -33.87, 151.21))


def run(ev, reader, seconds, start=T0):
    """Step once a second; returns the last step."""
    step = None
    for k in range(seconds + 1):
        step = ev.step(start + timedelta(seconds=k), reader)
    return step


def written(step, point):
    return next(o.value for o in step.outputs if o.point == point)


# ---------- the format and the checks ----------

def test_the_v1_block_set():
    assert set(BLOCKS) == {"point_read", "point_write", "variable_read", "variable_write", "schedule", "and", "or",
                           "not", "compare", "limit", "scale", "delay_on", "delay_off", "min_on_off", "astro", "pid",
                           "manual_auto"}


def test_a_good_diagram_passes_and_is_ordered_inputs_to_outputs():
    p, issues = plan(cooling(), site())
    assert not errors(issues)
    assert p.order.index("temp") < p.order.index("hot") < p.order.index("wait") < p.order.index("fan")


def messages(diagram, s=None):
    _p, issues = plan(diagram, s or site())
    return [f"{i.block}: {i.message}" if i.block else i.message for i in errors(issues)]


def test_checks():
    d = cooling()
    d["blocks"] += [
        block("x", "teapot"),
        block("bad", "compare", op="~"),
        block("dup", "not"), block("dup", "not"),
        block("w1", "point_write", point="room_temp"),  # not writable
        block("w2", "point_write", point="sched_point"),  # follows a schedule
        block("w3", "point_write", point="taken", inputs={"in": True}),  # another diagram writes it
        block("w4", "point_write", point="nope", inputs={"in": True}),
        block("c", "variable_write", variable="occ_sp", inputs={"in": 1.0}),  # a constant
        block("r", "point_read", point="room_temp"),  # fallback mode without a value
        block("sch", "schedule", schedule="weekends"),
        block("n", "not"),  # unwired, no value
    ]
    d["wires"] += [wire("hot.out", "fan.in"),  # wired twice
                   wire("sp.out", "dup.in"),  # number into bool
                   wire("temp.out", "hot.zz")]
    got = messages(d)
    expected = [
        "x: unknown block type 'teapot'",
        "dup: the block ID 'dup' is used twice",
        "fan: input 'in' is wired twice",
        "w1: 'room_temp' cannot be written (only writable setpoints and commands)",
        "w2: 'sched_point' already follows a schedule (its occupancy values); a point has one writer",
        "w3: 'taken' is already written by the diagram 'other_diagram'",
        "w4: point 'nope' does not exist",
        "c: 'occ_sp' is a constant (set by a person); only memory variables are written by logic",
        "r: choose a fallback value for when the point is not Good, or use the fail-safe",
        "sch: schedule 'weekends' does not exist",
        "n: input 'in' is not wired and has no value",
        "hot: wire temp.out → hot.zz: 'zz' is not an input of hot",
    ]
    for e in expected:
        assert e in got, (e, got)
    assert any(m.startswith("bad: op") for m in got)
    assert any("joins number to bool" in m for m in got)


def test_loops_need_a_timing_block():
    looped = {"name": "loop", "blocks": [block("a", "not"), block("b", "not")],
              "wires": [wire("a.out", "b.in"), wire("b.out", "a.in")]}
    assert any("a loop without a timing block" in m for m in messages(looped))
    # Through a delay it is fine: the delay reads its input from the previous cycle.
    ok = {"name": "osc", "blocks": [block("a", "not"), block("d", "delay_on", inputs={"time": 0})],
          "wires": [wire("a.out", "d.in"), wire("d.out", "a.in")]}
    assert messages(ok) == []


def test_astro_needs_the_site_location_and_unreadable_json():
    s = site()
    s.located = False
    d = {"name": "lights", "blocks": [block("sun", "astro"), block("o", "point_write", point="fan_cmd")],
         "wires": [wire("sun.out", "o.in")]}
    assert "sun: the astro time switch needs the site's latitude and longitude (Setup → Site)" in messages(d, s)
    assert messages({"name": "Bad Name"})[0].startswith("name:")


# ---------- the blocks ----------

def test_compare_with_deadband_and_delay_on():
    ev = runner(cooling())
    r = Reader({"room_temp": 22.0}, {"occ_sp": 22.0})
    assert written(run(ev, r, 0), "fan_cmd") is False  # 22 is not above 22.5
    r.points["room_temp"] = 22.6
    step = run(ev, r, 3, T0 + timedelta(seconds=1))
    assert step.values[("hot", "out")] is True and written(step, "fan_cmd") is False  # waiting 5 s
    assert step.info["wait"]["remaining"] == pytest.approx(2.0)
    step = run(ev, r, 3, T0 + timedelta(seconds=5))
    assert written(step, "fan_cmd") is True
    r.points["room_temp"] = 21.8  # inside the deadband: stays on
    assert written(run(ev, r, 0, T0 + timedelta(seconds=10)), "fan_cmd") is True
    r.points["room_temp"] = 21.4  # below 22 - 0.5: off at once
    assert written(run(ev, r, 0, T0 + timedelta(seconds=11)), "fan_cmd") is False


def test_bad_inputs_use_the_fallback_or_the_failsafe():
    ev = runner(cooling())
    step = run(ev, Reader({}, {"occ_sp": 20.0}), 6)  # room_temp offline: fallback 21 > 20.5
    assert step.info["temp"]["bad"] is True and written(step, "fan_cmd") is True
    d = cooling()
    d["blocks"][0]["settings"] = {"point": "room_temp", "onBad": "failsafe"}
    ev = runner(d)
    step = run(ev, Reader({}, {"occ_sp": 20.0}), 2)
    out = next(o for o in step.outputs if o.point == "fan_cmd")
    assert out.value is None and out.failsafe.mode == "value"  # bad all the way: the fail-safe applies


def _single(type_, inputs, out_type="number", **settings):
    sink = "valve" if out_type == "number" else "fan_cmd"
    d = {"name": "t", "blocks": [block("b", type_, inputs=inputs, **settings), block("o", "point_write", point=sink)],
         "wires": [wire("b.out", "o.in")]}
    return runner(d)


def test_limit_with_rate_and_scale():
    ev = _single("limit", {"in": 50.0, "min": 0.0, "max": 100.0}, rate=5)
    assert run(ev, Reader(), 0).values[("b", "out")] == 50
    ev.plan.settings["b"].inputs["in"] = 100.0
    step = run(ev, Reader(), 2, T0 + timedelta(seconds=1))  # 5 %/s for 3 s
    assert step.values[("b", "out")] == pytest.approx(65) and step.info["b"]["clamping"] == "rate"
    ev.plan.settings["b"].inputs["in"] = 150.0
    step = run(ev, Reader(), 20, T0 + timedelta(seconds=5))
    assert step.values[("b", "out")] == 100 and step.info["b"]["clamping"] == "max"
    # 4-20 mA to 0-50 °C, clamped; swapped output range inverts.
    ev = _single("scale", {"in": 12.0}, inMin=4, inMax=20, outMin=0, outMax=50)
    assert run(ev, Reader(), 0).values[("b", "out")] == pytest.approx(25)
    ev.plan.settings["b"].inputs["in"] = 30.0
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=1)).values[("b", "out")] == 50
    ev = _single("scale", {"in": 2.5}, inMin=0, inMax=10, outMin=100, outMax=0)
    assert run(ev, Reader(), 0).values[("b", "out")] == pytest.approx(75)


def test_delay_off_and_minimum_on_off():
    ev = _single("delay_off", {"in": True, "time": 10.0}, "bool")
    assert run(ev, Reader(), 0).values[("b", "out")] is True
    ev.plan.settings["b"].inputs["in"] = False
    assert run(ev, Reader(), 9, T0 + timedelta(seconds=1)).values[("b", "out")] is True
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=11)).values[("b", "out")] is False

    ev = _single("min_on_off", {"in": True, "minOn": 60.0, "minOff": 30.0}, "bool")
    assert run(ev, Reader(), 0).values[("b", "out")] is True  # first change is allowed at once
    ev.plan.settings["b"].inputs["in"] = False
    step = run(ev, Reader(), 0, T0 + timedelta(seconds=20))
    assert step.values[("b", "out")] is True and step.info["b"]["remaining"] == pytest.approx(40)
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=60)).values[("b", "out")] is False
    ev.plan.settings["b"].inputs["in"] = True
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=70)).values[("b", "out")] is False  # min off 30 s
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=90)).values[("b", "out")] is True


def test_gates_manual_auto_and_schedule():
    d = {"name": "g", "blocks": [
        block("s", "schedule", schedule="office_hours"),
        block("and", "and", count=3, inputs={"in2": True, "in3": True}),
        block("not", "not"),
        block("ma", "manual_auto", valueType="bool", inputs={"manual": True}),
        block("o", "point_write", point="fan_cmd")],
        "wires": [wire("s.out", "and.in1"), wire("and.out", "not.in"), wire("not.out", "ma.auto"),
                  wire("ma.out", "o.in")]}
    ev = runner(d)
    assert written(run(ev, Reader(schedules={"office_hours": True}), 0), "fan_cmd") is False
    assert written(run(ev, Reader(schedules={"office_hours": False}), 0, T0 + timedelta(seconds=1)), "fan_cmd") is True
    ev.plan.settings["ma"].inputs["mode"] = True  # Manual: the manual value
    step = run(ev, Reader(schedules={"office_hours": True}), 0, T0 + timedelta(seconds=2))
    assert written(step, "fan_cmd") is True and step.info["ma"]["mode"] == "Manual"


def test_pid_reverse_acting_anti_windup_and_bumpless_tracking():
    d = {"name": "p", "blocks": [
        block("pid", "pid", inputs={"pv": 18.0, "sp": 21.0}, kp=5, ti=30, outMin=0, outMax=100),
        block("o", "point_write", point="valve")], "wires": [wire("pid.out", "o.in")]}
    ev = runner(d)
    first = run(ev, Reader(), 0).values[("pid", "out")]
    later = run(ev, Reader(), 30, T0 + timedelta(seconds=1)).values[("pid", "out")]
    assert first == pytest.approx(15) and later > first  # heating: below setpoint, output rises
    run(ev, Reader(), 600, T0 + timedelta(seconds=40))  # held at 100 for minutes
    assert ev.state["pid"]["out"] == 100
    ev.plan.settings["pid"].inputs["pv"] = 23.0  # now above setpoint: it comes off 100 at once
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=700)).values[("pid", "out")] < 100
    # Disabled, it tracks; enabling again starts from the tracked value (bumpless).
    ev.plan.settings["pid"].inputs.update(enable=False, track=40.0, pv=21.0)
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=701)).values[("pid", "out")] == 40
    ev.plan.settings["pid"].inputs["enable"] = True
    assert run(ev, Reader(), 0, T0 + timedelta(seconds=702)).values[("pid", "out")] == pytest.approx(40)


def test_astro_night_switch_in_sydney():
    d = {"name": "a", "blocks": [block("sun", "astro", active="night", sunsetOffset=-30),
                                 block("o", "point_write", point="fan_cmd")], "wires": [wire("sun.out", "o.in")]}
    ev = runner(d)
    noon = datetime(2026, 11, 6, 13, 0, tzinfo=ZoneInfo("Australia/Sydney"))
    step = ev.step(noon, Reader())
    assert written(step, "fan_cmd") is False and step.info["sun"]["next"].startswith("2026-11-06T18:57")
    assert written(ev.step(noon.replace(hour=19), Reader()), "fan_cmd") is True  # after sunset - 30 min
    assert written(ev.step(noon.replace(hour=4), Reader()), "fan_cmd") is True  # before sunrise
    assert written(ev.step(noon.replace(hour=7), Reader()), "fan_cmd") is False


def test_variable_write_and_a_failing_block():
    d = {"name": "v", "blocks": [block("t", "point_read", point="fan_state", onBad="failsafe"),
                                 block("w", "variable_write", variable="latch")],
         "wires": [wire("t.out", "w.in")]}
    ev = runner(d)
    step = run(ev, Reader({"fan_state": True}), 0)
    assert [(v.variable, v.value) for v in step.variables] == [("latch", True)]
    assert run(ev, Reader({}), 0, T0 + timedelta(seconds=1)).variables == []  # bad: nothing written
    # A block that fails gives bad outputs and its output goes to the fail-safe.
    d = {"name": "a", "blocks": [block("sun", "astro"), block("o", "point_write", point="fan_cmd")],
         "wires": [wire("sun.out", "o.in")]}
    ev = runner(d, Place(ZoneInfo("UTC")))  # no location
    step = run(ev, Reader(), 0)
    assert "latitude" in step.errors["sun"] and written(step, "fan_cmd") is None


needs_db = pytest.mark.skipif(not os.environ.get("BMS_TEST_DATABASE_URL"), reason="BMS_TEST_DATABASE_URL is not set")


# ---------- the runtime and the API ----------

def lights_logic(failsafe=None) -> dict:
    """Lights on when the site variable says so and the reading on register 22 is above 50; the
    demand limit follows that reading scaled 0-100 to 0-400 (clamped by its write limits)."""
    return {"name": "lights_logic", "dis": "Lights logic", "scope": "site",
            "variables": [{"name": "count", "type": "number", "kind": "memory", "value": 0}],
            "blocks": [
                block("want", "variable_read", variable="lights_on"),
                block("level", "point_read", point="site_meter2_nolimit", onBad="failsafe"),
                block("high", "compare", 1, op=">", inputs={"b": 50.0}),
                block("both", "and", 2),
                block("lights", "point_write", 3, point="site_lights1_relay",
                      failsafe=failsafe or {"mode": "value", "value": False}),
                block("scale", "scale", 1, inMin=0, inMax=100, outMin=0, outMax=400),
                block("limit", "point_write", 3, point="site_meter2_limit", failsafe={"mode": "hold"}),
                block("keep", "variable_write", 3, variable="count"),
            ],
            "wires": [wire("want.out", "both.in1"), wire("level.out", "high.a"), wire("high.out", "both.in2"),
                      wire("both.out", "lights.in"), wire("level.out", "scale.in"), wire("scale.out", "limit.in"),
                      wire("scale.out", "keep.in")]}


@needs_db
async def test_logic_runs_writes_fails_safe_and_versions():
    from setup_helpers import running_stack
    from sqlalchemy import select
    from test_commands import bus_stack, limit_value, pid, wait_for
    from test_commands import site as rs485_site

    from devine_bms.db import models as db

    sim = await bus_stack()
    try:
        async for s in running_stack(rs485_site(sim.port, 5099)):
            c = s["client"]
            sim.units[1].writable[2] = 80  # register 22 (int16): the reading
            r = await c.put("/api/logic-variables/lights_on", json={"type": "bool", "kind": "constant", "value": True})
            assert r.json()["ok"], r.text

            # A draft may be incomplete; checks say why it cannot be published.
            r = (await c.put("/api/logic/lights_logic/draft", json={"dis": "Lights logic", "blocks": [
                block("o", "point_write", point="site_lights1_relay")]})).json()
            assert r["created"] and not r["ok"]
            assert any("not wired" in i["message"] for i in r["issues"])
            refused = await c.post("/api/logic/lights_logic/publish", json={"confirmFailsafe": True})
            assert refused.status_code == 422 and refused.json()["issues"]

            assert (await c.put("/api/logic/lights_logic/draft", json=lights_logic())).json()["ok"]
            unconfirmed = await c.post("/api/logic/lights_logic/publish", json={})
            assert unconfirmed.status_code == 409 and "fail-safe" in unconfirmed.json()["error"]
            r = (await c.post("/api/logic/lights_logic/publish", json={"confirmFailsafe": True, "note": "first"})).json()
            assert r["version"] == 1 and r["state"]["state"] == "running"

            # It runs: the lights come on and the limit is 80 % of 400 = 320, clamped to 200.
            await wait_for(lambda: _true(sim.units[2].coils[3]))
            await wait_for(lambda: _true(abs(limit_value(sim) - 200) < 0.01))
            live = (await c.get("/api/logic/lights_logic/live")).json()
            assert live["state"] == "running" and live["blocks"]["high"]["outputs"]["out"] is True
            assert live["outputs"]["site_lights1_relay"]["state"] == "written"
            assert live["outputs"]["site_meter2_limit"]["written"] == 200

            # A person changes the constant: logic follows.
            await c.put("/api/logic-variables/lights_on", json={"value": False})
            await wait_for(lambda: _true(sim.units[2].coils[3] is False))

            # Hand mode wins over logic; when it ends the point gets logic's value.
            relay = await pid(c, "site_lights1_relay")
            await c.post(f"/api/points/{relay}/command", json={"value": True, "mode": "hand", "hours": 1, "confirm": True})
            await _sleep(1.0)
            assert sim.units[2].coils[3] is True
            assert (await c.get("/api/logic/lights_logic/live")).json()["outputs"]["site_lights1_relay"]["state"] == "hand"
            await c.post(f"/api/points/{relay}/release")
            await wait_for(lambda: _true(sim.units[2].coils[3] is False))

            # A bad input: the output goes to its fail-safe (hold keeps the limit at 200).
            await c.put("/api/logic-variables/lights_on", json={"value": True})
            await wait_for(lambda: _true(sim.units[2].coils[3] is True))

            # One writer per point: a second diagram writing the relay is refused.
            other = {"name": "other", "blocks": [block("o", "point_write", point="site_lights1_relay",
                                                       inputs={"in": True})]}
            await c.put("/api/logic/other/draft", json=other)
            r = await c.post("/api/logic/other/publish", json={"confirmFailsafe": True})
            assert r.status_code == 422 and any("already written by the diagram 'lights_logic'" in i["message"]
                                                for i in r.json()["issues"])

            # Disable: the fail-safes apply (the relay goes off; the limit holds).
            r = (await c.post("/api/logic/lights_logic/enable", json={"enabled": False})).json()
            assert r["enabled"] is False
            await wait_for(lambda: _true(sim.units[2].coils[3] is False))
            assert abs(limit_value(sim) - 200) < 0.01
            await c.post("/api/logic/lights_logic/enable", json={"enabled": True})
            await wait_for(lambda: _true(sim.units[2].coils[3] is True))

            # The memory variable is kept in the database.
            await _sleep(5.5)
            async with s["sessions"]() as session:
                row = await session.scalar(select(db.LogicVariable).where(db.LogicVariable.name == "count"))
            assert row is not None and row.value == {"v": 320.0}

            # Versions: publish a second, restore the first into the draft.
            d2 = lights_logic({"mode": "release"})
            assert (await c.put("/api/logic/lights_logic/draft", json=d2)).json()["ok"]
            assert (await c.post("/api/logic/lights_logic/publish", json={"confirmFailsafe": True})).json()["version"] == 2
            got = (await c.get("/api/logic/lights_logic")).json()
            assert [v["version"] for v in got["versions"]] == [1, 2] and got["publishedVersion"] == 2
            await c.post("/api/logic/lights_logic/restore", json={"version": 1})
            got = (await c.get("/api/logic/lights_logic")).json()
            assert got["draft"]["blocks"][4]["settings"]["failsafe"]["mode"] == "value" and got["draftChanged"]

            # A test run of the draft writes nothing.
            before = sim.units[2].coils[3]
            t = (await c.post("/api/logic/lights_logic/test", json={
                "inputs": {"site_meter2_nolimit": 10}, "steps": 3})).json()
            assert t["ok"] and t["steps"][-1]["outputs"]["site_lights1_relay"] is False
            t = (await c.post("/api/logic/lights_logic/test", json={
                "inputs": {"site_meter2_nolimit": None}, "steps": 1})).json()
            assert t["steps"][0]["outputs"]["site_lights1_relay"] == "fail-safe: value False"
            assert sim.units[2].coils[3] == before

            # Import: errors stop it; a good one becomes a draft; a clash needs replace.
            bad = await c.post("/api/logic/import", json={"diagram": {"name": "imp", "blocks": [block("x", "teapot")]}})
            assert bad.status_code == 422 and "unknown block type" in bad.json()["issues"][0]["message"]
            assert (await c.post("/api/logic/import", json={"diagram": "{not json"})).status_code == 422
            ok = (await c.post("/api/logic/import", json={"diagram": {**lights_logic(), "name": "copy"}})).json()
            assert ok["ok"] and ok["name"] == "copy"
            assert any(i["level"] == "warning" and "already written by the diagram 'lights_logic'" in i["message"]
                       for i in ok["issues"])  # what does not fit the site is a warning on import
            assert (await c.post("/api/logic/import", json={"diagram": {**lights_logic(), "name": "copy"}})).status_code == 409
            listed = {d["name"]: d for d in (await c.get("/api/logic")).json()}
            assert listed["copy"]["state"] == "draft" and listed["lights_logic"]["state"] == "running"

            # Removing the diagram: its outputs fail safe (v2: release; Modbus just stops writing).
            assert (await c.delete("/api/logic/lights_logic")).json()["ok"]
            assert "lights_logic" not in {d["name"] for d in (await c.get("/api/logic")).json()}
            audit = [a["action"] for a in (await c.get("/api/audit", params={"kind": "logic"})).json()]
            for action in ("logic-create", "logic-publish", "logic-disable", "logic-enable", "logic-restore",
                           "logic-import", "logic-delete"):
                assert action in audit, action
    finally:
        await sim.stop()


async def _true(x):
    return bool(x)


async def _sleep(s):
    import asyncio

    await asyncio.sleep(s)


def test_published_schema_matches_the_model():
    import json
    from pathlib import Path

    from devine_bms.logic.model import json_schema

    path = Path(__file__).resolve().parents[2] / "config/schema/devine-bms-logic-1.schema.json"
    assert json.loads(path.read_text()) == json.loads(json.dumps(json_schema())), \
        "run python -m devine_bms.logic.write_schema"
