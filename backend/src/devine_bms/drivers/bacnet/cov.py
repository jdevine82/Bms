"""BACnet change-of-value subscriptions (main spec 5.1.4, milestone 4b).

For a device with `cov: true`, each Fast and Normal point is subscribed to. A
notification updates the value straight away. The point is still polled, at the Slow
interval only, as a back-up in case a notification is missed; its stale time follows
the Slow interval too. If a subscription cannot be made (the device or object does not
support COV), the point is polled at its own class instead, and the health page says so.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from bacpypes3.apdu import ErrorRejectAbortNack

from devine_bms.config.model import PollClass
from devine_bms.drivers.bacnet.driver import PRESENT_VALUE, STATUS_FLAGS, object_id, point_outcome
from devine_bms.drivers.bacnet.link import BacnetLink, device_address
from devine_bms.poller.device_poller import PointError
from devine_bms.poller.quality import QualityTracker
from devine_bms.runtime_model import RuntimeDevice, RuntimePoint

log = logging.getLogger(__name__)
COV_CLASSES = {PollClass.FAST, PollClass.NORMAL}
RETRY_S = 60.0


@dataclass
class CovState:
    subscribed: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)
    notifications: int = 0


class CovSubscriber:
    def __init__(self, link: BacnetLink, device: RuntimeDevice, points: list[RuntimePoint],
                 tracker: QualityTracker) -> None:
        self.link = link
        self.device = device
        self.points = [p for p in points if p.poll_class in COV_CLASSES]
        self.tracker = tracker
        self.state = CovState()
        self._tasks: list[asyncio.Task] = []
        # Called when a point's subscription starts working, and when it stops working.
        self.on_subscribed = None
        self.on_failed = None

    def start(self) -> None:
        for point in self.points:
            self._tasks.append(asyncio.create_task(self._follow(point), name=f"cov {point.name}"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _follow(self, point: RuntimePoint) -> None:
        conn = self.device.connection
        address = device_address(conn.address)
        flags = None
        while True:
            app = self.link.app
            try:
                if app is None:
                    raise RuntimeError("the BACnet link is not running")
                manager = app.change_of_value(
                    address, object_id(point.address), self.link.next_process_id(), False, conn.cov_lifetime_s
                )
                # Subscribing waits for the device; a device that never answers would wait forever.
                async with asyncio.timeout(conn.timeout_s * (conn.retries + 1)):
                    subscription = await manager.__aenter__()
                try:
                    self.state.subscribed.add(point.name)
                    self.state.failed.pop(point.name, None)
                    if self.on_subscribed:
                        self.on_subscribed(point)
                    while True:
                        prop, value = await subscription.get_value()
                        self.state.notifications += 1
                        if str(prop) == STATUS_FLAGS:
                            flags = value
                            continue
                        if str(prop) != PRESENT_VALUE:
                            continue
                        outcome = point_outcome(point, value, flags)
                        if isinstance(outcome, PointError):
                            self.tracker.bad(point, outcome.status, outcome.reason)
                        else:
                            self.tracker.good(point, outcome)
                finally:
                    try:
                        await asyncio.wait_for(manager.__aexit__(None, None, None), conn.timeout_s)
                    except Exception:  # unsubscribing is best effort; the subscription expires anyway
                        pass
            except asyncio.CancelledError:
                raise
            except (ErrorRejectAbortNack, TimeoutError, OSError, RuntimeError, ValueError) as exc:
                self.state.subscribed.discard(point.name)
                first_failure = point.name not in self.state.failed
                self.state.failed[point.name] = str(exc) or type(exc).__name__
                if self.on_failed:
                    self.on_failed(point)
                if first_failure:
                    log.warning("COV for %s on %s is not available (%s); it is polled instead",
                                point.name, self.device.name, self.state.failed[point.name])
                await asyncio.sleep(RETRY_S)
