"""Poll the devices on one connection by speed class (main spec 5.1.4, milestone 2 spec 6, 4a, 4b).

A BusPoller owns one connection through a driver: a Modbus TCP device's own connection,
an RS485 line, a serial gateway, or one BACnet/IP device. Requests on one connection go
one at a time, because a serial line (and the serial side of a gateway) carries one
request at a time. The driver reads a block of points; this module does the schedule,
back-off and value quality for every protocol.

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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from devine_bms.config.model import POLL_INTERVAL_S, PollClass
from devine_bms.drivers.errors import BusDown, DeviceDown, DeviceFault
from devine_bms.drivers.modbus.client import ModbusClient
from devine_bms.drivers.modbus.codec import DecodeError
from devine_bms.poller.blocks import Block, make_blocks
from devine_bms.poller.quality import QualityTracker, point_value
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint
from devine_bms.values import Status

log = logging.getLogger(__name__)

BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 30.0


@dataclass(frozen=True)
class PointError:
    """One point in a block could not be read; the others may be fine."""

    status: Status
    reason: str


class AnyBlock(Protocol):
    poll_class: PollClass
    points: tuple[RuntimePoint, ...]


class Driver(Protocol):
    name: str
    where: str
    shared: bool

    async def read_block(self, state: DeviceState, block: Any) -> Sequence[tuple[RuntimePoint, Any]]:
        """Values for the block's points: a value, or a PointError. Raises BusDown,
        DeviceDown or DeviceFault for the whole block."""

    def close(self) -> None: ...


class ModbusDriver:
    def __init__(self, client: ModbusClient) -> None:
        self.client = client
        self.name = client.name
        self.where = client.where
        self.shared = client.shared

    async def read_block(self, state: DeviceState, block: Block) -> list[tuple[RuntimePoint, Any]]:
        raw = await self.client.read(state.device.unit_id, block.object_type, block.start, block.count)
        results: list[tuple[RuntimePoint, Any]] = []
        for point in block.points:
            try:
                results.append((point, point_value(point, block, raw)))
            except DecodeError as exc:
                results.append((point, PointError(Status.FAULT, f"cannot decode: {exc}")))
        return results

    def close(self) -> None:
        self.client.close()


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

    def __init__(
        self,
        device: RuntimeDevice,
        points: list[RuntimePoint],
        tracker: QualityTracker,
        blocks: Sequence[AnyBlock] | None = None,
    ) -> None:
        self.device = device
        self.tracker = tracker
        self.blocks = list(blocks) if blocks is not None else make_blocks(points, device.block_size)
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

    def mark(self, block: AnyBlock, status: Status, reason: str) -> None:
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
        driver: Driver | ModbusClient,
        devices: list[DeviceState],
        intervals: dict[PollClass, float] = POLL_INTERVAL_S,
    ) -> None:
        self.name = name
        self.driver = ModbusDriver(driver) if isinstance(driver, ModbusClient) else driver
        self.devices = devices
        self.intervals = intervals
        self.online: bool | None = None
        self.backoff = Backoff()
        # Jobs that need this connection between polls: test reads now, commands later (milestone 7).
        self._jobs: asyncio.Queue = asyncio.Queue()

    async def submit(self, job, timeout_s: float = 60.0):
        """Run `job(driver)` on this connection between polls and return its result.

        A serial port is never opened twice: test reads and (later) commands queue here and
        run before the next poll, so they go ahead of polling (main spec 5.1.4).
        """
        future = asyncio.get_running_loop().create_future()
        await self._jobs.put((job, future))
        return await asyncio.wait_for(future, timeout_s)

    async def _run_jobs(self) -> None:
        while not self._jobs.empty():
            job, future = self._jobs.get_nowait()
            if future.done():
                continue  # the caller gave up waiting
            try:
                future.set_result(await job(self.driver))
            except Exception as exc:  # the caller gets the error; the poller carries on
                if not future.done():
                    future.set_exception(exc)

    async def _wait(self, seconds: float) -> None:
        """Sleep until the next poll is due, but run queued jobs at once when they arrive."""
        if seconds <= 0:
            return
        try:
            job = await asyncio.wait_for(self._jobs.get(), seconds)
        except TimeoutError:
            return
        await self._jobs.put(job)
        await self._run_jobs()

    async def run(self) -> None:
        schedule = {(d, c): time.monotonic() for d in self.devices for c in d.classes()}
        try:
            while True:
                await self._run_jobs()
                if not schedule:
                    await self._wait(3600)  # nothing to poll yet; still serve jobs
                    continue
                wait = min(schedule.values()) - time.monotonic()
                if wait > 0:
                    await self._wait(wait)
                    if min(schedule.values()) > time.monotonic():
                        continue
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
            self.driver.close()

    async def poll(self, state: DeviceState, poll_class: PollClass) -> None:
        if self.backoff.waiting() or state.backoff.waiting():
            return  # waiting before the next attempt; the points are already marked down
        for block in (b for b in state.blocks if b.poll_class == poll_class):
            try:
                results = await self.driver.read_block(state, block)
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
            for point, outcome in results:
                if isinstance(outcome, PointError):
                    state.tracker.bad(point, outcome.status, outcome.reason)
                else:
                    state.tracker.good(point, outcome)

    def _bus_down(self, reason: str) -> None:
        if self.online is not False and self.driver.shared:
            log.warning("bus %s is down: %s", self.name, reason)
        self.online = False
        self.backoff.failed()
        for state in self.devices:
            state.went_down(reason)
            state.mark_all(Status.DOWN, reason)

    def _bus_up(self) -> None:
        if self.online is False and self.driver.shared:
            log.info("bus %s is back", self.name)
        self.online = True
        self.backoff.ok()
