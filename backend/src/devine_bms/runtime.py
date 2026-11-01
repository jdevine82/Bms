"""The running backend: pollers, value quality, the live cache and history (milestone 2 spec 6)."""

from __future__ import annotations

import asyncio
import logging

from devine_bms.config.model import POLL_INTERVAL_S, PollClass
from devine_bms.poller.device_poller import DevicePoller
from devine_bms.poller.history_writer import HistoryWriter
from devine_bms.poller.quality import QualityTracker
from devine_bms.runtime_model import RuntimeConfig
from devine_bms.storage.base import Storage
from devine_bms.values import ValueCache

log = logging.getLogger(__name__)


class Runtime:
    def __init__(
        self,
        storage: Storage,
        site_name: str,
        intervals: dict[PollClass, float] = POLL_INTERVAL_S,
        stale_check_s: float = 1.0,
        flush_interval_s: float = 1.0,
    ) -> None:
        self.storage = storage
        self.site_name = site_name
        self.intervals = intervals
        self.stale_check_s = stale_check_s
        self.cache = ValueCache()
        self.history = HistoryWriter(storage, flush_interval_s)
        self.config: RuntimeConfig | None = None
        self.tracker: QualityTracker | None = None
        self.pollers: list[DevicePoller] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self.config = await self.storage.load_runtime_config(self.site_name)
        self.tracker = QualityTracker(list(self.config.points), self.cache, self.history.add, self.intervals)
        for device in self.config.devices:
            if device.protocol != "modbus_tcp":
                log.warning("device %s: protocol %s is not supported yet", device.name, device.protocol)
                continue
            poller = DevicePoller(device, self.config.points_for(device.id), self.tracker, self.intervals)
            self.pollers.append(poller)
            self._tasks.append(asyncio.create_task(poller.run(), name=f"poll {device.name}"))
        self._tasks.append(asyncio.create_task(self._stale_loop(), name="stale check"))
        self._tasks.append(asyncio.create_task(self.history.run(), name="history writer"))
        log.info("runtime started: %d devices, %d points", len(self.pollers), len(self.config.points))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.history.flush()
        log.info("runtime stopped")

    async def _stale_loop(self) -> None:
        while True:
            await asyncio.sleep(self.stale_check_s)
            self.tracker.check_stale()
