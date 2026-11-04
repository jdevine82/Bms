"""Poll one device by speed class (main spec 5.1.4, milestone 2 spec 6)."""

from __future__ import annotations

import asyncio
import logging
import time

from devine_bms.config.model import POLL_INTERVAL_S, PollClass
from devine_bms.drivers.modbus.client import DeviceDown, DeviceFault, ModbusTcpDevice
from devine_bms.drivers.modbus.codec import DecodeError
from devine_bms.poller.blocks import Block, make_blocks
from devine_bms.poller.quality import QualityTracker, point_value
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint
from devine_bms.values import Status

log = logging.getLogger(__name__)

BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 30.0


class DevicePoller:
    def __init__(
        self,
        device: RuntimeDevice,
        points: list[RuntimePoint],
        tracker: QualityTracker,
        intervals: dict[PollClass, float] = POLL_INTERVAL_S,
        client: ModbusTcpDevice | None = None,
    ) -> None:
        self.device = device
        self.tracker = tracker
        self.intervals = intervals
        self.client = client or ModbusTcpDevice(device.name, device.connection)
        self.blocks = make_blocks(points, device.connection.block_size)
        self.online: bool | None = None  # None until the first attempt
        self.polls = 0
        self.failures = 0
        self._backoff = BACKOFF_START_S
        self._retry_at = 0.0
        self._recovered = False

    def classes(self) -> list[PollClass]:
        return sorted({b.poll_class for b in self.blocks})

    async def run(self) -> None:
        classes = self.classes()
        if not classes:
            return
        next_due = {c: time.monotonic() for c in classes}
        try:
            while True:
                due_at = min(next_due.values())
                wait = due_at - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                now = time.monotonic()
                for poll_class in classes:
                    if next_due[poll_class] <= now:
                        await self.poll_class(poll_class)
                        interval = self.intervals[poll_class]
                        next_due[poll_class] += interval
                        if next_due[poll_class] < time.monotonic():  # fell behind; do not burst
                            next_due[poll_class] = time.monotonic() + interval
                    if self._recovered:
                        # The device has just come back: read every class now, so Normal and
                        # Slow points do not show "down" until their next scheduled poll.
                        self._recovered = False
                        for other in classes:
                            next_due[other] = min(next_due[other], time.monotonic())
        finally:
            self.client.close()

    async def poll_class(self, poll_class: PollClass) -> None:
        blocks = [b for b in self.blocks if b.poll_class == poll_class]
        if time.monotonic() < self._retry_at:
            return  # waiting before the next connection attempt; points are already marked down
        for block in blocks:
            try:
                raw = await self.client.read(block.object_type, block.start, block.count)
            except DeviceDown as exc:
                # The whole device is unreachable, so every point on it is down,
                # not only the speed class being polled now.
                self._went_down(str(exc))
                for any_block in self.blocks:
                    self._mark(any_block, Status.DOWN, str(exc))
                return
            except DeviceFault as exc:
                self._came_up()
                self.failures += 1
                self._mark(block, Status.FAULT, str(exc))
                continue
            self._came_up()
            self.polls += 1
            for point in block.points:
                try:
                    self.tracker.good(point, point_value(point, block, raw))
                except DecodeError as exc:
                    self.tracker.bad(point, Status.FAULT, f"cannot decode: {exc}")

    def _mark(self, block: Block, status: Status, reason: str) -> None:
        for point in block.points:
            self.tracker.bad(point, status, reason)

    def _went_down(self, reason: str) -> None:
        self.failures += 1
        if self.online is not False:
            log.warning("device %s is down: %s", self.device.name, reason)
        self.online = False
        self._retry_at = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, BACKOFF_MAX_S)

    def _came_up(self) -> None:
        if self.online is False:
            self._recovered = True
        if self.online is not True:
            log.info("device %s is answering", self.device.name)
        self.online = True
        self._backoff = BACKOFF_START_S
        self._retry_at = 0.0
