"""The BACnet router in bms-mstp (milestone 8): config checks, the router counters in the trunk's
health, and, when the binaries are built, a BACnet/IP tool reading an MS/TP device through
the real compiled service.

The real test needs BMS_MSTP_BIN, BMS_BACSERV_BIN and BMS_BACRP_BIN (mstp/build.sh
--with-test-tools), socat, and a non-loopback IPv4 interface (BMS_ROUTER_IFACE, default eth0).
"""

import asyncio
import fcntl
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
from pathlib import Path

import pytest

import test_mstp as tm
from devine_bms.config.validate import ConfigError, parse_config
from devine_bms.drivers.bacnet_mstp.client import MstpServiceClient

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "site.example-mstp.json"


def site(**trunk) -> dict:
    data = json.loads(EXAMPLE.read_text())
    data["bacnet"] = {"localAddress": "192.168.10.5/24", "deviceInstance": 3999001, "networkNumber": 1}
    data["buses"][0].update(trunk)
    return data


def errors(data) -> list[str]:
    try:
        parse_config(data)
    except ConfigError as exc:
        return [i.message for i in exc.issues if i.severity.value == "error"]
    return []


def test_network_numbers_and_router_checks():
    ok = site(networkNumber=101, router={"interface": "eth1:mstp1", "address": "192.168.10.21"})
    assert errors(ok) == []
    assert any("already used by bacnet" in e for e in errors(site(networkNumber=1)))
    assert any("needs its networkNumber" in e for e in errors(site(router={"interface": "eth1:mstp1", "address": "192.168.10.21"})))
    assert any("its own address" in e for e in errors(site(networkNumber=101, router={"interface": "eth1", "address": "192.168.10.5"})))
    two = site(networkNumber=101, router={"interface": "eth1:mstp1", "address": "192.168.10.21"})
    second = {**two["buses"][0], "name": "mstp2", "port": "/dev/bms-rs485-3", "deviceInstance": 3999102,
              "service": "127.0.0.1:47901", "networkNumber": 101}
    two["buses"].append(second)
    assert any("network number 101 is already used by trunk" in e for e in errors(two))
    second["networkNumber"] = 102
    assert any("each routing trunk needs its own address" in e for e in errors(two))
    no_bacnet = site(networkNumber=101, router={"interface": "eth1:mstp1", "address": "192.168.10.21"})
    del no_bacnet["bacnet"]
    assert any("site's BACnet settings" in e for e in errors(no_bacnet))


async def test_router_counters_reach_the_trunk_health():
    service = tm.MstpServiceStandIn(tm.devices(), timeout_s=0.2)
    service.routing = {"ipNet": 1, "mstpNet": 101, "fromIp": 7, "fromMstp": 9, "toIp": 9, "toMstp": 7,
                       "rejects": 0, "clashes": 0}
    await service.start()
    runtime = await tm.start(tm.config(service.address))
    try:
        driver = runtime.buses[0].driver
        await tm.wait_for(lambda: driver.stats is not None and "router" in driver.stats, timeout=8)
        assert driver.stats["router"]["mstpNet"] == 101 and driver.stats["router"]["fromIp"] == 7
        client = MstpServiceClient(service.address, 2.0)
        assert (await client.router())["routing"] is True
        client.close()
    finally:
        await runtime.stop()
        await service.stop()


def interface_address(name: str) -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name.encode()))[20:24])
    except OSError:
        return None


BACRP_BIN = os.environ.get("BMS_BACRP_BIN")
IFACE = os.environ.get("BMS_ROUTER_IFACE", "eth0")
IFACE_ADDRESS = interface_address(IFACE)


@pytest.mark.skipif(not (tm.MSTP_BIN and tm.BACSERV_BIN and BACRP_BIN and shutil.which("socat") and IFACE_ADDRESS),
                    reason="set BMS_MSTP_BIN, BMS_BACSERV_BIN and BMS_BACRP_BIN (mstp/build.sh --with-test-tools), "
                           "install socat, and have an IPv4 interface (BMS_ROUTER_IFACE)")
async def test_a_bacnet_ip_tool_reads_an_mstp_device_through_the_router():
    folder = Path(tempfile.mkdtemp(prefix="bms-router-"))
    tty_device, tty_service = folder / "ttyDevice", folder / "ttyService"
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
                                       "--route", IFACE, "--ip-net", "1", "--mstp-net", "101"],
                                      env={**common, "BACNET_IFACE": str(tty_service), "BACNET_MSTP_MAC": "0"},
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        client = MstpServiceClient(f"127.0.0.1:{api_port}", 5.0)
        for _ in range(50):
            try:
                await client.ping()
                break
            except Exception:
                await asyncio.sleep(0.1)
        await client.whois(wait_ms=1500)  # the trunk is up

        def bacrp(*args: str) -> str:
            out = subprocess.run([BACRP_BIN, *args, "--mac", f"{IFACE_ADDRESS}:47808", "--dnet", "101", "--dadr", "5"],
                                 env={**os.environ, "BACNET_IFACE": IFACE, "BACNET_IP_PORT": "47815"},
                                 capture_output=True, text=True, timeout=15)
            return out.stdout.strip()

        name = await asyncio.to_thread(bacrp, "1234", "device", "1234", "object-name")
        assert name == '"SimpleServer"'  # bacnet-stack's server, read over BACnet/IP via network 101
        value = await asyncio.to_thread(bacrp, "1234", "analog-value", "1", "present-value")
        assert float(value) == 0.0
        stats = await client.router()
        assert stats["routing"] is True and stats["mstpNet"] == 101 and stats["ipNet"] == 1
        assert stats["fromIp"] >= 2 and stats["toMstp"] >= 2 and stats["clashes"] == 0

        # Another router announcing network 101 on the IP network is a clash.
        npdu = bytes([0x01, 0x80, 0x01, 0x00, 101])  # I-Am-Router-To-Network: 101
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(bytes([0x81, 0x0A, 0, 4 + len(npdu)]) + npdu, (IFACE_ADDRESS, 47808))
        await tm.wait_for(lambda: True, timeout=0.1)
        await asyncio.sleep(0.5)
        assert (await client.router())["clashes"] == 1
        client.close()
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(folder, ignore_errors=True)
