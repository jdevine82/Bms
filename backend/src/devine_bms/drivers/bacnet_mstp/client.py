"""Client for the bms-mstp service's local API (milestone 4c; mstp/bms-mstp/main.c).

Requests are text lines and answers JSON lines, matched by request ID:

    PING <id>
    STATS <id>
    WHOIS <id> <low> <high> <wait_ms>
    READ <id> <mac> <type>:<instance>:<property> [...]

Failures:
- BusDown: the service cannot be reached (it is not running, or the host is unreachable);
  every device on the trunk is down.
- DeviceDown: the service answered "no answer (timeout)" for the device.
- DeviceFault: the device answered the whole request with an error, abort or reject.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

from devine_bms.drivers.errors import BusDown, DeviceDown, DeviceFault

log = logging.getLogger(__name__)

# BACnet object type and property numbers (ASHRAE 135).
OBJECT_TYPE_NUMBERS = {
    "analogInput": 0,
    "analogOutput": 1,
    "analogValue": 2,
    "binaryInput": 3,
    "binaryOutput": 4,
    "binaryValue": 5,
    "multiStateInput": 13,
    "multiStateOutput": 14,
    "multiStateValue": 19,
}
PRESENT_VALUE = 85
STATUS_FLAGS = 111


class MstpServiceClient:
    def __init__(self, service: str, timeout_s: float, name: str = "mstp") -> None:
        host, port = service.rsplit(":", 1)
        self.host = host
        self.port = int(port)
        self.timeout_s = timeout_s
        self.name = name
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()  # one request at a time per connection

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout_s
            )
        except (OSError, TimeoutError) as exc:
            raise BusDown(f"bms-mstp service at {self.host}:{self.port} is not reachable: {exc}") from exc

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._reader = self._writer = None

    async def request(self, command: str, args: str = "", wait_s: float | None = None) -> dict[str, Any]:
        async with self._lock:
            if not self.connected:
                await self._connect()
            request_id = f"r{next(self._ids)}"
            line = f"{command} {request_id} {args}".rstrip() + "\n"
            try:
                self._writer.write(line.encode())
                await self._writer.drain()
                while True:
                    raw = await asyncio.wait_for(self._reader.readline(), wait_s or self.timeout_s)
                    if not raw:
                        raise ConnectionResetError("the service closed the connection")
                    answer = json.loads(raw)
                    if answer.get("id") == request_id:
                        return answer
                    # an answer to an earlier request that timed out here; skip it
            except TimeoutError as exc:
                # The service always answers, so silence means it is stuck or gone.
                self.close()
                raise BusDown(f"bms-mstp service did not answer within {wait_s or self.timeout_s:g} s") from exc
            except (OSError, ConnectionError, ValueError) as exc:
                self.close()
                raise BusDown(f"lost the bms-mstp service: {exc}") from exc

    async def ping(self) -> dict[str, Any]:
        return await self.request("PING")

    async def stats(self) -> dict[str, Any]:
        return await self.request("STATS")

    async def whois(self, low: int = -1, high: int = -1, wait_ms: int = 3000) -> list[dict[str, Any]]:
        answer = await self.request("WHOIS", f"{low} {high} {wait_ms}", wait_s=wait_ms / 1000 + self.timeout_s)
        if not answer.get("ok"):
            raise DeviceFault(answer.get("error", "Who-Is failed"))
        return answer["devices"]

    async def read(self, mac: int, refs: list[tuple[int, int, int]]) -> list[dict[str, Any]]:
        """ReadPropertyMultiple of (object type, instance, property) at a MAC address."""
        args = f"{mac} " + " ".join(f"{t}:{i}:{p}" for t, i, p in refs)
        answer = await self.request("READ", args)
        if answer.get("ok"):
            return answer["values"]
        error = answer.get("error", "unknown error")
        if "timeout" in error:
            raise DeviceDown(f"no answer from MAC {mac} ({error})")
        if error.startswith("busy"):
            raise DeviceDown(f"bms-mstp is busy: {error}")
        raise DeviceFault(error)
