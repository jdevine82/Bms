"""Device template library, bulk create and test reads (milestone 5A).

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import copy
import json
import os

import pytest

from devine_bms.templates.device import builtin_templates
from helpers import example_data
from setup_helpers import running_stack, stack  # noqa: F401 (stack is a fixture)
from simulators.modbus_bus import IoModuleModel, MeterModel, ModbusSimulator

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


def my_template(version="1.0.0"):
    data = json.loads(builtin_templates()["cwt-mb328b"].model_dump_json(by_alias=True))
    data.update(id="my-io-8", version=version, vendor="Bench", model="IO-8", status="bench tested",
                hardwareId=None)
    data["points"] = data["points"][:8]  # the 8 inputs only
    return data


async def test_template_library(stack):
    client = stack["client"]
    listed = (await client.get("/api/setup/templates")).json()
    assert {t["id"] for t in listed} >= {"cwt-mb328b", "mitsubishi-bac-hd150-group"}
    assert all(t["source"] == "builtin" for t in listed)

    saved = (await client.post("/api/setup/templates", json=my_template())).json()
    assert saved == {"ok": True, "applied": True, "id": "my-io-8", "version": "1.0.0"}
    again = await client.post("/api/setup/templates", json=my_template())
    assert again.status_code == 422 and "give it a new version" in again.json()["error"]
    assert (await client.post("/api/setup/templates", json=my_template("1.1.0"))).json()["applied"]
    got = (await client.get("/api/setup/templates/my-io-8")).json()
    assert got["source"] == "site" and got["versions"] == ["1.0.0", "1.1.0"]
    assert got["template"]["version"] == "1.1.0"

    builtin = json.loads(builtin_templates()["cwt-mb328b"].model_dump_json(by_alias=True))
    refused = await client.post("/api/setup/templates", json=builtin)
    assert refused.status_code == 422 and "built-in" in refused.json()["error"]
    broken = my_template("2.0.0")
    broken["points"][1]["address"] = broken["points"][0]["address"]
    assert (await client.post("/api/setup/templates", json=broken)).status_code == 422

    assert (await client.delete("/api/setup/templates/my-io-8?version=1.0.0")).json()["applied"]
    assert (await client.get("/api/setup/templates/my-io-8")).json()["versions"] == ["1.1.0"]
    log = (await client.get("/api/setup/changes")).json()
    assert any("saved device template my-io-8 version 1.1.0" == c["summary"] for c in log)


async def rtu_site():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    data = example_data(5020)
    data["buses"] = [{"name": "rs485_1", "dis": "Line 1", "type": "modbus_rtu_over_tcp", "host": "127.0.0.1",
                      "port": sim.port, "timeoutS": 0.3, "retries": 0}]
    data["equips"].append({"name": "lights1", "dis": "Lights", "tags": {"lighting": True}})
    data["devices"].append({"name": "io1", "dis": "I/O", "protocol": "modbus",
                            "connection": {"bus": "rs485_1", "unitId": 2}})
    return sim, data


async def test_apply_template_and_test_read_through_the_poller():
    sim, data = await rtu_site()
    try:
        async for s in running_stack(data):
            client = s["client"]
            applied = (await client.post("/api/setup/templates/cwt-mb328b/apply", json={
                "device": "io1", "namePrefix": "site_lights1_", "equip": "lights1", "only": ["di0", "do0", "do1"],
            })).json()
            assert applied["applied"] and applied["report"]["created"] == {"point": 3}
            names = [p["name"] for p in (await client.get("/api/setup/points")).json()]
            assert {"site_lights1_di0", "site_lights1_do0", "site_lights1_do1"} <= set(names)

            read = (await client.post("/api/setup/test-read", json={"device": "io1"})).json()
            assert read["ok"] and {r["point"]: r["value"] for r in read["results"]}["site_lights1_do0"] is True
            coil_request = next(r for r in read["requests"] if "function 01" in r["request"])
            assert coil_request["error"] is None and coil_request["ms"] >= 0
            sent = coil_request["frames"][0]
            assert sent["direction"] == "sent" and sent["hex"].startswith("02 01 00 00 00 02")  # unit 2, FC01
            assert len(coil_request["frames"]) == 2

            one = (await client.post("/api/setup/test-read",
                                     json={"device": "io1", "points": ["site_lights1_di0"]})).json()
            assert [r["point"] for r in one["results"]] == ["site_lights1_di0"]

            sim.unit_modes[2] = "down"
            silent = (await client.post("/api/setup/test-read", json={"device": "io1"})).json()
            assert all(r["status"] == "down" for r in silent["results"])
            assert "no answer" in silent["requests"][0]["error"]
    finally:
        await sim.stop()


async def test_bulk_create_with_repeat_creates_equipment():
    sim, data = await rtu_site()
    try:
        async for s in running_stack(data):
            client = s["client"]
            result = (await client.post("/api/setup/templates/cwt-mb328b/apply", json={
                "device": "io1", "namePrefix": "site_zone{n}_", "equip": "zone{n}", "equipDis": "Zone {n} lights",
                "equipTags": {"lighting": True}, "only": ["do0"], "repeat": {"from": 1, "to": 3, "step": 1},
            })).json()
            assert result["applied"] and result["report"]["created"] == {"equip": 3, "point": 3}
            points = {p["name"]: p for p in (await client.get("/api/setup/points")).json()}
            assert points["site_zone3_do0"]["address"]["address"] == 3 and points["site_zone3_do0"]["equip"] == "zone3"
            equips = {e["name"]: e for e in (await client.get("/api/setup/equips")).json()}
            assert equips["zone2"]["dis"] == "Zone 2 lights"
            clash = await client.post("/api/setup/templates/cwt-mb328b/apply", json={
                "device": "io1", "namePrefix": "site_zone{n}_", "equip": "zone{n}", "only": ["do0"],
                "repeat": {"from": 1, "to": 1}})
            assert clash.status_code == 422 and "already exist" in clash.json()["error"]
    finally:
        await sim.stop()


async def test_test_read_a_device_that_is_not_saved(stack):
    client, sim = stack["client"], stack["sim"]
    point = (await client.get("/api/setup/points/site_meter1_power")).json()
    new_point = {**copy.deepcopy(point), "name": "site_meter1_power_test", "device": "spare_meter"}
    read = (await client.post("/api/setup/test-read", json={"config": {
        "device": {"name": "spare_meter", "dis": "Spare", "protocol": "modbus_tcp",
                   "connection": {"host": "127.0.0.1", "port": sim.port, "timeoutS": 1, "retries": 0}},
        "points": [new_point]}})).json()
    assert read["ok"] and read["results"][0]["status"] == "ok" and 5 < read["results"][0]["value"] < 19
    assert (await client.get("/api/setup/devices/spare_meter")).status_code == 404  # nothing was saved

    # Points of equipment that is not saved yet (the wizard's draft) can be test read too.
    drafted = (await client.post("/api/setup/test-read", json={"config": {
        "device": {"name": "spare_meter", "dis": "Spare", "protocol": "modbus_tcp",
                   "connection": {"host": "127.0.0.1", "port": sim.port, "timeoutS": 1, "retries": 0}},
        "points": [{**new_point, "equip": "not_saved_yet", "space": "nor_this"}]}})).json()
    assert drafted["ok"] and drafted["results"][0]["status"] == "ok"

    bad = await client.post("/api/setup/test-read", json={"config": {
        "device": {"name": "spare", "dis": "Spare", "protocol": "modbus_tcp", "connection": {"host": "127.0.0.1"}},
        "points": [{**new_point, "device": "nope"}]}})
    assert bad.status_code == 422


async def test_test_read_is_local_only(stack):
    import httpx

    remote = httpx.AsyncClient(transport=httpx.ASGITransport(app=stack["app"], client=("10.0.0.9", 1)),
                               base_url="http://bms")
    async with remote:
        assert (await remote.post("/api/setup/test-read", json={"device": "sim_meter"})).status_code == 403
