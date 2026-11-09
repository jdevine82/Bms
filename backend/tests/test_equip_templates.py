"""Equipment templates (main spec 5.1.2, milestone 5A): instances, overrides, roll-out, unlink.

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import copy
import os

import pytest

from devine_bms.templates.equip import builtin_equip_templates
from helpers import example_data
from setup_helpers import running_stack, stack  # noqa: F401 (stack is a fixture)

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


def test_built_in_starter_templates():
    ids = set(builtin_equip_templates())
    assert ids == {"indoor-unit", "ahu", "room", "pump", "fan", "exhaust-fan", "erv", "light-circuit", "power-meter"}
    assert builtin_equip_templates()["room"].applies_to == "space"
    assert builtin_equip_templates()["erv"].tags == {"heatExchanger": True}  # Appendix A.2


def gateway_site() -> dict:
    data = example_data(5020)
    data["bacnet"] = {"localAddress": "127.0.0.1/8", "port": 47997, "deviceInstance": 3999001}
    data["spaces"] = [{"name": "office", "dis": "Office"}]
    data["devices"].append({"name": "gw1", "dis": "Mitsubishi gateway", "protocol": "bacnet_ip",
                            "connection": {"address": "127.0.0.1:47999", "deviceInstance": 2001}})
    return data


async def test_bulk_create_indoor_units_from_the_gateway_template():
    async for s in running_stack(gateway_site()):
        client = s["client"]
        result = (await client.post("/api/setup/equip-templates/indoor-unit/create", json={
            "name": "ac{n:02}", "dis": "AC {n}", "space": "office",
            "fromDeviceTemplate": {"template": "mitsubishi-bac-hd150-group", "device": "gw1"},
            "repeat": {"from": 1, "to": 3, "step": 100},
        })).json()
        assert result["applied"], result
        # 12 points each, plus each unit's two maintenance counters on the BMS counters device (5.10).
        assert result["report"]["created"] == {"equip": 3, "point": 42, "device": 1}
        assert result["warnings"] == []  # every required role is bound
        points = {p["name"]: p for p in (await client.get("/api/setup/points")).json()}
        temp = points["office_ac02_room_temp"]
        assert temp["address"] == {"objectType": "analogInput", "instance": 10209, "property": "presentValue"}
        assert temp["tags"] == {"zone": True, "air": True, "temp": True} and temp["equip"] == "ac02"
        equip = (await client.get("/api/setup/equips/ac03")).json()
        assert equip["tags"] == {"fcu": True} and equip["template"]["id"] == "indoor-unit"
        assert equip["template"]["bindings"]["setpoint"] == "office_ac03_setpoint"
        listed = {t["id"]: t for t in (await client.get("/api/setup/equip-templates")).json()}
        assert listed["indoor-unit"]["instances"] == 3

        # A hand change to a bound point shows as an override.
        point = points["office_ac01_room_temp"]
        point["dis"] = "Meeting room temperature"
        await client.put("/api/setup/points/office_ac01_room_temp", json=point)
        instance = (await client.get("/api/setup/instances/equips/ac01")).json()
        room_temp = next(r for r in instance["roles"] if r["role"] == "room_temp")
        assert room_temp["overrides"] == {"dis": {"template": "Room temperature", "point": "Meeting room temperature"}}
        reset = (await client.post("/api/setup/instances/equips/ac01/reset",
                                   json={"role": "room_temp", "field": "dis"})).json()
        assert reset["applied"]
        assert (await client.get("/api/setup/points/office_ac01_room_temp")).json()["dis"] == "Room temperature"


def my_meter(version: str, power_dis: str, energy_dis: str, voltage: bool = False) -> dict:
    roles = [
        {"role": "power", "dis": power_dis, "type": "sensor", "kind": "Number", "unit": "kW",
         "tags": {"elec": True, "power": True}, "pollClass": "fast"},
        {"role": "energy", "dis": energy_dis, "type": "sensor", "kind": "Number", "unit": "kWh",
         "tags": {"elec": True, "energy": True}, "pollClass": "slow"},
    ]
    if voltage:
        roles.append({"role": "voltage", "dis": "Voltage", "type": "sensor", "kind": "Number", "unit": "V",
                      "tags": {"elec": True, "volt": True}})
    return {"id": "my-meter", "version": version, "dis": "Bench meter", "tags": {"ac": True, "elec": True,
            "meter": True}, "roles": roles}


async def test_versions_rollout_and_overrides(stack):
    client = stack["client"]
    assert (await client.post("/api/setup/equip-templates", json=my_meter("1.0.0", "Power", "Energy"))).json()["applied"]
    builtin_copy = copy.deepcopy(my_meter("1.0.0", "x", "y"))
    builtin_copy["id"] = "ahu"
    assert (await client.post("/api/setup/equip-templates", json=builtin_copy)).status_code == 422
    dup = await client.post("/api/setup/equip-templates", json=my_meter("1.0.0", "Power", "Energy"))
    assert dup.status_code == 422 and "new version" in dup.json()["error"]

    made = (await client.post("/api/setup/equip-templates/my-meter/create", json={
        "name": "meter2", "dis": "Second meter",
        "bindings": {
            "power": {"device": "sim_meter", "address": {"objectType": "holdingRegister", "address": 0,
                                                         "dataType": "float32"}},
            "energy": {"device": "sim_meter", "address": {"objectType": "holdingRegister", "address": 8,
                                                          "dataType": "uint32", "wordOrder": "low_first",
                                                          "scale": 0.1}},
        }})).json()
    assert made["applied"] and made["report"]["created"] == {"equip": 1, "point": 2}

    energy = (await client.get("/api/setup/points/site_meter2_energy")).json()
    energy["dis"] = "Energy (main board)"  # an override
    await client.put("/api/setup/points/site_meter2_energy", json=energy)

    v2 = my_meter("2.0.0", "Active power", "Total energy", voltage=True)
    assert (await client.post("/api/setup/equip-templates", json=v2)).json()["applied"]
    difference = (await client.get("/api/setup/equip-templates/my-meter/diff?from=1.0.0&to=2.0.0")).json()
    assert difference["added"] == ["voltage"] and set(difference["changed"]) == {"power", "energy"}

    preview = (await client.post("/api/setup/equip-templates/my-meter/rollout?check=true",
                                 json={"version": "2.0.0", "names": ["meter2"]})).json()
    assert preview["applied"] is False
    meter2 = preview["instances"][0]
    assert {"point": "site_meter2_power", "field": "dis", "old": "Power", "new": "Active power"} in meter2["changes"]
    kept = next(c for c in meter2["changes"] if c["point"] == "site_meter2_energy")
    assert kept["note"] == "override kept" and kept["kept"] == "Energy (main board)"
    assert meter2["needsBinding"] == ["voltage"]
    assert (await client.get("/api/setup/points/site_meter2_power")).json()["dis"] == "Power"  # only a preview

    rolled = (await client.post("/api/setup/equip-templates/my-meter/rollout",
                                json={"version": "2.0.0", "all": True})).json()
    assert rolled["applied"]
    assert any("required roles not bound yet: ['voltage']" in w["message"] for w in rolled["warnings"])
    assert (await client.get("/api/setup/points/site_meter2_power")).json()["dis"] == "Active power"
    assert (await client.get("/api/setup/points/site_meter2_energy")).json()["dis"] == "Energy (main board)"
    assert (await client.get("/api/setup/equips/meter2")).json()["template"]["version"] == "2.0.0"

    in_use = await client.delete("/api/setup/equip-templates/my-meter")
    assert in_use.status_code == 422 and "still used" in in_use.json()["error"]
    assert (await client.post("/api/setup/instances/equips/meter2/unlink")).json()["applied"]
    assert "template" not in (await client.get("/api/setup/equips/meter2")).json()
    assert (await client.delete("/api/setup/equip-templates/my-meter")).json()["applied"]


async def test_room_template_and_binding_checks(stack):
    client = stack["client"]
    room = (await client.post("/api/setup/equip-templates/room/create", json={
        "name": "meeting", "dis": "Meeting room",
        "bindings": {"room_temp": {"device": "sim_meter", "address": {
            "objectType": "holdingRegister", "address": 2, "dataType": "float32"}}}})).json()
    assert room["applied"] and room["report"]["created"] == {"space": 1, "point": 1}
    point = (await client.get("/api/setup/points/meeting_room_temp")).json()
    assert point["space"] == "meeting" and "equip" not in point

    data = (await client.get("/api/setup/config")).json()
    space = next(s for s in data["spaces"] if s["name"] == "meeting")
    space["template"]["bindings"]["humidity"] = "site_meter1_power"  # a point of other equipment
    refused = await client.put("/api/setup/config", json=data)
    assert refused.status_code == 422
    assert any("belongs to another space" in i["message"] for i in refused.json()["issues"])
