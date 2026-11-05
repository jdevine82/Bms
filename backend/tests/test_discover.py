"""Discover scans and adding what they find (main spec 5.1.3, milestone 5A).

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import asyncio
import os
import socket

import pytest

from devine_bms.setup.discover import suggest_templates
from helpers import example_data
from setup_helpers import running_stack, stack  # noqa: F401 (stack is a fixture)
from simulators.bacnet_device import BacnetSimulator
from simulators.modbus_bus import IoModuleModel, MeterModel, ModbusSimulator
from simulators.mstp_service import MstpServiceStandIn, SimMstpDevice

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


async def finished(client, scan_id, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        scan = (await client.get(f"/api/setup/discover/{scan_id}")).json()
        if scan["status"] != "running":
            return scan
        assert asyncio.get_running_loop().time() < deadline, "scan did not finish"
        await asyncio.sleep(0.1)


def test_template_suggestions():
    assert suggest_templates("Mitsubishi Electric", "BAC-HD150") == ["mitsubishi-bac-hd150-group"]
    assert suggest_templates("CWT", "CWT-MB328B(II)") == ["cwt-mb328b"]
    assert suggest_templates("Someone Else", "XYZ-1") == []
    assert suggest_templates(None, None) == []


async def test_modbus_rtu_scan_through_the_bus_poller():
    sim = ModbusSimulator({1: MeterModel(), 2: IoModuleModel()}, port=0, framing="rtu", update_interval_s=0.05)
    await sim.start()
    data = example_data(5020)
    data["buses"] = [{"name": "rs485_1", "dis": "Line 1", "type": "modbus_rtu_over_tcp", "host": "127.0.0.1",
                      "port": sim.port, "timeoutS": 0.3, "retries": 0}]
    data["devices"].append({"name": "io1", "dis": "I/O", "protocol": "modbus",
                            "connection": {"bus": "rs485_1", "unitId": 2}})
    try:
        async for s in running_stack(data):
            client = s["client"]
            refused = await client.post("/api/setup/discover", json={"kind": "modbus_rtu", "bus": "rs485_1"})
            assert refused.status_code == 422 and "confirm" in refused.json()["error"]

            scan = (await client.post("/api/setup/discover", json={
                "kind": "modbus_rtu", "bus": "rs485_1", "from": 1, "to": 4, "confirm": True})).json()
            scan = await finished(client, scan["id"])
            assert scan["status"] == "done" and scan["progress"] == {"done": 4, "total": 4}
            rows = {r["unitId"]: r for r in scan["results"]}
            assert set(rows) == {1, 2}
            assert rows[1]["status"] == "new" and rows[1]["answer"] == "registers"
            assert rows[1]["vendor"] == "Devine BMS simulator" and rows[1]["productCode"] == "SIM-METER"
            assert rows[2]["status"] == "added" and rows[2]["device"] == "io1"
            assert rows[2]["answer"].startswith("exception")  # the I/O module has no holding registers

            index = scan["results"].index(rows[1])
            added = (await client.post(f"/api/setup/discover/{scan['id']}/add",
                                       json={"picks": [{"row": index, "name": "meter_rtu", "dis": "RTU meter"}]})).json()
            assert added["ok"] and added["added"][0]["device"] == "meter_rtu"
            device = (await client.get("/api/setup/devices/meter_rtu")).json()
            assert device["connection"]["bus"] == "rs485_1" and device["connection"]["unitId"] == 1
            again = (await client.post(f"/api/setup/discover/{scan['id']}/add",
                                       json={"picks": [{"row": index, "name": "meter_rtu2"}]})).json()
            assert not again["ok"] and "not new" in again["failed"][0]["error"]
            log = (await client.get("/api/setup/changes")).json()
            assert any(c["summary"] == "started a modbus_rtu scan" for c in log)
    finally:
        await sim.stop()


async def test_modbus_tcp_scan(stack):
    client, sim = stack["client"], stack["sim"]
    scan = (await client.post("/api/setup/discover", json={
        "kind": "modbus_tcp", "network": "127.0.0.1/32", "port": sim.port, "timeoutS": 0.5})).json()
    scan = await finished(client, scan["id"])
    assert scan["status"] == "done" and len(scan["results"]) == 1
    row = scan["results"][0]
    assert row["host"] == "127.0.0.1" and row["answer"] == "registers"
    assert row["status"] == "added" and row["device"] == "sim_meter"

    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    empty = (await client.post("/api/setup/discover", json={
        "kind": "modbus_tcp", "first": "127.0.0.1", "last": "127.0.0.3", "port": closed, "timeoutS": 0.3})).json()
    empty = await finished(client, empty["id"])
    assert empty["progress"] == {"done": 3, "total": 3}
    assert [r["status"] for r in empty["results"]] == ["missing"]  # sim_meter is at a different port


async def test_scan_can_be_stopped(stack):
    client = stack["client"]
    scan = (await client.post("/api/setup/discover", json={
        "kind": "modbus_tcp", "network": "10.255.255.0/28", "timeoutS": 5, "concurrency": 2})).json()
    await asyncio.sleep(0.2)
    stopped = (await client.delete(f"/api/setup/discover/{scan['id']}")).json()
    assert stopped["status"] in ("stopped", "done")
    assert (await client.post("/api/setup/discover", json={"kind": "modbus_tcp", "network": "10.0.0.0/8"})
            ).status_code == 422  # too many addresses


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_bacnet_ip_scan_reads_vendor_and_model():
    device = BacnetSimulator(port=free_udp_port(), update_interval_s=0.2)
    await device.start()
    data = example_data(5020)
    data["bacnet"] = {"localAddress": "127.0.0.1/8", "port": free_udp_port(), "deviceInstance": 3999001}
    data["devices"].append({"name": "ctrl_old", "dis": "Old controller", "protocol": "bacnet_ip",
                            "connection": {"address": "127.0.0.1:47999", "deviceInstance": 2001}})
    try:
        async for s in running_stack(data):
            client = s["client"]
            scan = (await client.post("/api/setup/discover", json={
                "kind": "bacnet_ip", "target": device.endpoint, "waitS": 1})).json()
            scan = await finished(client, scan["id"])
            assert scan["status"] == "done", scan
            row = scan["results"][0]
            assert row["instance"] == 1001 and row["address"] == device.endpoint and row["status"] == "new"
            assert row["vendor"] == "Devine BMS simulator" and row["model"] == "SIM-1"
            added = (await client.post(f"/api/setup/discover/{scan['id']}/add",
                                       json={"picks": [{"row": 0, "name": "ctrl1001"}]})).json()
            assert added["ok"]
            saved = (await client.get("/api/setup/devices/ctrl1001")).json()
            assert saved["connection"] == {**saved["connection"], "address": device.endpoint, "deviceInstance": 1001}
    finally:
        await device.stop()


async def test_mstp_scan_through_the_service():
    service = MstpServiceStandIn({5: SimMstpDevice(1234, {}), 6: SimMstpDevice(1235, {})}, timeout_s=0.2)
    await service.start()
    data = example_data(5020)
    data["buses"] = [{"name": "mstp1", "dis": "Trunk 1", "type": "bacnet_mstp", "service": service.address,
                      "port": "/dev/ttyMSTP", "deviceInstance": 3999101}]
    data["devices"].append({"name": "ctrl5", "dis": "Controller 5", "protocol": "bacnet_mstp",
                            "connection": {"trunk": "mstp1", "mac": 5, "deviceInstance": 1234}})
    try:
        async for s in running_stack(data):
            client = s["client"]
            scan = (await client.post("/api/setup/discover", json={
                "kind": "bacnet_mstp", "trunk": "mstp1", "waitMs": 100, "confirm": True})).json()
            scan = await finished(client, scan["id"])
            statuses = {r["mac"]: r["status"] for r in scan["results"]}
            assert statuses == {5: "added", 6: "new"}
    finally:
        await service.stop()
