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
        self.bacnet = None  # BacnetLink when the site has BACnet/IP devices
        self.cov: list = []  # CovSubscriber per BACnet device with COV
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
            elif device.protocol not in ("modbus", "bacnet_ip"):
                log.warning("device %s: protocol %s is not supported yet", device.name, device.protocol)
        for name, client, members in groups:
            states = [DeviceState(d, self.config.points_for(d.id), self.tracker) for d in members]
            self.pollers.extend(states)
            bus = BusPoller(name, client, states, self.intervals)
            self.buses.append(bus)
            self._tasks.append(asyncio.create_task(bus.run(), name=f"poll {name}"))
        await self._start_bacnet()
        self._tasks.append(asyncio.create_task(self._stale_loop(), name="stale check"))
        self._tasks.append(asyncio.create_task(self.history.run(), name="history writer"))
        log.info("runtime started: %d connections, %d devices, %d points",
                 len(self.buses), len(self.pollers), len(self.config.points))

    async def _start_bacnet(self) -> None:
        devices = [d for d in self.config.devices if d.protocol == "bacnet_ip"]
        if not devices:
            return
        from devine_bms.drivers.bacnet.cov import CovSubscriber
        from devine_bms.drivers.bacnet.driver import BacnetDriver, make_bacnet_blocks
        from devine_bms.drivers.bacnet.link import BacnetLink

        if self.config.bacnet is None:
            log.error("BACnet/IP devices are set up but the site has no 'bacnet' settings; they are not polled")
            return
        self.bacnet = BacnetLink(self.config.bacnet)
        await self.bacnet.start()
        slow = self.intervals[PollClass.SLOW]
        for device in devices:
            points = self.config.points_for(device.id)
            driver = BacnetDriver(self.bacnet, device)
            driver.backup_interval_s = slow
            blocks = make_bacnet_blocks(points, device.block_size)
            state = DeviceState(device, points, self.tracker, blocks)
            self.pollers.append(state)
            poller = BusPoller(device.name, driver, [state], self.intervals)
            self.buses.append(poller)
            self._tasks.append(asyncio.create_task(poller.run(), name=f"poll {device.name}"))
            if device.connection.cov:
                cov = CovSubscriber(self.bacnet, device, points, self.tracker)
                cov.on_subscribed = self._cov_started(driver, slow)
                cov.on_failed = self._cov_stopped(driver)
                cov.start()
                self.cov.append(cov)

    def _cov_started(self, driver, slow: float):
        def started(point) -> None:
            driver.cov_active.add(point.id)
            self.tracker.stale_overrides[point.id] = 3 * slow  # COV reports only on a change
        return started

    def _cov_stopped(self, driver):
        def stopped(point) -> None:
            driver.cov_active.discard(point.id)
            self.tracker.stale_overrides.pop(point.id, None)
        return stopped

    async def stop(self) -> None:
        for cov in self.cov:
            await cov.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self.bacnet is not None:
            self.bacnet.close()
        await self.history.flush()
        log.info("runtime stopped")

    async def _stale_loop(self) -> None:
        while True:
            await asyncio.sleep(self.stale_check_s)
            self.tracker.check_stale()
