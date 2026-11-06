"""A stand-in for the bms-mstp service (milestone 4c), for tests without the C build.

It speaks the same local API as mstp/bms-mstp/main.c (text requests, JSON answers)
and holds simulated MS/TP devices by MAC address. The real service is tested against
a real bacnet-stack MS/TP device in backend/tests/test_mstp.py when it is built.

Device modes: "normal"; "silent" (the service answers "no answer (timeout)" after
`timeout_s`); "reject" (every request is rejected, as by a device without
ReadPropertyMultiple).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

PRESENT_VALUE = 85
STATUS_FLAGS = 111


@dataclass
class SimMstpDevice:
    instance: int
    # (object type number, instance) -> present value. Status flags default to all clear.
    objects: dict[tuple[int, int], object]
    flags: dict[tuple[int, int], list[bool]] = field(default_factory=dict)
    mode: str = "normal"
    vendor: int = 260
    # WriteProperty (milestone 7A): (type, instance) -> {priority: value}; the highest set
    # priority gives the present value, else the relinquish default (the starting value).
    priorities: dict[tuple[int, int], dict[int, object]] = field(default_factory=dict)
    defaults: dict[tuple[int, int], object] = field(default_factory=dict)
    writes: list[tuple] = field(default_factory=list)


class MstpServiceStandIn:
    def __init__(self, devices: dict[int, SimMstpDevice], host: str = "127.0.0.1", port: int = 0,
                 timeout_s: float = 0.3, mac: int = 0, instance: int = 3999101) -> None:
        self.devices = devices
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.mac = mac
        self.instance = instance
        self.requests = 0
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            for w in list(self._writers):
                w.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                parts = line.decode().split()
                if len(parts) < 2:
                    continue
                answer = await self.answer(parts[0], parts[1], parts[2:])
                writer.write((json.dumps(answer) + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def answer(self, command: str, request_id: str, args: list[str]) -> dict:
        self.requests += 1
        if command == "PING":
            return {"id": request_id, "ok": True, "service": "bms-mstp", "version": "stand-in",
                    "mac": self.mac, "instance": self.instance}
        if command == "STATS":
            return {"id": request_id, "ok": True, "mac": self.mac, "transmitFrames": self.requests,
                    "receiveValidFrames": self.requests, "receiveInvalidFrames": 0, "lostToken": 0, "badCrc": 0}
        if command == "WHOIS":
            await asyncio.sleep(int(args[2]) / 1000 if len(args) > 2 else 0.1)
            return {"id": request_id, "ok": True, "devices": [
                {"instance": d.instance, "mac": mac, "maxApdu": 480, "segmentation": 3, "vendor": d.vendor}
                for mac, d in sorted(self.devices.items()) if d.mode != "silent"
            ]}
        if command == "READ":
            return await self._read(request_id, args)
        if command == "WRITE":
            return await self._write(request_id, args)
        return {"id": request_id, "ok": False, "error": "unknown request; use PING, STATS, WHOIS, READ or WRITE"}

    async def _write(self, request_id: str, args: list[str]) -> dict:
        """WRITE <mac> <type>:<instance>:<property> <priority> <tag> <value>, as the real service."""
        mac = int(args[0])
        device = self.devices.get(mac)
        if device is None or device.mode == "silent":
            await asyncio.sleep(self.timeout_s)
            return {"id": request_id, "ok": False, "error": "no answer (timeout)"}
        obj_type, instance, prop = (int(x) for x in args[1].split(":"))
        priority, tag, raw = int(args[2]), args[3], args[4]
        key = (obj_type, instance)
        if key not in device.objects:
            return {"id": request_id, "ok": False, "error": "BACnet error object: unknown-object"}
        if obj_type in (0, 3, 13) or prop != PRESENT_VALUE:  # inputs are not writable
            return {"id": request_id, "ok": False, "error": "BACnet error property: write-access-denied"}
        value = None if tag == "null" else float(raw) if tag == "real" else int(raw)
        device.writes.append((key, priority, value))
        device.defaults.setdefault(key, device.objects[key])
        slots = device.priorities.setdefault(key, {})
        if value is None:
            slots.pop(priority, None)
        else:
            slots[priority or 16] = value
        device.objects[key] = slots[min(slots)] if slots else device.defaults[key]
        return {"id": request_id, "ok": True}

    async def _read(self, request_id: str, args: list[str]) -> dict:
        mac = int(args[0])
        device = self.devices.get(mac)
        if device is None or device.mode == "silent":
            await asyncio.sleep(self.timeout_s)
            return {"id": request_id, "ok": False, "error": "no answer (timeout)"}
        if device.mode == "reject":
            return {"id": request_id, "ok": False, "error": "BACnet reject: unrecognized-service"}
        values = []
        for ref in args[1:]:
            obj_type, instance, prop = (int(x) for x in ref.split(":"))
            row = {"type": obj_type, "instance": instance, "property": prop}
            key = (obj_type, instance)
            if key not in device.objects:
                row["error"] = {"class": "object", "code": "unknown-object"}
            elif prop == PRESENT_VALUE:
                row["value"] = device.objects[key]
            elif prop == STATUS_FLAGS:
                row["value"] = device.flags.get(key, [False, False, False, False])
            else:
                row["error"] = {"class": "property", "code": "unknown-property"}
            values.append(row)
        return {"id": request_id, "ok": True, "values": values}
