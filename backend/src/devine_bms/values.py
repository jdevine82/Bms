"""Live values with quality, and the in-memory cache (main spec 5.2.1, milestone 2 spec 4 and 6).

Every value carries a status. Haystack `curStatus` names are used directly:
`ok` is Good, `stale` is Stale, `fault` and `down` are Bad, and `disabled`
marks points or devices switched off.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Status(StrEnum):
    OK = "ok"
    STALE = "stale"
    FAULT = "fault"
    DOWN = "down"
    DISABLED = "disabled"
    UNKNOWN = "unknown"  # not read yet since start-up


GOOD = {Status.OK}


@dataclass(frozen=True)
class LiveValue:
    point_id: uuid.UUID
    value: float | bool | str | None
    status: Status
    ts: datetime  # when this value or status was set (UTC)
    reason: str | None = None
    last_good_ts: datetime | None = None  # when the last Good value was read


@dataclass(frozen=True)
class HisRow:
    point_id: uuid.UUID
    ts: datetime
    num: float | None
    str_: str | None
    status: Status
    reason: str | None


@dataclass
class ValueCache:
    """Current value of every point, with change notifications for live clients."""

    values: dict[uuid.UUID, LiveValue] = field(default_factory=dict)
    _subscribers: set[asyncio.Queue] = field(default_factory=set)

    def get(self, point_id: uuid.UUID) -> LiveValue | None:
        return self.values.get(point_id)

    def set(self, live: LiveValue) -> None:
        self.values[live.point_id] = live
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(live)
            except asyncio.QueueFull:
                pass  # a slow client misses intermediate values; it still gets later ones

    def subscribe(self, maxsize: int = 1000) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
