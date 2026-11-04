"""Load and check a site configuration file (milestone 2 spec section 5).

Checks that pydantic cannot do alone: unique names, references between
entities, "part of" loops and depth, address rules, Haystack tag rules and the
point naming pattern. Errors stop the load; warnings are reported and allowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from devine_bms.config.model import BacnetAddress, Kind, ModbusAddress, ObjectType, PointType, SiteConfig
from devine_bms.drivers.modbus.codec import DataType
from devine_bms.haystack.library import TagLibrary, load_library

MAX_EQUIP_DEPTH = 3  # main spec 5.2 (proposed)
POINT_TYPE_MARKERS = {"sensor", "sp", "cmd"}
# Tags the BMS sets itself from dedicated fields; they must not be written in `tags`.
RESERVED_TAGS = {"id", "dis", "point", "equip", "space", "site", "siteRef", "equipRef",
                 "spaceRef", "kind", "unit", "enum", "his", "curVal", "curStatus", "devineName"}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.where}: {self.message}"


class ConfigError(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))


def load_config(path: Path | str, library: TagLibrary | None = None) -> tuple[SiteConfig, list[Issue]]:
    """Read, parse and check a config file. Raises ConfigError if there are errors."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_config(json.loads(text), library)


def parse_config(data: dict, library: TagLibrary | None = None) -> tuple[SiteConfig, list[Issue]]:
    try:
        config = SiteConfig.model_validate(data)
    except ValidationError as exc:
        issues = [
            Issue(Severity.ERROR, ".".join(str(p) for p in err["loc"]) or "file", err["msg"])
            for err in exc.errors()
        ]
        raise ConfigError(issues) from exc
    issues = check_config(config, library or load_library())
    errors = [i for i in issues if i.severity == Severity.ERROR]
    if errors:
        raise ConfigError(errors)
    return config, issues


def check_config(config: SiteConfig, library: TagLibrary) -> list[Issue]:
    issues: list[Issue] = []

    def error(where: str, message: str) -> None:
        issues.append(Issue(Severity.ERROR, where, message))

    def warning(where: str, message: str) -> None:
        issues.append(Issue(Severity.WARNING, where, message))

    # Unique names in each collection. Point names must be unique on the site (5.2).
    for label, items in (("spaces", config.spaces), ("equips", config.equips), ("buses", config.buses),
                         ("devices", config.devices), ("points", config.points)):
        seen: set[str] = set()
        for item in items:
            if item.name in seen:
                error(f"{label}.{item.name}", "duplicate name")
            seen.add(item.name)

    # Buses: a serial port is never shared between buses (main spec 5.1).
    ports: dict[str, str] = {}
    for bus in config.buses:
        if bus.type == "modbus_rtu":
            if bus.port in ports:
                error(f"buses.{bus.name}", f"serial port {bus.port} is already used by bus '{ports[bus.port]}'")
            ports[bus.port] = bus.name
    buses = {b.name: b for b in config.buses}

    # Devices on a bus: the bus exists, and no two devices share a unit ID on it (main spec 5.1:
    # many units ship with the same default ID, so a clash must be caught).
    used: dict[tuple[str, int], str] = {}
    for device in config.devices:
        if device.protocol != "modbus":
            continue
        where = f"devices.{device.name}"
        conn = device.connection
        if conn.bus not in buses:
            error(where, f"bus '{conn.bus}' does not exist")
            continue
        key = (conn.bus, conn.unit_id)
        if key in used:
            error(where, f"unit ID {conn.unit_id} on bus '{conn.bus}' is already used by device '{used[key]}'")
        used[key] = device.name

    device_by_name = {d.name: d for d in config.devices}

    # BACnet: the BMS needs its own identity, and device instances are unique on a network.
    bacnet_devices = [d for d in config.devices if d.protocol == "bacnet_ip"]
    if bacnet_devices and config.bacnet is None:
        error("bacnet", "BACnet/IP devices need the site's 'bacnet' settings (the BMS's own address and device instance)")
    instances: dict[int, str] = {}
    if config.bacnet is not None:
        instances[config.bacnet.device_instance] = "the BMS itself"
    for device in bacnet_devices:
        number = device.connection.device_instance
        if number in instances:
            error(f"devices.{device.name}", f"device instance {number} is already used by {instances[number]}")
        instances[number] = f"device '{device.name}'"

    spaces = {s.name for s in config.spaces}
    equips = {e.name: e for e in config.equips}
    devices = {d.name for d in config.devices}

    # Equipment: space and parent references, loops and depth.
    for equip in config.equips:
        where = f"equips.{equip.name}"
        if equip.space and equip.space not in spaces:
            error(where, f"space '{equip.space}' does not exist")
        if equip.parent and equip.parent not in equips:
            error(where, f"parent '{equip.parent}' does not exist")
    for equip in config.equips:
        chain = [equip.name]
        current = equip
        while current.parent and current.parent in equips:
            if current.parent in chain:
                error(f"equips.{equip.name}", "'part of' links form a loop: " + " > ".join(chain + [current.parent]))
                break
            chain.append(current.parent)
            current = equips[current.parent]
        else:
            if len(chain) > MAX_EQUIP_DEPTH:
                error(f"equips.{equip.name}", f"nested more than {MAX_EQUIP_DEPTH} levels deep")

    # Feeds: from must be equipment; to may be equipment or a space.
    for i, feed in enumerate(config.feeds):
        where = f"feeds[{i}]"
        if feed.from_ not in equips:
            error(where, f"'from' equipment '{feed.from_}' does not exist")
        if feed.to not in equips and feed.to not in spaces:
            error(where, f"'to' '{feed.to}' is not an equipment or space")
        if feed.from_ == feed.to:
            error(where, "an item cannot feed itself")

    # Tags on site, spaces and equipment.
    for where, tags in ([("site", config.site.tags)]
                        + [(f"spaces.{s.name}", s.tags) for s in config.spaces]
                        + [(f"equips.{e.name}", e.tags) for e in config.equips]):
        _check_tags(where, tags, library, error, warning)
    for equip in config.equips:
        if not any(library.source_of(t) != "custom" and t not in {"equip"} for t in equip.tags):
            warning(f"equips.{equip.name}", "has no Haystack equipment type tag (for example ahu, fcu, meter)")

    # Points.
    for point in config.points:
        where = f"points.{point.name}"
        if point.device not in devices:
            error(where, f"device '{point.device}' does not exist")
        if point.equip and point.equip not in equips:
            error(where, f"equip '{point.equip}' does not exist")
        if point.space and point.space not in spaces:
            error(where, f"space '{point.space}' does not exist")
        if not point.equip and not point.space:
            error(where, "a point must belong to an equipment or a space (5.2)")

        _check_tags(where, point.tags, library, error, warning)
        markers = POINT_TYPE_MARKERS & set(point.tags)
        if markers:
            error(where, f"do not tag {sorted(markers)}; the point type comes from 'type' ({point.type})")

        device = device_by_name.get(point.device)
        if device is not None:
            is_bacnet_device = device.protocol == "bacnet_ip"
            if is_bacnet_device and not isinstance(point.address, BacnetAddress):
                error(where, f"device '{point.device}' is BACnet, so the point needs a BACnet address")
            if not is_bacnet_device and not isinstance(point.address, ModbusAddress):
                error(where, f"device '{point.device}' is Modbus, so the point needs a Modbus address")

        if point.kind == Kind.STR and not point.enum:
            error(where, "Str points need an 'enum' list")
        if isinstance(point.address, ModbusAddress):
            _check_modbus_point(where, point, error)
        else:
            _check_bacnet_point(where, point, error)

        prefix = _name_prefix(point, equips)
        if prefix and not point.name.startswith(prefix):
            warning(where, f"name does not follow <room>_<equipment>_<function>; expected it to start '{prefix}'")

    return issues


def _check_modbus_point(where, point, error) -> None:
    is_bit_object = point.address.object_type in (ObjectType.COIL, ObjectType.DISCRETE_INPUT)
    is_bit_type = point.address.data_type == DataType.BIT
    if is_bit_object != is_bit_type:
        error(where, "coils and discrete inputs use data type 'bit', and registers must not")
    if point.kind == Kind.BOOL and not is_bit_type:
        error(where, "Bool points must use a coil or discrete input in this milestone")
    if point.kind == Kind.NUMBER and is_bit_type:
        error(where, "Number points cannot use data type 'bit'")
    if point.type != PointType.SENSOR and point.address.object_type in (
            ObjectType.DISCRETE_INPUT, ObjectType.INPUT_REGISTER):
        error(where, f"a {point.type} point must be writable, but {point.address.object_type} is read-only")


def _check_bacnet_point(where, point, error) -> None:
    family = point.address.object_type.value
    if family.startswith("analog") and point.kind != Kind.NUMBER:
        error(where, f"{family} objects are Number points")
    if family.startswith("binary") and point.kind != Kind.BOOL:
        error(where, f"{family} objects are Bool points")
    if family.startswith("multiState") and point.kind == Kind.BOOL:
        error(where, f"{family} objects are Str points (with an enum list) or Number points (the state number)")
    if point.type != PointType.SENSOR and family.endswith("Input"):
        error(where, f"a {point.type} point must be writable, but a BACnet {family} is read-only")


def _check_tags(where, tags, library, error, warning) -> None:
    reserved = RESERVED_TAGS & set(tags)
    if reserved:
        error(where, f"tags {sorted(reserved)} are set by the BMS and must not be in 'tags'")
    custom = library.custom_tags(tags)
    if custom:
        warning(where, f"custom tags (not Haystack 4.0.0 or house): {custom}")


def _name_prefix(point, equips) -> str | None:
    """The expected start of a point name: '<room>_<equipment>_' (5.2)."""
    if point.equip and point.equip in equips:
        room = equips[point.equip].space or point.space or "site"
        return f"{room}_{point.equip}_"
    if point.space and not point.equip:
        return f"{point.space}_"
    return None
