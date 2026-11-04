"""Device templates and the H1 built-in template (milestone 4a)."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError

from devine_bms.config.validate import parse_config
from devine_bms.runtime import Runtime
from devine_bms.storage.base import MemoryStorage
from devine_bms.templates.device import _main, builtin_templates, expand, load_template
from devine_bms.values import Status
from helpers import TEST_INTERVALS, runtime_config_from
from simulators.modbus_bus import IoModuleModel, ModbusSimulator

def h1():
    return builtin_templates()["cwt-mb328b"]

def test_h1_template_matches_the_manual():
    t = h1()
    assert t.hardware_id == "H1" and t.status == "datasheet only"
    by_key = {p.key: p for p in t.points}
    assert len([k for k in by_key if k.startswith("di")]) == 8
    assert len([k for k in by_key if k.startswith("do")]) == 18
    # DI0-DI7 are discrete inputs 10001-10008 (FC02); DO0-DO17 are coils 00001-00018 (FC01/05/15).
    assert by_key["di0"].address.object_type == "discreteInput" and by_key["di0"].address.address == 0
    assert by_key["di7"].manual_address == "10008"
    assert by_key["do17"].address.object_type == "coil" and by_key["do17"].address.address == 17
    assert by_key["do17"].manual_address == "00018"
    assert by_key["do0"].type == "cmd" and by_key["di0"].type == "sensor"
    assert t.defaults.unit_id == 1 and t.defaults.baudrate == 9600 and t.defaults.tcp_host == "192.168.1.1"
    assert any("slave ID 1" in n for n in t.notes)

def test_expand_gives_valid_config_points():
    points = expand(h1(), "io1", "site_lights1_", equip="lights1", only=["di0", "do0", "do17"])
    assert [p["name"] for p in points] == ["site_lights1_di0", "site_lights1_do0", "site_lights1_do17"]
    data = {
        "schema": "devine-bms-config/0",
        "site": {"name": "bench", "dis": "Bench"},
        "equips": [{"name": "lights1", "dis": "Lights", "tags": {"lighting": True}}],
        "buses": [{"name": "rs485_1", "dis": "Line 1", "type": "modbus_rtu", "port": "/dev/ttyUSB0"}],
        "devices": [{"name": "io1", "dis": "I/O", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 3}}],
        "points": points,
    }
    config, warnings = parse_config(data)
    assert len(config.points) == 3 and warnings == []

def test_expand_checks_its_input():
    with pytest.raises(ValueError):
        expand(h1(), "io1", "x_", equip="lights1", only=["nope"])
    with pytest.raises(ValueError):
        expand(h1(), "io1", "x_")

def test_bad_templates_are_rejected():
    data = json.loads(h1().model_dump_json(by_alias=True))
    dup = copy.deepcopy(data)
    dup["points"][1]["key"] = dup["points"][0]["key"]
    with pytest.raises(ValidationError):
        load_template(dup)
    clash = copy.deepcopy(data)
    clash["points"][1]["address"] = clash["points"][0]["address"]
    with pytest.raises(ValidationError):
        load_template(clash)

def test_cli_list_and_expand(capsys):
    assert _main(["list"]) == 0
    assert "cwt-mb328b" in capsys.readouterr().out
    assert _main(["expand", "cwt-mb328b", "--device", "io1", "--name-prefix", "site_l_", "--equip", "l",
                  "--only", "do1"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["address"] == {
        "objectType": "coil", "address": 1, "dataType": "bit", "byteOrder": "big", "wordOrder": "high_first",
        "scale": 1.0, "offset": 0.0,
    }

async def test_h1_template_reads_the_simulated_io_module():
    io = IoModuleModel()
    io.coils[5] = True
    sim = ModbusSimulator({4: io}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    data = {
        "schema": "devine-bms-config/0",
        "site": {"name": "bench", "dis": "Bench"},
        "equips": [{"name": "lights1", "dis": "Lights", "tags": {"lighting": True}}],
        "buses": [{"name": "gw", "dis": "Gateway", "type": "modbus_rtu_over_tcp", "host": "127.0.0.1",
                   "port": sim.port, "timeoutS": 0.3, "retries": 0}],
        "devices": [{"name": "io1", "dis": "I/O", "protocol": "modbus", "connection": {"bus": "gw", "unitId": 4}}],
        "points": expand(h1(), "io1", "site_lights1_", equip="lights1"),
    }
    runtime = Runtime(MemoryStorage(runtime_config_from(data)), "bench", intervals=TEST_INTERVALS,
                      stale_check_s=0.05, flush_interval_s=0.1)
    await runtime.start()
    try:
        deadline = asyncio.get_running_loop().time() + 6
        while not all(runtime.cache.get(p.id).status == Status.OK for p in runtime.config.points):
            assert asyncio.get_running_loop().time() < deadline, "not all points Good"
            await asyncio.sleep(0.05)
        values = {p.name: runtime.cache.get(p.id).value for p in runtime.config.points}
        assert len(values) == 26
        assert values["site_lights1_do0"] is True and values["site_lights1_do5"] is True
        assert values["site_lights1_do1"] is False
        assert [values[f"site_lights1_di{i}"] for i in range(8)] == io.inputs
        # 8 inputs (fast) and 18 coils (normal) each fit one request.
        assert len(runtime.pollers[0].blocks) == 2
    finally:
        await runtime.stop()
        await sim.stop()

import asyncio  # noqa: E402  (used by the async test above)
