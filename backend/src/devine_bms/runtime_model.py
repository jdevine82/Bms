"""The site model the runtime and API use, loaded from storage (milestone 2 spec 6 and 7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from devine_bms.config.model import (
    POLL_INTERVAL_S,
    BacnetAddress,
    BacnetIpConnection,
    BacnetMstpConnection,
    BacnetMstpTrunk,
    BacnetSettings,
    Kind,
    ModbusAddress,
    ModbusBusConnection,
    ModbusRtuBus,
    ModbusRtuOverTcpBus,
    ModbusTcpConnection,
    ModbusTcpGatewayBus,
    PollClass,
)

STALE_FACTOR = 3  # Stale after 3 times the poll interval by default (main spec 5.2.1)


@dataclass(frozen=True)
class SiteSpace:
    id: uuid.UUID
    name: str
    dis: str
    tags: dict = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class SiteEquip:
    id: uuid.UUID
    name: str
    dis: str
    space_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    tags: dict = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class RuntimeBus:
    id: uuid.UUID
    settings: ModbusRtuBus | ModbusRtuOverTcpBus | ModbusTcpGatewayBus | BacnetMstpTrunk

    @property
    def name(self) -> str:
        return self.settings.name


@dataclass(frozen=True)
class RuntimeDevice:
    id: uuid.UUID
    name: str
    protocol: str
    connection: ModbusTcpConnection | ModbusBusConnection | BacnetIpConnection | BacnetMstpConnection
    enabled: bool
    bus_id: uuid.UUID | None = None

    @property
    def unit_id(self) -> int:
        return self.connection.unit_id

    @property
    def block_size(self) -> int:
        return self.connection.block_size


@dataclass(frozen=True)
class RuntimePoint:
    id: uuid.UUID
    name: str
    device_id: uuid.UUID
    kind: Kind
    unit: str | None
    enum: tuple[str, ...] | None
    address: ModbusAddress | BacnetAddress
    poll_class: PollClass
    stale_after_s: float | None
    his: bool
    # Descriptive fields for the API; the poller does not use them.
    dis: str = ""
    point_type: str = "sensor"
    equip_id: uuid.UUID | None = None
    space_id: uuid.UUID | None = None
    tags: dict = field(default_factory=dict, hash=False)

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
    site_name: str = ""
    site_dis: str = ""
    spaces: tuple[SiteSpace, ...] = ()
    equips: tuple[SiteEquip, ...] = ()
    buses: tuple[RuntimeBus, ...] = ()
    bacnet: BacnetSettings | None = None

    def points_for(self, device_id: uuid.UUID) -> list[RuntimePoint]:
        return [p for p in self.points if p.device_id == device_id]
