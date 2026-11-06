"""The site model the runtime and API use, loaded from storage (milestone 2 spec 6 and 7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from devine_bms.config.model import (
    OfflineAlarm,
    PointAlarm,
    WriteLimits,
    POLL_INTERVAL_S,
    BacnetAddress,
    BacnetIpConnection,
    BacnetMstpConnection,
    BacnetMstpTrunk,
    MqttAddress,
    MqttBroker,
    MqttConnection,
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
    settings: ModbusRtuBus | ModbusRtuOverTcpBus | ModbusTcpGatewayBus | BacnetMstpTrunk | MqttBroker

    @property
    def name(self) -> str:
        return self.settings.name


@dataclass(frozen=True)
class RuntimeDevice:
    id: uuid.UUID
    name: str
    protocol: str
    connection: (
        ModbusTcpConnection | ModbusBusConnection | BacnetIpConnection | BacnetMstpConnection | MqttConnection
    )
    enabled: bool
    bus_id: uuid.UUID | None = None
    dis: str = ""
    offline_alarm: OfflineAlarm | None = None  # milestone 6B

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
    address: ModbusAddress | BacnetAddress | MqttAddress
    poll_class: PollClass
    stale_after_s: float | None
    his: bool
    # Descriptive fields for the API; the poller does not use them.
    dis: str = ""
    point_type: str = "sensor"
    equip_id: uuid.UUID | None = None
    space_id: uuid.UUID | None = None
    tags: dict = field(default_factory=dict, hash=False)
    alarms: tuple[PointAlarm, ...] = ()  # milestone 6B
    write_limits: WriteLimits | None = None  # milestone 7A
    writable: bool = False  # a setpoint or command whose address can be written

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


def point_writable(point_type: str, address) -> bool:
    """Whether commands can be written (milestone 7A); the same rule as config.validate.writable."""
    if point_type not in ("sp", "cmd"):
        return False
    if isinstance(address, ModbusAddress):
        return address.object_type.value in ("coil", "holdingRegister")
    if isinstance(address, BacnetAddress):
        return not address.object_type.value.endswith("Input")
    return bool(getattr(address, "command_topic", None))


def _writable(p) -> bool:
    return point_writable(p.type.value, p.address)


def from_site_config(config) -> RuntimeConfig:
    """Runtime objects straight from a checked SiteConfig, with new IDs.

    Used by tests (in-memory storage) and by test reads of devices that are not saved yet.
    """
    import uuid as _uuid

    spaces = {s.name: SiteSpace(_uuid.uuid4(), s.name, s.dis, dict(s.tags)) for s in config.spaces}
    equips = {
        e.name: SiteEquip(_uuid.uuid4(), e.name, e.dis, spaces[e.space].id if e.space else None, None, dict(e.tags))
        for e in config.equips
    }
    buses = {b.name: RuntimeBus(_uuid.uuid4(), b) for b in config.buses}

    def bus_of(d):
        name = getattr(d.connection, "bus", None) or getattr(d.connection, "trunk", None) \
            or getattr(d.connection, "broker", None)
        return buses[name].id if name else None

    devices = {d.name: RuntimeDevice(_uuid.uuid4(), d.name, d.protocol, d.connection, d.enabled, bus_id=bus_of(d),
                                     dis=d.dis, offline_alarm=d.offline_alarm)
               for d in config.devices}
    points = tuple(
        RuntimePoint(
            id=_uuid.uuid4(), name=p.name, device_id=devices[p.device].id, kind=p.kind, unit=p.unit,
            enum=tuple(p.enum) if p.enum else None, address=p.address, poll_class=p.poll_class,
            stale_after_s=p.stale_after_s, his=p.his, dis=p.dis, point_type=p.type.value,
            equip_id=equips[p.equip].id if p.equip else None,
            space_id=spaces[p.space].id if p.space else None, tags=dict(p.tags), alarms=tuple(p.alarms),
            write_limits=p.write_limits, writable=_writable(p),
        )
        for p in config.points
    )
    return RuntimeConfig(
        _uuid.uuid4(), config.site.tz, tuple(devices.values()), points,
        site_name=config.site.name, site_dis=config.site.dis,
        spaces=tuple(spaces.values()), equips=tuple(equips.values()), buses=tuple(buses.values()),
        bacnet=config.bacnet,
    )
