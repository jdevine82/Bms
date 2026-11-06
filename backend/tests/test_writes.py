"""Writing to devices on every protocol (milestone 7A): Modbus coils and registers, BACnet/IP
with priorities and release, MS/TP through the service, and MQTT command topics.

These run the runtime in memory, like the protocol tests of milestone 4.
"""

import asyncio
import json
import os
import shutil
import struct

import aiomqtt
import pytest

import test_bacnet as tb
import test_mqtt as tq
import test_mstp as tm
from devine_bms.commands.writer import MANUAL_PRIORITY, SCHEDULE_PRIORITY, WriteError, write_point
from devine_bms.runtime import Runtime
from devine_bms.storage.base import MemoryStorage
from devine_bms.values import Status
from helpers import TEST_INTERVALS, runtime_config_from
from simulators.modbus_bus import IoModuleModel, MeterModel, ModbusSimulator


def find(runtime, name):
    return next(p for p in runtime.config.points if p.name == name)


async def wait_for(condition, timeout=6.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


# ---------- Modbus ----------

def modbus_site(port: int) -> dict:
    def pt(name, obj, address, kind, type_, **extra):
        return {"name": f"plant_io1_{name}", "dis": name, "equip": "io1", "device": "io1", "type": type_,
                "kind": kind, "pollClass": "fast", "address": {"objectType": obj, "address": address, **extra.pop("a", {})},
                **extra}
    return {
        "schema": "devine-bms-config/0", "site": {"name": "bench", "dis": "Bench"},
        "equips": [{"name": "io1", "dis": "I/O", "tags": {"lighting": True}},
                   {"name": "meter1", "dis": "Meter", "tags": {"elec": True, "meter": True}}],
        "buses": [{"name": "rs485_1", "dis": "Line 1", "type": "modbus_rtu_over_tcp", "host": "127.0.0.1",
                   "port": port, "timeoutS": 0.5, "retries": 0}],
        "devices": [{"name": "io1", "dis": "I/O", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 2}},
                    {"name": "meter", "dis": "Meter", "protocol": "modbus", "connection": {"bus": "rs485_1", "unitId": 1}}],
        "points": [
            pt("do3", "coil", 3, "Bool", "cmd", a={"dataType": "bit"}),
            {"name": "plant_meter1_limit", "dis": "Demand limit", "equip": "meter1", "device": "meter", "type": "sp",
             "kind": "Number", "unit": "kW", "pollClass": "fast", "writeLimits": {"min": 0, "max": 500},
             "address": {"objectType": "holdingRegister", "address": 20, "dataType": "float32"}},
            {"name": "plant_meter1_steps", "dis": "Steps", "equip": "meter1", "device": "meter", "type": "sp",
             "kind": "Number", "pollClass": "fast", "writeLimits": {"min": 0, "max": 100},
             "address": {"objectType": "holdingRegister", "address": 22, "dataType": "int16", "scale": 0.1}},
            {"name": "plant_meter1_power", "dis": "Power", "equip": "meter1", "device": "meter", "type": "sensor",
             "kind": "Number", "unit": "kW", "pollClass": "fast",
             "address": {"objectType": "holdingRegister", "address": 0, "dataType": "float32"}},
        ],
    }


async def start(data):
    runtime = Runtime(MemoryStorage(runtime_config_from(data)), "bench", intervals=TEST_INTERVALS,
                      stale_check_s=0.05, flush_interval_s=0.1)
    await runtime.start()
    return runtime


async def test_modbus_coil_and_registers_through_the_bus_poller():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    runtime = await start(modbus_site(sim.port))
    try:
        assert find(runtime, "plant_io1_do3").writable and not find(runtime, "plant_meter1_power").writable
        await write_point(runtime, find(runtime, "plant_io1_do3"), True)
        assert sim.units[2].coils[3] is True
        await write_point(runtime, find(runtime, "plant_meter1_limit"), 123.5)
        regs = sim.units[1].writable[0:2]
        assert struct.unpack(">f", struct.pack(">HH", *regs))[0] == 123.5
        await write_point(runtime, find(runtime, "plant_meter1_steps"), 4.2)  # scale 0.1: raw 42
        assert sim.units[1].writable[2] == 42
        assert [w[0] for w in sim.writes] == [0x05, 0x10, 0x06]  # coil, two registers, one register

        point = find(runtime, "plant_meter1_limit")
        await wait_for(lambda: runtime.cache.get(point.id).value == 123.5)  # the next poll reads it back
        with pytest.raises(WriteError, match="does not fit"):
            await write_point(runtime, find(runtime, "plant_meter1_steps"), 5000.0)
        with pytest.raises(WriteError, match="no priorities"):
            await write_point(runtime, find(runtime, "plant_io1_do3"), None)
        sim.unit_modes[2] = "exception"
        with pytest.raises(WriteError, match="exception"):
            await write_point(runtime, find(runtime, "plant_io1_do3"), False)
    finally:
        await runtime.stop()
        await sim.stop()


# ---------- BACnet/IP ----------

async def test_bacnet_ip_priorities_and_release(tmp_path):
    sim = tb.BacnetSimulator(port=tb.free_udp_port(), update_interval_s=0.1)
    await sim.start()
    data = tb.config_for(sim)
    for p in data["points"]:
        if p["name"] == "site_ahu1_sp":
            p["writeLimits"] = {"min": 15, "max": 30}
    runtime = await tb.start(data)
    try:
        sp, fan, mode = (find(runtime, f"site_ahu1_{n}") for n in ("sp", "fan_cmd", "mode"))
        await write_point(runtime, sp, 23.5, MANUAL_PRIORITY)
        assert sim.setpoint.presentValue == 23.5
        assert sim.setpoint.priorityArray[MANUAL_PRIORITY - 1].real == 23.5
        await write_point(runtime, sp, 19.0, SCHEDULE_PRIORITY)
        assert sim.setpoint.presentValue == 23.5  # the operator's priority 8 still wins
        await write_point(runtime, sp, None, MANUAL_PRIORITY)  # release
        assert sim.setpoint.presentValue == 19.0  # back to the schedule's value
        await write_point(runtime, sp, None, SCHEDULE_PRIORITY)
        assert sim.setpoint.presentValue == 21.0  # the relinquish default

        await write_point(runtime, fan, False)
        assert str(sim.fan_command.presentValue) == "inactive"
        await write_point(runtime, mode, "Fan")
        assert sim.mode.presentValue == 3
        await wait_for(lambda: runtime.cache.get(fan.id).value is False)
        assert not find(runtime, "site_ahu1_room_temp").writable  # inputs are never commanded
    finally:
        await runtime.stop()
        await sim.stop()


# ---------- MS/TP through the service ----------

async def test_mstp_writes_through_the_service():
    service = tm.MstpServiceStandIn(tm.devices(), timeout_s=0.2)
    await service.start()
    runtime = await tm.start(tm.config(service.address))
    try:
        sp, fan = find(runtime, "site_ahu1_sp"), find(runtime, "site_ahu1_fan_cmd")
        device = service.devices[5]
        await write_point(runtime, sp, 24.0)
        assert device.writes[-1] == ((tm.AV, 1), 8, 24.0) and device.objects[(tm.AV, 1)] == 24.0
        await write_point(runtime, sp, None)
        assert device.objects[(tm.AV, 1)] == 21.0
        await write_point(runtime, fan, True)
        assert device.writes[-1] == ((tm.BO, 1), 8, 1)
        with pytest.raises(WriteError, match="write-access-denied"):
            await write_point(runtime, find(runtime, "site_ahu1_room_temp"), 20.0)
        await wait_for(lambda: runtime.cache.get(sp.id).status == Status.OK)
    finally:
        await runtime.stop()
        await service.stop()


@pytest.mark.skipif(not (tm.MSTP_BIN and tm.BACSERV_BIN and shutil.which("socat")),
                    reason="set BMS_MSTP_BIN and BMS_BACSERV_BIN (see mstp/README.md) and install socat")
async def test_real_mstp_service_writes_over_a_pty_pair(tmp_path):
    """The compiled bms-mstp WRITE command against bacnet-stack's own MS/TP server."""
    import subprocess

    tty_device, tty_service = tmp_path / "ttyDevice", tmp_path / "ttyService"
    api_port = tm.free_tcp_port()
    procs = []
    try:
        procs.append(subprocess.Popen(["socat", f"pty,raw,echo=0,link={tty_device}", f"pty,raw,echo=0,link={tty_service}"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        await tm.wait_for(lambda: tty_device.exists() and tty_service.exists(), timeout=5)
        common = {**os.environ, "BACNET_MSTP_BAUD": "38400", "BACNET_MAX_MASTER": "10"}
        procs.append(subprocess.Popen([tm.BACSERV_BIN, "1234"],
                                      env={**common, "BACNET_IFACE": str(tty_device), "BACNET_MSTP_MAC": "5"},
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen([tm.MSTP_BIN, "--listen", f"127.0.0.1:{api_port}", "--instance", "3999101",
                                       "--apdu-timeout", "1000", "--apdu-retries", "1"],
                                      env={**common, "BACNET_IFACE": str(tty_service), "BACNET_MSTP_MAC": "0"},
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        client = tm.MstpServiceClient(f"127.0.0.1:{api_port}", 5.0)
        for _ in range(50):
            try:
                await client.ping()
                break
            except Exception:
                await asyncio.sleep(0.1)
        await client.whois(wait_ms=1500)
        # bacnet-stack's server: analog-value 1 takes a write; analog-output 1 (type 1) is
        # commandable, so releasing priority 8 falls back to its relinquish default.
        await client.write(5, 2, 1, 85, 8, "real", 42.5)
        values = await client.read(5, [(2, 1, 85)])
        assert abs(values[0]["value"] - 42.5) < 1e-6
        before = (await client.read(5, [(1, 1, 85)]))[0]["value"]
        await client.write(5, 1, 1, 85, 8, "real", 55.5)
        assert abs((await client.read(5, [(1, 1, 85)]))[0]["value"] - 55.5) < 1e-6
        await client.write(5, 1, 1, 85, 8, "null", None)
        assert (await client.read(5, [(1, 1, 85)]))[0]["value"] == before
        client.close()
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


# ---------- MQTT ----------

@tq.needs_broker
async def test_mqtt_command_topic_and_template():
    broker = tq.Broker()
    await broker.start()
    data = tq.config(broker.port)
    data["points"].append(tq.mqtt_point("relay_cmd", "stat/plug1/POWER", "Bool") | {
        "type": "cmd", "address": {"topic": "stat/plug1/POWER", "commandTopic": "cmnd/plug1/POWER"}})
    data["points"].append(tq.mqtt_point("light", "zigbee2mqtt/room1_light", "Bool", "state", device="room1",
                                        equip="room1") | {
        "type": "cmd", "address": {"topic": "zigbee2mqtt/room1_light", "jsonPath": "state",
                                   "commandTopic": "zigbee2mqtt/room1_light/set",
                                   "commandTemplate": '{"state": "{value}"}'}})
    runtime = await tq.start(data)
    seen: list[tuple[str, str]] = []
    try:
        async with aiomqtt.Client("127.0.0.1", broker.port) as listener:
            await listener.subscribe("cmnd/#")
            await listener.subscribe("zigbee2mqtt/+/set")

            async def collect():
                async for m in listener.messages:
                    seen.append((str(m.topic), m.payload.decode()))
            task = asyncio.create_task(collect())
            link = runtime.buses[0]
            await wait_for(lambda: link.client is not None)
            await write_point(runtime, find(runtime, "site_plug1_relay_cmd"), True)
            await write_point(runtime, find(runtime, "site_room1_light"), False)
            await wait_for(lambda: len(seen) == 2)
            task.cancel()
        assert ("cmnd/plug1/POWER", "ON") in seen
        assert ("zigbee2mqtt/room1_light/set", json.dumps({"state": "OFF"})) in seen
        with pytest.raises(WriteError, match="no priorities"):
            await write_point(runtime, find(runtime, "site_plug1_relay_cmd"), None)
    finally:
        await runtime.stop()
        broker.cleanup()
