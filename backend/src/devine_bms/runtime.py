"""The running backend: pollers, value quality, the live cache and history (milestone 2 spec 6)."""

from __future__ import annotations

import asyncio
import logging

from devine_bms.config.model import POLL_INTERVAL_S, PollClass
from devine_bms.drivers.modbus.client import ModbusClient
from devine_bms.poller.device_poller import BusPoller, DeviceState
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
        self.pollers: list[DeviceState] = []  # one per device, for health reporting
        self.buses: list[BusPoller] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self.config = await self.storage.load_runtime_config(self.site_name)
        self.tracker = QualityTracker(list(self.config.points), self.cache, self.history.add, self.intervals)
        groups: list[tuple[str, ModbusClient, list]] = []
        for bus in self.config.buses:
            members = [d for d in self.config.devices if d.bus_id == bus.id]
            if members:
                groups.append((bus.name, ModbusClient.for_bus(bus.settings), members))
        for device in self.config.devices:
            if device.protocol == "modbus_tcp":
                groups.append((device.name, ModbusClient.for_tcp_device(device.name, device.connection), [device]))
            elif device.protocol != "modbus":
                log.warning("device %s: protocol %s is not supported yet", device.name, device.protocol)
        for name, client, members in groups:
            states = [DeviceState(d, self.config.points_for(d.id), self.tracker) for d in members]
            self.pollers.extend(states)
            bus = BusPoller(name, client, states, self.intervals)
            self.buses.append(bus)
            self._tasks.append(asyncio.create_task(bus.run(), name=f"poll {name}"))
        self._tasks.append(asyncio.create_task(self._stale_loop(), name="stale check"))
        self._tasks.append(asyncio.create_task(self.history.run(), name="history writer"))
        log.info("runtime started: %d connections, %d devices, %d points",
                 len(self.buses), len(self.pollers), len(self.config.points))

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
