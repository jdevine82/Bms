"""Poll the devices on one connection by speed class (main spec 5.1.4, milestone 2 spec 6, 4a).

A BusPoller owns one Modbus connection: a single Modbus TCP device's own connection,
an RS485 line, or a serial gateway. Requests on one connection go one at a time,
because a serial line (and the serial side of a gateway) carries one request at a time.

Failures:
- the connection is down (BusDown): every point on every device of the connection is
  down, and the connection is retried with back-off;
- one device does not answer (DeviceDown): only that device's points are down, the
  other devices on the bus keep being polled, and that device is retried with back-off;
- a device answers with an exception (DeviceFault): only that block's points are fault.
When a device answers again, every speed class of it is read at once, so Normal and Slow
points do not keep showing "down" until their next scheduled poll.
"""

from __future__ import annotations

import asyncio
import logging
import time

from devine_bms.config.model import POLL_INTERVAL_S, PollClass
from devine_bms.drivers.modbus.client import BusDown, DeviceDown, DeviceFault, ModbusClient
from devine_bms.drivers.modbus.codec import DecodeError
from devine_bms.poller.blocks import Block, make_blocks
from devine_bms.poller.quality import QualityTracker, point_value
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint
from devine_bms.values import Status

log = logging.getLogger(__name__)

BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 30.0


class Backoff:
    def __init__(self) -> None:
        self.delay = BACKOFF_START_S
        self.retry_at = 0.0

    def failed(self) -> None:
        self.retry_at = time.monotonic() + self.delay
        self.delay = min(self.delay * 2, BACKOFF_MAX_S)

    def ok(self) -> None:
        self.delay = BACKOFF_START_S
        self.retry_at = 0.0

    def waiting(self) -> bool:
        return time.monotonic() < self.retry_at


class DeviceState:
    """One device's blocks, schedule and health. These are what the API reports as devices."""

    def __init__(self, device: RuntimeDevice, points: list[RuntimePoint], tracker: QualityTracker) -> None:
        self.device = device
        self.tracker = tracker
        self.blocks = make_blocks(points, device.block_size)
        self.online: bool | None = None  # None until the first attempt
        self.polls = 0
        self.failures = 0
        self.backoff = Backoff()
        self.recovered = False

    def classes(self) -> list[PollClass]:
        return sorted({b.poll_class for b in self.blocks})

    def mark_all(self, status: Status, reason: str) -> None:
        for block in self.blocks:
            self.mark(block, status, reason)

    def mark(self, block: Block, status: Status, reason: str) -> None:
        for point in block.points:
            self.tracker.bad(point, status, reason)

    def went_down(self, reason: str) -> None:
        self.failures += 1
        if self.online is not False:
            log.warning("device %s is down: %s", self.device.name, reason)
        self.online = False
        self.backoff.failed()

    def came_up(self) -> None:
        if self.online is False:
            self.recovered = True
        if self.online is not True:
            log.info("device %s is answering", self.device.name)
        self.online = True
        self.backoff.ok()


class BusPoller:
    def __init__(
        self,
        name: str,
        client: ModbusClient,
        devices: list[DeviceState],
        intervals: dict[PollClass, float] = POLL_INTERVAL_S,
    ) -> None:
        self.name = name
        self.client = client
        self.devices = devices
        self.intervals = intervals
        self.online: bool | None = None
        self.backoff = Backoff()

    async def run(self) -> None:
        schedule = {(d, c): time.monotonic() for d in self.devices for c in d.classes()}
        if not schedule:
            return
        try:
            while True:
                wait = min(schedule.values()) - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                now = time.monotonic()
                for (state, poll_class), due in sorted(schedule.items(), key=lambda item: item[1]):
                    if due > now:
                        continue
                    await self.poll(state, poll_class)
                    interval = self.intervals[poll_class]
                    schedule[(state, poll_class)] = max(due + interval, time.monotonic())
                    if schedule[(state, poll_class)] < time.monotonic():  # fell behind; do not burst
                        schedule[(state, poll_class)] = time.monotonic() + interval
                    if state.recovered:
                        state.recovered = False
                        for other in state.classes():
                            schedule[(state, other)] = min(schedule[(state, other)], time.monotonic())
        finally:
            self.client.close()

    async def poll(self, state: DeviceState, poll_class: PollClass) -> None:
        if self.backoff.waiting() or state.backoff.waiting():
            return  # waiting before the next attempt; the points are already marked down
        for block in (b for b in state.blocks if b.poll_class == poll_class):
            try:
                raw = await self.client.read(state.device.unit_id, block.object_type, block.start, block.count)
            except BusDown as exc:
                self._bus_down(str(exc))
                return
            except DeviceDown as exc:
                # This device is unreachable, so all its points are down, not only this class.
                self._bus_up()
                state.went_down(str(exc))
                state.mark_all(Status.DOWN, str(exc))
                return
            except DeviceFault as exc:
                self._bus_up()
                state.came_up()
                state.failures += 1
                state.mark(block, Status.FAULT, str(exc))
                continue
            self._bus_up()
            state.came_up()
            state.polls += 1
            for point in block.points:
                try:
                    state.tracker.good(point, point_value(point, block, raw))
                except DecodeError as exc:
                    state.tracker.bad(point, Status.FAULT, f"cannot decode: {exc}")

    def _bus_down(self, reason: str) -> None:
        if self.online is not False and self.client.shared:
            log.warning("bus %s is down: %s", self.name, reason)
        self.online = False
        self.backoff.failed()
        for state in self.devices:
            state.went_down(reason)
            state.mark_all(Status.DOWN, reason)

    def _bus_up(self) -> None:
        if self.online is False and self.client.shared:
            log.info("bus %s is back", self.name)
        self.online = True
        self.backoff.ok()
