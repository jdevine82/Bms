"""Discover: find devices on each network and add them (main spec 5.1.3, milestone 5A).

Scans run in the background with progress and can be stopped. They only read: a Who-Is,
or one register read and an optional device identification per address. Every scan is
recorded in the change log. A scan on a live serial line (Modbus RTU or MS/TP) needs
`confirm: true`, because it adds traffic to a bus that is in use.

Each result gets a status: new, added (a saved device is at that address), clash (two
devices answer at one address or instance) or missing (a saved device on that network
that did not answer), and template suggestions from its vendor and model.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from devine_bms.drivers.errors import DeviceDown, DeviceFault
from devine_bms.setup.service import SetupError, SetupService
from devine_bms.templates.device import builtin_templates

MAX_TCP_HOSTS = 1024
MAX_SCANS_KEPT = 20


@dataclass
class Scan:
    id: str
    kind: str
    params: dict
    started: float = field(default_factory=time.time)
    status: str = "running"  # running, done, stopped, failed
    done: int = 0
    total: int = 0
    error: str | None = None
    results: list[dict] = field(default_factory=list)
    task: asyncio.Task | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "params": self.params, "status": self.status,
                "progress": {"done": self.done, "total": self.total}, "error": self.error,
                "results": self.results, "started": self.started}


def suggest_templates(vendor: str | None, model: str | None, extra: list | None = None) -> list[str]:
    """Templates whose vendor and model match what the device reports (case and spacing ignored)."""
    def norm(text: str | None) -> str:
        return "".join(ch for ch in (text or "").lower() if ch.isalnum())
    v, m = norm(vendor), norm(model)
    if not v and not m:
        return []
    found = []
    for t in list(builtin_templates().values()) + list(extra or []):
        tv = norm(t.vendor)
        tm = norm(t.model.split("(")[0])  # "BAC-HD150 (one group)" matches a device reporting "BAC-HD150"
        model_hit = bool(m and tm) and (m in tm or tm in m)
        vendor_hit = bool(v and tv) and (v in tv or tv in v)
        if model_hit or (vendor_hit and not m):
            found.append(t.id)
    return found


class DiscoverManager:
    def __init__(self, runtime, setup: SetupService) -> None:
        self.runtime = runtime
        self.setup = setup
        self.scans: dict[str, Scan] = {}

    # ---------- start, read, stop ----------

    async def start(self, kind: str, params: dict, client: str) -> Scan:
        runner = {"bacnet_ip": self._bacnet_ip, "bacnet_mstp": self._bacnet_mstp,
                  "modbus_tcp": self._modbus_tcp, "modbus_rtu": self._modbus_rtu}.get(kind)
        if runner is None:
            raise SetupError("scan kind must be bacnet_ip, bacnet_mstp, modbus_tcp or modbus_rtu", status=404)
        if kind in ("modbus_rtu", "bacnet_mstp") and not params.get("confirm"):
            raise SetupError("this scan adds traffic to a live serial line; send confirm: true to go ahead")
        self._check(kind, params)
        scan = Scan(uuid.uuid4().hex[:12], kind, params)
        self.scans[scan.id] = scan
        while len(self.scans) > MAX_SCANS_KEPT:
            self.scans.pop(next(iter(self.scans)))
        await self.setup.record(client, "scan", f"started a {kind} scan", kind="scan", name=scan.id, after=params)
        scan.task = asyncio.create_task(self._run(scan, runner), name=f"scan {scan.id}")
        return scan

    def get(self, scan_id: str) -> Scan:
        scan = self.scans.get(scan_id)
        if scan is None:
            raise SetupError(f"no scan '{scan_id}'", status=404)
        return scan

    async def stop(self, scan_id: str) -> Scan:
        scan = self.get(scan_id)
        if scan.task and not scan.task.done():
            scan.task.cancel()
            await asyncio.gather(scan.task, return_exceptions=True)
            scan.status = "stopped"
        return scan

    async def stop_all(self) -> None:
        for scan in list(self.scans.values()):
            if scan.task and not scan.task.done():
                await self.stop(scan.id)

    async def _run(self, scan: Scan, runner) -> None:
        try:
            await runner(scan)
            self._mark(scan)
            scan.status = "done"
        except asyncio.CancelledError:
            self._mark(scan)
            scan.status = "stopped"
            raise
        except SetupError as exc:
            scan.status, scan.error = "failed", exc.message
        except Exception as exc:  # report it on the scan; the backend carries on
            scan.status, scan.error = "failed", f"{type(exc).__name__}: {exc}"

    def _check(self, kind: str, p: dict) -> None:
        if kind == "modbus_tcp":
            hosts = self._hosts(p)
            if not 1 <= len(hosts) <= MAX_TCP_HOSTS:
                raise SetupError(f"scan 1 to {MAX_TCP_HOSTS} addresses at a time")
        if kind == "modbus_rtu":
            first, last = int(p.get("from", 1)), int(p.get("to", 247))
            if not 1 <= first <= last <= 247:
                raise SetupError("unit IDs run from 1 to 247")
            if self._bus(p.get("bus"), "modbus") is None:
                raise SetupError(f"no Modbus bus '{p.get('bus')}'")
        if kind == "bacnet_mstp" and self._bus(p.get("trunk"), "bacnet_mstp") is None:
            raise SetupError(f"no MS/TP trunk '{p.get('trunk')}'")
        if kind == "bacnet_ip" and self.runtime.bacnet is None:
            raise SetupError("BACnet/IP scans need the site's BACnet settings (and a running BACnet link)")

    def _bus(self, name, family: str):
        for bus in self.runtime.config.buses:
            if bus.name == name and (bus.settings.type == family or
                                     (family == "modbus" and bus.settings.type.startswith("modbus"))):
                return bus
        return None

    @staticmethod
    def _hosts(p: dict) -> list[str]:
        if p.get("network"):
            net = ipaddress.ip_network(p["network"], strict=False)
            return [str(h) for h in itertools.islice(net.hosts(), MAX_TCP_HOSTS + 1)]
        first, last = ipaddress.ip_address(p.get("first", "")), ipaddress.ip_address(p.get("last", p.get("first")))
        count = int(last) - int(first) + 1
        if count < 1 or count > MAX_TCP_HOSTS:
            return []
        return [str(ipaddress.ip_address(int(first) + i)) for i in range(count)]

    # ---------- scans ----------

    async def _bacnet_ip(self, scan: Scan) -> None:
        from devine_bms.drivers.bacnet.link import device_address

        app = self.runtime.bacnet.app
        p = scan.params
        scan.total = 2
        target = device_address(p["target"]) if p.get("target") else None
        answers = await app.who_is(p.get("low"), p.get("high"), target, timeout=float(p.get("waitS", 3)))
        scan.done = 1
        for i_am in answers:
            row = {"protocol": "bacnet_ip", "instance": i_am.iAmDeviceIdentifier[1], "address": str(i_am.pduSource),
                   "vendorId": int(i_am.vendorID)}
            row.update(await self._bacnet_identity(app, i_am.pduSource, row["instance"]))
            scan.results.append(row)
        scan.done = 2

    @staticmethod
    async def _bacnet_identity(app, address, instance: int) -> dict:
        from bacpypes3.primitivedata import ObjectIdentifier

        try:
            rows = await asyncio.wait_for(app.read_property_multiple(address, [
                ObjectIdentifier(f"device,{instance}"),
                ["vendor-name", "model-name", "object-name", "firmware-revision"]]), 3)
        except Exception:
            return {}
        names = {"vendor-name": "vendor", "model-name": "model", "object-name": "objectName",
                 "firmware-revision": "firmware"}
        return {names[str(prop)]: str(value) for _o, prop, _i, value in rows
                if str(prop) in names and not type(value).__name__.startswith("Error")}

    async def _bacnet_mstp(self, scan: Scan) -> None:
        from devine_bms.drivers.bacnet_mstp.client import MstpServiceClient

        p = scan.params
        trunk = self._bus(p["trunk"], "bacnet_mstp")
        client = MstpServiceClient(trunk.settings.service, trunk.settings.timeout_s)
        try:
            scan.total = 2
            found = await client.whois(int(p.get("low", -1)), int(p.get("high", -1)), int(p.get("waitMs", 3000)))
            scan.done = 1
            for d in found:
                row = {"protocol": "bacnet_mstp", "trunk": trunk.name, "instance": d["instance"], "mac": d["mac"],
                       "vendorId": d.get("vendor")}
                try:
                    values = await client.read(d["mac"], [(8, d["instance"], 121), (8, d["instance"], 70),
                                                          (8, d["instance"], 77)])
                    keys = {121: "vendor", 70: "model", 77: "objectName"}
                    row.update({keys[v["property"]]: v["value"] for v in values if "value" in v})
                except (DeviceDown, DeviceFault):
                    pass
                scan.results.append(row)
            scan.done = 2
        finally:
            client.close()

    async def _modbus_tcp(self, scan: Scan) -> None:
        from devine_bms.config.model import ModbusTcpConnection, ObjectType
        from devine_bms.drivers.modbus.client import ModbusClient

        p = scan.params
        hosts = self._hosts(p)
        port, unit = int(p.get("port", 502)), int(p.get("unitId", 1))
        timeout = float(p.get("timeoutS", 0.5))
        scan.total = len(hosts)
        limit = asyncio.Semaphore(int(p.get("concurrency", 16)))

        async def probe(host: str) -> None:
            async with limit:
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
                    writer.close()
                except (OSError, TimeoutError):
                    scan.done += 1
                    return
                client = ModbusClient.for_tcp_device(
                    host, ModbusTcpConnection(host=host, port=port, unit_id=unit, timeout_s=max(timeout, 1),
                                              retries=0))
                row = {"protocol": "modbus_tcp", "host": host, "port": port, "unitId": unit}
                try:
                    await client.read(unit, ObjectType.HOLDING_REGISTER, 0, 1)
                    row["answer"] = "registers"
                except DeviceFault as exc:
                    row["answer"] = f"exception ({exc})"  # it answered, so something is there
                except DeviceDown:
                    row["answer"] = "port open, no Modbus answer"
                try:
                    identity = await client.identify(unit)
                except DeviceDown:
                    identity = None
                if identity:
                    row.update(identity)
                client.close()
                scan.results.append(row)
                scan.done += 1

        await asyncio.gather(*(probe(h) for h in hosts))
        scan.results.sort(key=lambda r: ipaddress.ip_address(r["host"]))

    async def _modbus_rtu(self, scan: Scan) -> None:
        from devine_bms.config.model import ObjectType
        from devine_bms.drivers.modbus.client import ModbusClient

        p = scan.params
        bus = self._bus(p["bus"], "modbus")
        first, last = int(p.get("from", 1)), int(p.get("to", 247))
        scan.total = last - first + 1
        poller = next((b for b in self.runtime.buses if b.name == bus.name and hasattr(b, "submit")), None)
        own = None if poller else ModbusClient.for_bus(bus.settings)

        async def probe(client: ModbusClient, unit: int) -> dict | None:
            try:
                await client.read(unit, ObjectType.HOLDING_REGISTER, 0, 1)
                answer = "registers"
            except DeviceFault as exc:
                answer = f"exception ({exc})"
            except DeviceDown as exc:
                if "answer" in str(exc):
                    return None  # nothing at this unit ID
                raise
            row = {"protocol": "modbus", "bus": bus.name, "unitId": unit, "answer": answer}
            identity = await client.identify(unit)
            if identity:
                row.update(identity)
            return row

        try:
            for unit in range(first, last + 1):
                if poller is not None:
                    # Between polls, through the connection the poller already holds.
                    row = await poller.submit(lambda driver, u=unit: probe(driver.client, u), timeout_s=60)
                else:
                    row = await probe(own, unit)
                if row:
                    scan.results.append(row)
                scan.done += 1
        finally:
            if own is not None:
                own.close()

    # ---------- status per row ----------

    def _mark(self, scan: Scan) -> None:
        devices = self.runtime.config.devices
        site_templates: list = []
        for row in scan.results:
            row["templates"] = suggest_templates(row.get("vendor"), row.get("model") or row.get("productCode"),
                                                 site_templates)
            row["status"] = "new"
            for d in devices:
                if self._same_device(scan.kind, row, d):
                    row["status"], row["device"] = "added", d.name
        # Two answers at one instance (BACnet) are a clash: many units ship with the same default.
        if scan.kind.startswith("bacnet"):
            seen: dict[int, dict] = {}
            for row in scan.results:
                if row["instance"] in seen:
                    row["status"] = seen[row["instance"]]["status"] = "clash"
                seen[row["instance"]] = row
            for d in devices:
                if d.protocol == scan.kind and d.connection.device_instance not in seen and self._in_scope(scan, d):
                    scan.results.append({"protocol": d.protocol, "instance": d.connection.device_instance,
                                         "status": "missing", "device": d.name, "templates": []})
        else:
            answered = {(r.get("host"), r.get("bus"), r["unitId"]) for r in scan.results}
            for d in devices:
                key = self._modbus_key(d)
                if key and self._in_scope(scan, d) and key not in answered:
                    scan.results.append({"protocol": d.protocol, "unitId": key[2], "host": key[0], "bus": key[1],
                                         "status": "missing", "device": d.name, "templates": []})

    def _same_device(self, kind: str, row: dict, d) -> bool:
        if kind == "bacnet_ip" and d.protocol == "bacnet_ip":
            return d.connection.device_instance == row["instance"]
        if kind == "bacnet_mstp" and d.protocol == "bacnet_mstp":
            return d.connection.device_instance == row["instance"] or d.connection.mac == row.get("mac")
        key = self._modbus_key(d)
        return key is not None and key == (row.get("host"), row.get("bus"), row.get("unitId"))

    def _modbus_key(self, d):
        if d.protocol == "modbus_tcp":
            return (d.connection.host, None, d.connection.unit_id)
        if d.protocol == "modbus":
            bus = next((b for b in self.runtime.config.buses if b.id == d.bus_id), None)
            return (None, bus.name if bus else None, d.connection.unit_id)
        return None

    def _in_scope(self, scan: Scan, d) -> bool:
        p = scan.params
        if scan.kind == "bacnet_ip":
            low, high = p.get("low"), p.get("high")
            n = d.connection.device_instance
            return d.protocol == "bacnet_ip" and (low is None or n >= low) and (high is None or n <= high) \
                and not p.get("target")
        if scan.kind == "bacnet_mstp":
            return d.protocol == "bacnet_mstp" and d.bus_id == self._bus(p["trunk"], "bacnet_mstp").id
        if scan.kind == "modbus_tcp":
            return d.protocol == "modbus_tcp" and d.connection.host in set(self._hosts(p)) \
                and d.connection.unit_id == int(p.get("unitId", 1))
        bus = self._bus(p["bus"], "modbus")
        return d.protocol == "modbus" and d.bus_id == bus.id and \
            int(p.get("from", 1)) <= d.connection.unit_id <= int(p.get("to", 247))

    # ---------- add selected ----------

    async def add(self, scan_id: str, picks: list[dict], client: str) -> dict:
        """Add chosen results as devices. picks: [{"row": index, "name": "...", "dis": "..."}].
        Each is checked and added on its own; ones that fail stay out, with the reason."""
        scan = self.get(scan_id)
        added, failed = [], []
        for pick in picks:
            try:
                row = scan.results[int(pick["row"])]
            except (KeyError, IndexError, ValueError):
                failed.append({"pick": pick, "error": "no such result row"})
                continue
            if row["status"] in ("added", "missing"):
                failed.append({"pick": pick, "error": f"this result is '{row['status']}', not new"})
                continue
            device = {"name": pick.get("name"), "dis": pick.get("dis") or pick.get("name"),
                      **self._device_config(row)}
            try:
                result = await self.setup.put_item("devices", device["name"], device, client)
            except SetupError as exc:
                failed.append({"pick": pick, "error": exc.message, "issues": exc.as_dict()["issues"]})
                continue
            row["status"], row["device"] = "added", device["name"]
            added.append({"device": device["name"], "warnings": result.get("warnings", [])})
        return {"ok": not failed, "added": added, "failed": failed}

    @staticmethod
    def _device_config(row: dict) -> dict[str, Any]:
        if row["protocol"] == "bacnet_ip":
            return {"protocol": "bacnet_ip", "connection": {"address": row["address"],
                                                            "deviceInstance": row["instance"]}}
        if row["protocol"] == "bacnet_mstp":
            return {"protocol": "bacnet_mstp", "connection": {"trunk": row["trunk"], "mac": row["mac"],
                                                              "deviceInstance": row["instance"]}}
        if row["protocol"] == "modbus_tcp":
            return {"protocol": "modbus_tcp", "connection": {"host": row["host"], "port": row["port"],
                                                             "unitId": row["unitId"]}}
        return {"protocol": "modbus", "connection": {"bus": row["bus"], "unitId": row["unitId"]}}
