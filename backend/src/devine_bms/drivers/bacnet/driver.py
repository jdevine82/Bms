"""Read BACnet/IP points (milestone 4b, main spec 5.1, 5.1.4 and 5.2.1).

Each point reads its present value and status flags. Points are read together with
ReadPropertyMultiple; if a device rejects that, it falls back to one ReadProperty per
property. Quality:
- no answer within the timeout and retries: the device is down;
- an error for one object (for example unknown-object): that point is Bad (fault);
- status flags `fault`: Bad (fault), the device reports a fault on that object;
- status flags `out-of-service`: Bad (fault). The value is not live: it can be written
  by hand on the device (main spec 20, H4). [Assumed]
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.basetypes import ErrorType
from bacpypes3.primitivedata import ObjectIdentifier

from devine_bms.config.model import BacnetAddress, Kind, PollClass
from devine_bms.drivers.bacnet.link import BacnetLink, device_address
from devine_bms.drivers.errors import DeviceDown
from devine_bms.poller.device_poller import DeviceState, PointError
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint
from devine_bms.values import Status

PRESENT_VALUE = "present-value"
STATUS_FLAGS = "status-flags"
FLAG_FAULT = 1
FLAG_OUT_OF_SERVICE = 3


def kebab(name: str) -> str:
    """analogInput -> analog-input (bacpypes3 names)."""
    return "".join(f"-{c.lower()}" if c.isupper() else c for c in name)


def object_id(address: BacnetAddress) -> ObjectIdentifier:
    # Always a real ObjectIdentifier: bacpypes3 matches COV notifications to subscriptions by
    # comparing object identifiers, and a plain string never matches.
    return ObjectIdentifier(f"{kebab(address.object_type.value)},{address.instance}")


@dataclass(frozen=True)
class BacnetBlock:
    poll_class: PollClass
    points: tuple[RuntimePoint, ...]


def make_bacnet_blocks(points: list[RuntimePoint], block_size: int) -> list[BacnetBlock]:
    """Points per ReadPropertyMultiple request: two properties each (value and status flags)."""
    per_request = max(1, block_size // 2)
    blocks = []
    for poll_class in sorted({p.poll_class for p in points}):
        members = [p for p in points if p.poll_class == poll_class]
        for i in range(0, len(members), per_request):
            blocks.append(BacnetBlock(poll_class, tuple(members[i : i + per_request])))
    return blocks


def convert(point: RuntimePoint, value: Any) -> float | bool | str:
    """A BACnet present value as the point's kind. Raises ValueError if it does not fit."""
    if point.kind == Kind.BOOL:
        return int(value) == 1  # BinaryPV: inactive 0, active 1
    if point.kind == Kind.STR:
        state = int(value)  # multi-state values are 1-based
        if not point.enum or not 1 <= state <= len(point.enum):
            raise ValueError(f"state {state} has no entry in the enum list")
        return point.enum[state - 1]
    return float(value)


def quality_from_flags(flags: Any) -> PointError | None:
    if flags is None or isinstance(flags, ErrorType):
        return None  # status flags are optional on some objects; the value still counts
    if flags[FLAG_FAULT]:
        return PointError(Status.FAULT, "the device reports a fault on this object")
    if flags[FLAG_OUT_OF_SERVICE]:
        return PointError(Status.FAULT, "out of service on the device; the value is not live")
    return None


def error_text(error: Any) -> str:
    if isinstance(error, ErrorType):
        return f"BACnet error {error.errorClass}: {error.errorCode}"
    return f"BACnet error: {error}"


def point_outcome(point: RuntimePoint, value: Any, flags: Any) -> Any:
    if value is None or isinstance(value, ErrorType):
        return PointError(Status.FAULT, error_text(value) if value is not None else "no value returned")
    flag_error = quality_from_flags(flags)
    if flag_error:
        return flag_error
    try:
        return convert(point, value)
    except (TypeError, ValueError) as exc:
        return PointError(Status.FAULT, f"cannot convert: {exc}")


class BacnetDriver:
    """Reads one BACnet/IP device. Used by a BusPoller as that device's connection."""

    shared = False

    def __init__(self, link: BacnetLink, device: RuntimeDevice) -> None:
        self.link = link
        self.device = device
        self.name = device.name
        conn = device.connection
        self.address = device_address(conn.address)
        self.where = f"BACnet/IP {conn.address} device {conn.device_instance}"
        self.timeout_s = conn.timeout_s
        self.attempts = conn.retries + 1
        self.use_rpm = True
        # Points with a working COV subscription are read only as a back-up, every Slow interval.
        self.cov_active: set = set()
        self.backup_interval_s = 300.0
        self._last_backup: dict = {}

    def due(self, block: BacnetBlock) -> list[RuntimePoint]:
        now = time.monotonic()
        points = []
        for p in block.points:
            if p.id in self.cov_active and now - self._last_backup.get(p.id, 0.0) < self.backup_interval_s:
                continue
            if p.id in self.cov_active:
                self._last_backup[p.id] = now
            points.append(p)
        return points

    async def _request(self, make_call):
        for attempt in range(self.attempts):
            try:
                return await asyncio.wait_for(make_call(), self.timeout_s)
            except TimeoutError:
                if attempt == self.attempts - 1:
                    raise DeviceDown(f"no answer within {self.timeout_s:g} s ({self.attempts} tries)") from None

    async def read_block(self, state: DeviceState, block: BacnetBlock) -> list[tuple[RuntimePoint, Any]]:
        app = self.link.app
        if app is None:
            raise DeviceDown("the BACnet link is not running")
        points = self.due(block)
        if not points:
            return []
        if self.use_rpm:
            request = []
            for point in points:
                request += [object_id(point.address), [PRESENT_VALUE, STATUS_FLAGS]]
            try:
                rows = await self._request(lambda: app.read_property_multiple(self.address, request))
            except ErrorRejectAbortNack:
                self.use_rpm = False  # the device does not do ReadPropertyMultiple; read one at a time
            else:
                found = {(str(objid), str(prop)): value for objid, prop, _index, value in rows}
                return [
                    (p, point_outcome(p, found.get((str(object_id(p.address)), PRESENT_VALUE)),
                                      found.get((str(object_id(p.address)), STATUS_FLAGS))))
                    for p in points
                ]
        results = []
        for point in points:
            objid = object_id(point.address)
            try:
                value = await self._request(lambda: app.read_property(self.address, objid, PRESENT_VALUE))
            except ErrorRejectAbortNack as exc:
                results.append((point, PointError(Status.FAULT, f"BACnet error: {exc}")))
                continue
            try:
                flags = await self._request(lambda: app.read_property(self.address, objid, STATUS_FLAGS))
            except ErrorRejectAbortNack:
                flags = None
            results.append((point, point_outcome(point, value, flags)))
        return results

    def close(self) -> None:
        pass  # the link is shared and closed by the runtime
