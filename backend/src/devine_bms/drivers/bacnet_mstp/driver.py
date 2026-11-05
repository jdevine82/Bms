"""Read BACnet MS/TP points through the bms-mstp service (milestone 4c).

One trunk is one connection: a BusPoller sends one request at a time for every device
on it, as the service and the trunk handle one request at a time. Points read their
present value and status flags, as on BACnet/IP (4b), and value quality is the same:
an object error or the fault or out-of-service flag makes that point Bad (fault).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from devine_bms.drivers.bacnet.driver import BacnetBlock, convert, quality_from_flags
from devine_bms.drivers.bacnet_mstp.client import (
    OBJECT_TYPE_NUMBERS,
    PRESENT_VALUE,
    STATUS_FLAGS,
    MstpServiceClient,
)
from devine_bms.poller.device_poller import DeviceState, PointError
from devine_bms.runtime_model import RuntimePoint
from devine_bms.values import Status

log = logging.getLogger(__name__)
STATS_EVERY_S = 30.0


def refs_for(point: RuntimePoint) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    number = OBJECT_TYPE_NUMBERS[point.address.object_type.value]
    return (number, point.address.instance, PRESENT_VALUE), (number, point.address.instance, STATUS_FLAGS)


def outcome(point: RuntimePoint, value: dict | None, flags: dict | None) -> Any:
    if value is None:
        return PointError(Status.FAULT, "no value returned")
    if "error" in value:
        err = value["error"]
        return PointError(Status.FAULT, f"BACnet error {err.get('class')}: {err.get('code')}")
    flag_values = flags.get("value") if flags and "value" in flags else None
    if isinstance(flag_values, list) and len(flag_values) >= 4:
        flag_error = quality_from_flags(flag_values)
        if flag_error:
            return flag_error
    try:
        return convert(point, value["value"])
    except (TypeError, ValueError) as exc:
        return PointError(Status.FAULT, f"cannot convert: {exc}")


class MstpDriver:
    shared = True  # every device on the trunk goes through the one service

    def __init__(self, name: str, client: MstpServiceClient, port: str) -> None:
        self.name = name
        self.client = client
        self.where = f"MS/TP {port} via bms-mstp at {client.host}:{client.port}"
        self.stats: dict[str, Any] | None = None

    async def read_block(self, state: DeviceState, block: BacnetBlock) -> list[tuple[RuntimePoint, Any]]:
        refs: list[tuple[int, int, int]] = []
        for point in block.points:
            refs.extend(refs_for(point))
        rows = await self.client.read(state.device.connection.mac, refs)
        found = {(r["type"], r["instance"], r["property"]): r for r in rows}
        results = []
        for point in block.points:
            value_ref, flags_ref = refs_for(point)
            results.append((point, outcome(point, found.get(value_ref), found.get(flags_ref))))
        return results

    async def watch_stats(self) -> None:
        """Keep the trunk's counters (frames, lost tokens, CRC errors) for the health page."""
        while True:
            try:
                self.stats = await self.client.stats()
            except Exception as exc:  # the poller reports the trunk as down; keep trying
                log.debug("MS/TP stats from %s failed: %s", self.name, exc)
                self.stats = None
            await asyncio.sleep(STATS_EVERY_S)

    def close(self) -> None:
        self.client.close()
