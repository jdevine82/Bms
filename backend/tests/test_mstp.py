"""BACnet MS/TP through the bms-mstp service (milestone 4c).

Most tests use the Python stand-in for the service. `test_real_service_over_a_pty_pair`
runs the real compiled bms-mstp against a real bacnet-stack MS/TP device (bacserv) on
two pseudo-terminals joined by socat; it runs when BMS_MSTP_BIN and BMS_BACSERV_BIN
point at the built programs (see mstp/README.md), and is skipped otherwise.
"""

import asyncio
import copy
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

from devine_bms.config.validate import ConfigError, parse_config
from devine_bms.drivers.bacnet_mstp.client import MstpServiceClient
from devine_bms.drivers.errors import BusDown, DeviceDown, DeviceFault
from devine_bms.runtime import Runtime
from devine_bms.storage.base import MemoryStorage
from devine_bms.values import Status
from helpers import TEST_INTERVALS, runtime_config_from
from simulators.mstp_service import MstpServiceStandIn, SimMstpDevice

AI, AV, BI, BO, MSV = 0, 2, 3, 4, 19


def devices():
    return {
        5: SimMstpDevice(1234, {(AI, 1): 22.5, (AV, 1): 21.0, (BI, 1): 1, (BO, 1): 0, (MSV, 1): 3}),
        6: SimMstpDevice(1235, {(AI, 1): 18.0}),
    }


def point(name, device, obj, instance, kind, poll="fast", type_="sensor", **extra):
    return {"name": f"site_ahu1_{name}", "dis": name, "equip": "ahu1", "device": device, "type": type_,
            "kind": kind, "pollClass": poll, "address": {"objectType": obj, "instance": instance}, **extra}


def config(service: str, port: str = "/dev/bms-rs485-2") -> dict:
    return {
        "schema": "devine-bms-config/0",
        "site": {"name": "bench", "dis": "Bench"},
        "equips": [{"name": "ahu1", "dis": "AHU 1", "tags": {"ahu": True}}],
        "buses": [{"name": "mstp1", "dis": "MS/TP trunk 1", "type": "bacnet_mstp", "service": service,
                   "port": port, "baudrate": 38400, "maxMaster": 10, "deviceInstance": 3999101,
                   "timeoutS": 3}],
        "devices": [
            {"name": "ctrl5", "dis": "Controller 5", "protocol": "bacnet_mstp",
             "connection": {"trunk": "mstp1", "mac": 5, "deviceInstance": 1234}},
            {"name": "ctrl6", "dis": "Controller 6", "protocol": "bacnet_mstp",
             "connection": {"trunk": "mstp1", "mac": 6, "deviceInstance": 1235}},
        ],
        "points": [
            point("room_temp", "ctrl5", "analogInput", 1, "Number", unit="°C"),
            point("sp", "ctrl5", "analogValue", 1, "Number", poll="normal", type_="sp", unit="°C"),
            point("fan_status", "ctrl5", "binaryInput", 1, "Bool"),
            point("fan_cmd", "ctrl5", "binaryOutput", 1, "Bool", poll="normal", type_="cmd"),
            point("mode", "ctrl5", "multiStateValue", 1, "Str", poll="normal", type_="sp",
                  enum=["Cool", "Heat", "Fan"]),
            point("missing", "ctrl5", "analogInput", 9, "Number", poll="slow"),
            point("other_temp", "ctrl6", "analogInput", 1, "Number", unit="°C"),
        ],
    }


@pytest.fixture
async def service():
    s = MstpServiceStandIn(devices(), timeout_s=0.2)
    await s.start()
    yield s
    await s.stop()


async def start(data):
    runtime = Runtime(MemoryStorage(runtime_config_from(data)), "bench", intervals=TEST_INTERVALS,
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


GOOD = ["room_temp", "sp", "fan_status", "fan_cmd", "mode", "other_temp"]


def all_good(runtime, names=GOOD):
    return all(live(runtime, n).status == Status.OK for n in names)


async def test_reads_through_the_service(service):
    runtime = await start(config(service.address))
    try:
        await wait_for(lambda: all_good(runtime))
        assert live(runtime, "room_temp").value == 22.5
        assert live(runtime, "fan_status").value is True and live(runtime, "fan_cmd").value is False
        assert live(runtime, "mode").value == "Fan"
        assert live(runtime, "other_temp").value == 18.0
        await wait_for(lambda: live(runtime, "missing").status == Status.FAULT)
        assert "unknown-object" in live(runtime, "missing").reason
        assert len(runtime.buses) == 1 and runtime.buses[0].driver.shared is True
    finally:
        await runtime.stop()


async def test_status_flags(service):
    runtime = await start(config(service.address))
    try:
        await wait_for(lambda: all_good(runtime))
        service.devices[5].flags[(AI, 1)] = [False, True, False, False]
        await wait_for(lambda: live(runtime, "room_temp").status == Status.FAULT)
        service.devices[5].flags[(AV, 1)] = [False, False, False, True]
        await wait_for(lambda: live(runtime, "sp").status == Status.FAULT)
        assert "out of service" in live(runtime, "sp").reason
    finally:
        await runtime.stop()


async def test_one_silent_device_does_not_stop_the_trunk(service):
    runtime = await start(config(service.address))
    try:
        await wait_for(lambda: all_good(runtime))
        service.devices[6].mode = "silent"
        await wait_for(lambda: live(runtime, "other_temp").status == Status.DOWN)
        await asyncio.sleep(0.5)
        assert all_good(runtime, ["room_temp", "sp", "fan_status"])
        service.devices[6].mode = "normal"
        await wait_for(lambda: live(runtime, "other_temp").status == Status.OK, timeout=8)
    finally:
        await runtime.stop()


async def test_service_down_marks_the_trunk_down_and_recovers(service):
    runtime = await start(config(service.address))
    try:
        await wait_for(lambda: all_good(runtime))
        port = service.port
        await service.stop()
        await wait_for(lambda: all(live(runtime, n).status == Status.DOWN for n in GOOD))
        assert "bms-mstp" in live(runtime, "room_temp").reason
        assert runtime.buses[0].online is False
        again = MstpServiceStandIn(devices(), port=port, timeout_s=0.2)
        await again.start()
        try:
            await wait_for(lambda: all_good(runtime), timeout=10)
        finally:
            await again.stop()
    finally:
        await runtime.stop()


async def test_rejected_request_is_a_fault(service):
    runtime = await start(config(service.address))
    try:
        await wait_for(lambda: all_good(runtime))
        service.devices[6].mode = "reject"
        await wait_for(lambda: live(runtime, "other_temp").status == Status.FAULT)
        assert "reject" in live(runtime, "other_temp").reason
    finally:
        await runtime.stop()


async def test_client_whois_ping_and_errors(service):
    client = MstpServiceClient(service.address, 2.0)
    try:
        assert (await client.ping())["service"] == "bms-mstp"
        assert [d["mac"] for d in await client.whois(wait_ms=100)] == [5, 6]
        with pytest.raises(DeviceDown):
            await client.read(9, [(AI, 1, 85)])
        service.devices[5].mode = "reject"
        with pytest.raises(DeviceFault):
            await client.read(5, [(AI, 1, 85)])
    finally:
        client.close()
    with pytest.raises(BusDown):
        await MstpServiceClient("127.0.0.1:1", 1.0).ping()


async def test_trunk_stats_reach_the_health_data(service):
    runtime = await start(config(service.address))
    try:
        driver = runtime.buses[0].driver
        await wait_for(lambda: driver.stats is not None)
        assert driver.stats["ok"] is True and "lostToken" in driver.stats
    finally:
        await runtime.stop()


def errors_of(data):
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    return str(exc.value)


def test_mac_0_and_1_are_reserved():
    data = config("127.0.0.1:47900")
    data["devices"][0]["connection"]["mac"] = 1
    with pytest.raises(ConfigError):
        parse_config(data)


def test_duplicate_mac_on_a_trunk():
    data = config("127.0.0.1:47900")
    data["devices"][1]["connection"]["mac"] = 5
    assert "MAC 5 on trunk 'mstp1' is already used by device 'ctrl5'" in errors_of(data)


def test_mac_above_max_master_is_a_warning():
    data = config("127.0.0.1:47900")
    data["devices"][1]["connection"]["mac"] = 20
    _, warnings = parse_config(data)
    assert any("above the trunk's Max_Master" in str(w) for w in warnings)


def test_serial_port_not_shared_with_a_modbus_line():
    data = config("127.0.0.1:47900")
    data["buses"].append({"name": "rs485_1", "dis": "Modbus", "type": "modbus_rtu", "port": "/dev/bms-rs485-2"})
    assert "already used by bus 'mstp1'" in errors_of(data)


def test_device_instance_clash_with_the_trunk_node():
    data = config("127.0.0.1:47900")
    data["devices"][0]["connection"]["deviceInstance"] = 3999101
    assert "already used by MS/TP trunk 'mstp1'" in errors_of(data)


def test_modbus_device_cannot_use_an_mstp_trunk():
    data = config("127.0.0.1:47900")
    data["devices"].append({"name": "m1", "dis": "M", "protocol": "modbus", "connection": {"bus": "mstp1", "unitId": 3}})
    assert "is a BACnet MS/TP trunk, not a Modbus bus" in errors_of(data)


def test_valid_config():
    config_, warnings = parse_config(copy.deepcopy(config("127.0.0.1:47900")))
    assert config_.buses[0].type == "bacnet_mstp" and warnings == []


# ---------- the real service against a real bacnet-stack MS/TP device ----------

MSTP_BIN = os.environ.get("BMS_MSTP_BIN")
BACSERV_BIN = os.environ.get("BMS_BACSERV_BIN")


def free_tcp_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not (MSTP_BIN and BACSERV_BIN and shutil.which("socat")),
                    reason="set BMS_MSTP_BIN and BMS_BACSERV_BIN (see mstp/README.md) and install socat")
async def test_real_service_over_a_pty_pair():
    folder = Path(tempfile.mkdtemp(prefix="bms-mstp-"))
    tty_device, tty_service = folder / "ttyDevice", folder / "ttyService"
    api_port = free_tcp_port()
    procs = []
    try:
        procs.append(subprocess.Popen(
            ["socat", f"pty,raw,echo=0,link={tty_device}", f"pty,raw,echo=0,link={tty_service}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        await wait_for(lambda: tty_device.exists() and tty_service.exists(), timeout=5)
        common = {**os.environ, "BACNET_MSTP_BAUD": "38400", "BACNET_MAX_MASTER": "10"}
        procs.append(subprocess.Popen(
            [BACSERV_BIN, "1234"], env={**common, "BACNET_IFACE": str(tty_device), "BACNET_MSTP_MAC": "5"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen(
            [MSTP_BIN, "--listen", f"127.0.0.1:{api_port}", "--instance", "3999101",
             "--apdu-timeout", "1000", "--apdu-retries", "1"],
            env={**common, "BACNET_IFACE": str(tty_service), "BACNET_MSTP_MAC": "0"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

        client = MstpServiceClient(f"127.0.0.1:{api_port}", 5.0)
        for _ in range(50):
            try:
                await client.ping()
                break
            except BusDown:
                await asyncio.sleep(0.1)
        found = await client.whois(wait_ms=2000)
        assert {"instance": 1234, "mac": 5} == {k: found[0][k] for k in ("instance", "mac")}
        client.close()

        # The bacnet-stack server's objects are instance 1; ctrl6 at MAC 6 does not exist.
        data = config(f"127.0.0.1:{api_port}", port=str(tty_service))
        data["points"] = [p for p in data["points"] if p["name"] != "site_ahu1_mode"]
        runtime = await start(data)
        try:
            await wait_for(lambda: all_good(runtime, ["room_temp", "sp", "fan_status", "fan_cmd"]), timeout=15)
            assert isinstance(live(runtime, "room_temp").value, float)
            assert isinstance(live(runtime, "fan_status").value, bool)
            await wait_for(lambda: live(runtime, "missing").status == Status.FAULT, timeout=15)
            await wait_for(lambda: live(runtime, "other_temp").status == Status.DOWN, timeout=15)
            driver = runtime.buses[0].driver
            await wait_for(lambda: driver.stats is not None, timeout=5)
            assert driver.stats["badCrc"] == 0 and driver.stats["receiveValidFrames"] > 0
        finally:
            await runtime.stop()
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(folder, ignore_errors=True)
