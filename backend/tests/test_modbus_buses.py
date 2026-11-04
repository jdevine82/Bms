"""Modbus RTU lines and serial gateways (milestone 4a).

The simulator holds a meter (unit 1) and an H1-style I/O module (unit 2) on one bus.
Serial lines are tested through pyserial's socket:// ports and, when socat is
installed, a real pseudo-terminal.
"""

import asyncio
import copy
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from devine_bms.config.validate import ConfigError, parse_config
from devine_bms.runtime import Runtime
from devine_bms.storage.base import MemoryStorage
from devine_bms.values import Status
from helpers import TEST_INTERVALS, runtime_config_from
from simulators.modbus_bus import IoModuleModel, MeterModel, ModbusSimulator

BASE = {
    "schema": "devine-bms-config/0",
    "site": {"name": "bench", "dis": "Bench"},
    "equips": [
        {"name": "meter1", "dis": "Meter", "tags": {"ac": True, "elec": True, "meter": True}},
        {"name": "lights1", "dis": "Lights", "tags": {"lighting": True}},
    ],
    "devices": [
        {"name": "meter", "dis": "Meter", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 1}},
        {"name": "io", "dis": "I/O module", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 2}},
    ],
    "points": [
        {"name": "site_meter1_power", "dis": "Power", "equip": "meter1", "device": "meter", "type": "sensor",
         "kind": "Number", "unit": "kW", "tags": {"elec": True, "power": True}, "pollClass": "fast",
         "address": {"objectType": "holdingRegister", "address": 0, "dataType": "float32"}},
        {"name": "site_meter1_energy", "dis": "Energy", "equip": "meter1", "device": "meter", "type": "sensor",
         "kind": "Number", "unit": "kWh", "tags": {"elec": True, "energy": True}, "pollClass": "slow",
         "address": {"objectType": "holdingRegister", "address": 8, "dataType": "uint32",
                     "wordOrder": "low_first", "scale": 0.1}},
        {"name": "site_lights1_di0", "dis": "Input 0", "equip": "lights1", "device": "io", "type": "sensor",
         "kind": "Bool", "tags": {"run": True}, "pollClass": "fast",
         "address": {"objectType": "discreteInput", "address": 0, "dataType": "bit"}},
        {"name": "site_lights1_do0", "dis": "Relay 0", "equip": "lights1", "device": "io", "type": "cmd",
         "kind": "Bool", "tags": {"run": True}, "pollClass": "normal",
         "address": {"objectType": "coil", "address": 0, "dataType": "bit"}},
    ],
}


def config_with(bus: dict) -> dict:
    data = copy.deepcopy(BASE)
    data["buses"] = [{"name": "rs485_1", "dis": "RS485 line 1", "timeoutS": 0.3, "retries": 0, **bus}]
    return data


@pytest.fixture
async def rtu_sim():
    sim = ModbusSimulator({1: MeterModel(seed=1), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    yield sim
    await sim.stop()


async def run_bus(data: dict):
    runtime = Runtime(MemoryStorage(runtime_config_from(data)), "bench", intervals=TEST_INTERVALS,
                      stale_check_s=0.05, flush_interval_s=0.1)
    await runtime.start()
    return runtime


def status_of(runtime, name):
    point = next(p for p in runtime.config.points if p.name == name)
    return runtime.cache.get(point.id)


async def wait_for(condition, timeout=6.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def all_good(runtime):
    return all(runtime.cache.get(p.id).status == Status.OK for p in runtime.config.points)


async def check_bus_reads(runtime, sim):
    await wait_for(lambda: all_good(runtime))
    assert 5 < status_of(runtime, "site_meter1_power").value < 19
    assert status_of(runtime, "site_meter1_energy").value == pytest.approx(sim.units[1].energy_kwh, abs=0.2)
    assert status_of(runtime, "site_lights1_do0").value is True  # the simulator starts with DO0 on
    assert isinstance(status_of(runtime, "site_lights1_di0").value, bool)


async def test_rtu_over_tcp_gateway_reads_two_devices(rtu_sim):
    runtime = await run_bus(config_with({"type": "modbus_rtu_over_tcp", "host": "127.0.0.1", "port": rtu_sim.port}))
    try:
        await check_bus_reads(runtime, rtu_sim)
        assert len(runtime.buses) == 1 and len(runtime.pollers) == 2
    finally:
        await runtime.stop()


async def test_serial_line_through_socket_port(rtu_sim):
    bus = {"type": "modbus_rtu", "port": f"socket://127.0.0.1:{rtu_sim.port}", "baudrate": 19200}
    runtime = await run_bus(config_with(bus))
    try:
        await check_bus_reads(runtime, rtu_sim)
    finally:
        await runtime.stop()


@pytest.mark.skipif(shutil.which("socat") is None, reason="socat is not installed")
async def test_serial_line_through_a_real_pseudo_terminal(rtu_sim):
    """pyserial opens an actual tty, which socat joins to the simulator."""
    folder = tempfile.mkdtemp(prefix="bms-tty-")
    tty = Path(folder) / "ttyBMS0"
    socat = subprocess.Popen(
        ["socat", f"pty,raw,echo=0,link={tty}", f"tcp:127.0.0.1:{rtu_sim.port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        await wait_for(tty.exists, timeout=5)
        runtime = await run_bus(config_with({"type": "modbus_rtu", "port": str(tty), "baudrate": 9600}))
        try:
            await check_bus_reads(runtime, rtu_sim)
        finally:
            await runtime.stop()
    finally:
        socat.terminate()
        socat.wait(5)
        shutil.rmtree(folder, ignore_errors=True)


async def test_one_silent_device_does_not_stop_the_others(rtu_sim):
    runtime = await run_bus(config_with({"type": "modbus_rtu_over_tcp", "host": "127.0.0.1", "port": rtu_sim.port}))
    try:
        await wait_for(lambda: all_good(runtime))
        rtu_sim.unit_modes[2] = "down"
        await wait_for(lambda: status_of(runtime, "site_lights1_di0").status == Status.DOWN)
        assert status_of(runtime, "site_lights1_do0").status == Status.DOWN  # all of that device's points
        before = rtu_sim.requests_served
        await asyncio.sleep(0.6)
        assert status_of(runtime, "site_meter1_power").status == Status.OK
        assert rtu_sim.requests_served > before  # the meter is still being polled
        meter, io = runtime.pollers
        assert meter.online is True and io.online is False and runtime.buses[0].online is True

        rtu_sim.unit_modes[2] = "normal"
        await wait_for(lambda: all_good(runtime), timeout=8)
    finally:
        await runtime.stop()


async def test_bus_down_marks_every_device_down(rtu_sim):
    runtime = await run_bus(config_with({"type": "modbus_rtu_over_tcp", "host": "127.0.0.1", "port": rtu_sim.port}))
    try:
        await wait_for(lambda: all_good(runtime))
        port = rtu_sim.port
        await rtu_sim.stop()
        await wait_for(lambda: all(runtime.cache.get(p.id).status == Status.DOWN for p in runtime.config.points))
        assert runtime.buses[0].online is False
        assert "connect" in status_of(runtime, "site_meter1_energy").reason or "lost" in status_of(
            runtime, "site_meter1_energy").reason
        again = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=port, framing="rtu",
                                update_interval_s=0.05)
        await again.start()
        try:
            await wait_for(lambda: all_good(runtime), timeout=10)
        finally:
            await again.stop()
    finally:
        await runtime.stop()


async def test_tcp_gateway_target_not_answering_is_down_not_fault():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="tcp", gateway=True,
                          update_interval_s=0.05)
    await sim.start()
    runtime = await run_bus(config_with({"type": "modbus_tcp_gateway", "host": "127.0.0.1", "port": sim.port}))
    try:
        await wait_for(lambda: all_good(runtime))
        sim.unit_modes[2] = "down"  # the gateway answers with exception 11
        await wait_for(lambda: status_of(runtime, "site_lights1_di0").status == Status.DOWN)
        assert "exception 11" in status_of(runtime, "site_lights1_di0").reason
        assert status_of(runtime, "site_meter1_power").status == Status.OK
    finally:
        await runtime.stop()
        await sim.stop()


def test_bad_crc_is_ignored_by_the_simulator():
    from simulators.modbus_bus import crc16, with_crc

    frame = with_crc(bytes([1, 3, 0, 0, 0, 2]))
    assert crc16(frame[:6]).to_bytes(2, "little") == frame[6:]
    assert crc16(b"123456789") == 0x4B37  # the standard Modbus CRC check value


def test_duplicate_unit_id_on_a_bus_is_an_error():
    data = config_with({"type": "modbus_rtu", "port": "/dev/ttyUSB0"})
    data["devices"][1]["connection"]["unitId"] = 1
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    assert "unit ID 1 on bus 'rs485_1' is already used by device 'meter'" in str(exc.value)


def test_a_serial_port_is_never_shared():
    data = config_with({"type": "modbus_rtu", "port": "/dev/ttyUSB0"})
    data["buses"].append({"name": "rs485_2", "dis": "Line 2", "type": "modbus_rtu", "port": "/dev/ttyUSB0"})
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    assert "already used by bus 'rs485_1'" in str(exc.value)


def test_device_needs_an_existing_bus_and_matching_connection():
    data = config_with({"type": "modbus_rtu", "port": "/dev/ttyUSB0"})
    data["devices"][0]["connection"]["bus"] = "nope"
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    assert "bus 'nope' does not exist" in str(exc.value)

    data = config_with({"type": "modbus_rtu", "port": "/dev/ttyUSB0"})
    data["devices"][0]["protocol"] = "modbus_tcp"
    with pytest.raises(ConfigError):
        parse_config(data)


def test_unit_id_range():
    data = config_with({"type": "modbus_rtu", "port": "/dev/ttyUSB0"})
    data["devices"][0]["connection"]["unitId"] = 0  # broadcast address, never a device
    with pytest.raises(ConfigError):
        parse_config(data)


def test_example_json_round_trips():
    data = config_with({"type": "modbus_rtu", "port": "/dev/ttyUSB0", "parity": "E", "stopBits": 1})
    config, _ = parse_config(json.loads(json.dumps(data)))
    assert config.buses[0].parity == "E"
