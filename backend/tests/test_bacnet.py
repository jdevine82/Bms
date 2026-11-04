"""BACnet/IP reads, quality, ReadProperty fallback and COV (milestone 4b), against a simulated device."""

import asyncio
import copy
import socket

import pytest

from devine_bms.config.model import PollClass
from devine_bms.config.validate import ConfigError, parse_config
from devine_bms.runtime import Runtime
from devine_bms.storage.base import MemoryStorage
from devine_bms.values import Status
from helpers import TEST_INTERVALS, runtime_config_from
from simulators.bacnet_device import BacnetSimulator


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def point(name, dis, obj, instance, kind, poll="fast", type_="sensor", **extra):
    return {"name": f"site_ahu1_{name}", "dis": dis, "equip": "ahu1", "device": "ctrl", "type": type_,
            "kind": kind, "pollClass": poll, "address": {"objectType": obj, "instance": instance}, **extra}


def config_for(sim: BacnetSimulator, cov: bool = False) -> dict:
    return {
        "schema": "devine-bms-config/0",
        "site": {"name": "bench", "dis": "Bench"},
        "bacnet": {"localAddress": "127.0.0.1/8", "port": free_udp_port(), "deviceInstance": 3999001},
        "equips": [{"name": "ahu1", "dis": "AHU 1", "tags": {"ahu": True}}],
        "devices": [{"name": "ctrl", "dis": "Controller", "protocol": "bacnet_ip",
                     "connection": {"address": sim.endpoint, "deviceInstance": sim.instance, "timeoutS": 0.5,
                                    "retries": 0, "cov": cov}}],
        "points": [
            point("room_temp", "Room temperature", "analogInput", 1, "Number", unit="°C",
                  tags={"zone": True, "air": True, "temp": True}),
            point("sp", "Setpoint", "analogValue", 1, "Number", poll="normal", type_="sp", unit="°C"),
            point("fan_status", "Fan status", "binaryInput", 1, "Bool", tags={"fan": True, "run": True}),
            point("fan_cmd", "Fan command", "binaryOutput", 1, "Bool", poll="normal", type_="cmd",
                  tags={"fan": True, "run": True}),
            point("mode", "Mode", "multiStateValue", 1, "Str", poll="normal", type_="sp",
                  enum=["Cool", "Heat", "Fan"], tags={"hvacMode": True}),
            point("missing", "Missing object", "analogInput", 9, "Number", poll="slow"),
        ],
    }


@pytest.fixture
async def sim():
    simulator = BacnetSimulator(port=free_udp_port(), update_interval_s=0.1)
    await simulator.start()
    yield simulator
    await simulator.stop()


async def start(data, intervals=TEST_INTERVALS):
    runtime = Runtime(MemoryStorage(runtime_config_from(data)), "bench", intervals=intervals,
                      stale_check_s=0.05, flush_interval_s=0.1)
    await runtime.start()
    return runtime


def live(runtime, short):
    p = next(p for p in runtime.config.points if p.name == f"site_ahu1_{short}")
    return runtime.cache.get(p.id)


async def wait_for(condition, timeout=6.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


GOOD = ["room_temp", "sp", "fan_status", "fan_cmd", "mode"]


def all_good(runtime):
    return all(live(runtime, n).status == Status.OK for n in GOOD)


async def test_reads_every_object_type(sim):
    runtime = await start(config_for(sim))
    try:
        await wait_for(lambda: all_good(runtime))
        assert 19.9 < live(runtime, "room_temp").value < 23.1
        assert live(runtime, "sp").value == 21.0
        assert live(runtime, "fan_status").value is True
        assert live(runtime, "fan_cmd").value is True
        assert live(runtime, "mode").value == "Heat"  # state 2 of Cool, Heat, Fan
        await wait_for(lambda: live(runtime, "missing").status == Status.FAULT)
        assert "unknown-object" in live(runtime, "missing").reason
        assert runtime.pollers[0].online is True
    finally:
        await runtime.stop()


async def test_fault_and_out_of_service_flags(sim):
    runtime = await start(config_for(sim))
    try:
        await wait_for(lambda: all_good(runtime))
        sim.set_fault(sim.room_temp)
        await wait_for(lambda: live(runtime, "room_temp").status == Status.FAULT)
        assert "reports a fault" in live(runtime, "room_temp").reason
        sim.set_fault(sim.room_temp, False)
        await wait_for(lambda: live(runtime, "room_temp").status == Status.OK)

        sim.set_out_of_service(sim.setpoint)
        await wait_for(lambda: live(runtime, "sp").status == Status.FAULT)
        assert "out of service" in live(runtime, "sp").reason
        assert live(runtime, "room_temp").status == Status.OK
    finally:
        await runtime.stop()


async def test_device_gone_and_back(sim):
    runtime = await start(config_for(sim))
    try:
        await wait_for(lambda: all_good(runtime))
        await sim.stop()
        await wait_for(lambda: all(live(runtime, n).status == Status.DOWN for n in GOOD))
        assert "no answer" in live(runtime, "sp").reason
        again = BacnetSimulator(port=sim.port, update_interval_s=0.1)
        await again.start()
        try:
            await wait_for(lambda: all_good(runtime), timeout=10)
        finally:
            await again.stop()
    finally:
        await runtime.stop()


async def test_falls_back_to_read_property():
    simulator = BacnetSimulator(port=free_udp_port(), update_interval_s=0.1, no_rpm=True)
    await simulator.start()
    runtime = await start(config_for(simulator))
    try:
        await wait_for(lambda: all_good(runtime))
        assert live(runtime, "mode").value == "Heat"
        assert runtime.buses[0].driver.use_rpm is False
        await wait_for(lambda: live(runtime, "missing").status == Status.FAULT)
    finally:
        await runtime.stop()
        await simulator.stop()


async def test_cov_updates_without_waiting_for_a_poll(sim):
    # Polls are slow here, so only a COV notification can bring the new setpoint in time.
    slow = {PollClass.FAST: 30.0, PollClass.NORMAL: 30.0, PollClass.SLOW: 60.0}
    runtime = await start(config_for(sim, cov=True), intervals=slow)
    try:
        await wait_for(lambda: all_good(runtime))
        cov = runtime.cov[0]
        await wait_for(lambda: "site_ahu1_sp" in cov.state.subscribed)
        sim.setpoint.presentValue = 23.5
        await wait_for(lambda: live(runtime, "sp").value == 23.5, timeout=3)
        sp = next(p for p in runtime.config.points if p.name == "site_ahu1_sp")
        assert runtime.tracker.stale_overrides[sp.id] == 180.0  # 3 x Slow while COV keeps it current
        # The missing object cannot be subscribed to; it is Slow class here, so COV never tries it.
        assert "site_ahu1_missing" not in cov.state.subscribed
    finally:
        await runtime.stop()


async def test_cov_failure_falls_back_to_polling(sim):
    data = config_for(sim, cov=True)
    data["points"][-1]["pollClass"] = "fast"  # the missing object now gets a (failing) COV subscription
    runtime = await start(data)
    try:
        cov = runtime.cov[0]
        await wait_for(lambda: "site_ahu1_missing" in cov.state.failed)
        missing = next(p for p in runtime.config.points if p.name == "site_ahu1_missing")
        assert missing.id not in runtime.buses[0].driver.cov_active
        assert missing.id not in runtime.tracker.stale_overrides
        await wait_for(lambda: live(runtime, "missing").status == Status.FAULT)
    finally:
        await runtime.stop()


def base_config():
    # Config checks only need the address and instance; bacpypes3 objects need an event loop.
    from types import SimpleNamespace

    return config_for(SimpleNamespace(endpoint="127.0.0.1:47900", instance=1001))


def errors_of(data):
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    return str(exc.value)


def test_config_needs_bacnet_settings():
    data = base_config()
    del data["bacnet"]
    assert "need the site's 'bacnet' settings" in errors_of(data)


def test_device_instance_must_be_unique():
    data = base_config()
    data["devices"][0]["connection"]["deviceInstance"] = 3999001
    assert "already used by the BMS itself" in errors_of(data)


def test_bacnet_point_rules():
    data = base_config()
    data["points"][0]["kind"] = "Bool"
    assert "analogInput objects are Number points" in errors_of(data)
    data = base_config()
    data["points"][0]["type"] = "sp"
    assert "read-only" in errors_of(data)
    data = base_config()
    data["points"][0]["address"] = {"objectType": "holdingRegister", "address": 0, "dataType": "float32"}
    assert "needs a BACnet address" in errors_of(data)


def test_valid_config_round_trip():
    config, warnings = parse_config(copy.deepcopy(base_config()))
    assert config.bacnet.device_instance == 3999001 and len(config.points) == 6


async def test_bench_tools_whois_and_read(sim):
    from devine_bms.config.model import BacnetSettings
    from devine_bms.drivers.bacnet.link import BacnetLink
    from devine_bms.drivers.bacnet.tools import read, whois

    link = BacnetLink(BacnetSettings(localAddress="127.0.0.1/8", port=free_udp_port(), deviceInstance=4194301))
    await link.start()
    try:
        found = await whois(link, sim.endpoint, None, None, 2.0)
        assert [d["instance"] for d in found] == [1001]
        assert found[0]["address"] == sim.endpoint
        assert await read(link, sim.endpoint, "multi-state-value,1", "present-value", 2.0) == 2
    finally:
        link.close()
