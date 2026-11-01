"""The devices and points the runtime polls, loaded from storage (milestone 2 spec 6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from devine_bms.config.model import (
    POLL_INTERVAL_S,
    Kind,
    ModbusAddress,
    ModbusTcpConnection,
    PollClass,
)

STALE_FACTOR = 3  # Stale after 3 times the poll interval by default (main spec 5.2.1)


@dataclass(frozen=True)
class RuntimeDevice:
    id: uuid.UUID
    name: str
    protocol: str
    connection: ModbusTcpConnection
    enabled: bool


@dataclass(frozen=True)
class RuntimePoint:
    id: uuid.UUID
    name: str
    device_id: uuid.UUID
    kind: Kind
    unit: str | None
    enum: tuple[str, ...] | None
    address: ModbusAddress
    poll_class: PollClass
    stale_after_s: float | None
    his: bool

    def stale_after(self, intervals: dict[PollClass, float] = POLL_INTERVAL_S) -> float:
        if self.stale_after_s is not None:
            return self.stale_after_s
        return STALE_FACTOR * intervals[self.poll_class]


@dataclass(frozen=True)
class RuntimeConfig:
    site_id: uuid.UUID
    site_tz: str
    devices: tuple[RuntimeDevice, ...]
    points: tuple[RuntimePoint, ...]

    def points_for(self, device_id: uuid.UUID) -> list[RuntimePoint]:
        return [p for p in self.points if p.device_id == device_id]
