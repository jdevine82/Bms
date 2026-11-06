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

from devine_bms.config.model import (
    BacnetAddress,
    Kind,
    ModbusAddress,
    MqttAddress,
    ObjectType,
    PointType,
    SiteConfig,
)
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
        if bus.type in ("modbus_rtu", "bacnet_mstp"):
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
        if conn.bus not in buses or buses[conn.bus].type in ("bacnet_mstp", "mqtt_broker"):
            kinds = {"bacnet_mstp": "a BACnet MS/TP trunk", "mqtt_broker": "an MQTT broker"}
            error(where, f"bus '{conn.bus}' does not exist" if conn.bus not in buses
                  else f"bus '{conn.bus}' is {kinds[buses[conn.bus].type]}, not a Modbus bus")
            continue
        key = (conn.bus, conn.unit_id)
        if key in used:
            error(where, f"unit ID {conn.unit_id} on bus '{conn.bus}' is already used by device '{used[key]}'")
        used[key] = device.name

    # MQTT devices: the broker exists.
    for device in config.devices:
        if device.protocol == "mqtt":
            broker = buses.get(device.connection.broker)
            if broker is None or broker.type != "mqtt_broker":
                error(f"devices.{device.name}", f"MQTT broker '{device.connection.broker}' does not exist")

    # MS/TP devices: the trunk exists, MACs are unique on it, and within the trunk's Max_Master
    # (a master above Max_Master is never polled for the token).
    macs: dict[tuple[str, int], str] = {}
    for device in config.devices:
        if device.protocol != "bacnet_mstp":
            continue
        where = f"devices.{device.name}"
        conn = device.connection
        trunk = buses.get(conn.trunk)
        if trunk is None or trunk.type != "bacnet_mstp":
            error(where, f"MS/TP trunk '{conn.trunk}' does not exist")
            continue
        key = (conn.trunk, conn.mac)
        if key in macs:
            error(where, f"MAC {conn.mac} on trunk '{conn.trunk}' is already used by device '{macs[key]}'")
        macs[key] = device.name
        if conn.mac > trunk.max_master:
            warning(where, f"MAC {conn.mac} is above the trunk's Max_Master ({trunk.max_master}); "
                           "if it is a master it will never get the token")

    device_by_name = {d.name: d for d in config.devices}

    # BACnet network numbers and the MS/TP routers (milestone 8): unique on the site.
    trunks = [b for b in config.buses if b.type == "bacnet_mstp"]
    numbers: dict[int, str] = {}
    if config.bacnet is not None:
        numbers[config.bacnet.network_number] = "bacnet (the BACnet/IP network)"
    for t in trunks:
        where = f"buses.{t.name}"
        if t.network_number is not None:
            if t.network_number in numbers:
                error(where, f"network number {t.network_number} is already used by {numbers[t.network_number]}")
            numbers[t.network_number] = f"trunk {t.name}"
        if t.router is not None and t.router.enabled:
            if t.network_number is None:
                error(where, "a routing trunk needs its networkNumber")
            if config.bacnet is None:
                error(where, "routing needs the site's BACnet settings (bacnet, with networkNumber)")
            elif t.router.address == config.bacnet.local_address.split("/")[0]:
                error(where, "the router needs its own address, not the BMS's BACnet/IP address")
    addresses = [t.router.address for t in trunks if t.router is not None and t.router.enabled]
    if len(addresses) != len(set(addresses)):
        error("buses", "each routing trunk needs its own address")

    # BACnet: the BMS needs its own identity, and device instances are unique on a network.
    bacnet_devices = [d for d in config.devices if d.protocol in ("bacnet_ip", "bacnet_mstp")]
    if any(d.protocol == "bacnet_ip" for d in bacnet_devices) and config.bacnet is None:
        error("bacnet", "BACnet/IP devices need the site's 'bacnet' settings (the BMS's own address and device instance)")
    instances: dict[int, str] = {}
    if config.bacnet is not None:
        instances[config.bacnet.device_instance] = "the BMS itself"
    for bus in config.buses:
        if bus.type == "bacnet_mstp":
            if bus.device_instance in instances:
                error(f"buses.{bus.name}", f"device instance {bus.device_instance} is already used by "
                                           f"{instances[bus.device_instance]}")
            instances[bus.device_instance] = f"MS/TP trunk '{bus.name}'"
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

    _check_equip_templates(config, error, warning)
    _check_graphics(config, error, warning)

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
            wanted, label = {
                "bacnet_ip": (BacnetAddress, "BACnet"),
                "bacnet_mstp": (BacnetAddress, "BACnet"),
                "mqtt": (MqttAddress, "MQTT"),
            }.get(device.protocol, (ModbusAddress, "Modbus"))
            if not isinstance(point.address, wanted):
                error(where, f"device '{point.device}' is {label}, so the point needs a {label} address")

        if point.kind == Kind.STR and not point.enum:
            error(where, "Str points need an 'enum' list")
        _check_alarms(where, point.kind, point.enum, point.alarms, error)
        _check_writes(where, point, warning, error)
        if isinstance(point.address, ModbusAddress):
            _check_modbus_point(where, point, error)
        elif isinstance(point.address, BacnetAddress):
            _check_bacnet_point(where, point, error)
        else:
            _check_mqtt_point(where, point, error)

        prefix = _name_prefix(point, equips)
        if prefix and not point.name.startswith(prefix):
            warning(where, f"name does not follow <room>_<equipment>_<function>; expected it to start '{prefix}'")

    return issues


def writable(point) -> bool:
    """Whether commands can be written to this point (milestone 7A): a setpoint or command whose
    address can be written (Modbus coils and holding registers; BACnet value and output objects;
    MQTT with a command topic)."""
    if point.type.value not in ("sp", "cmd"):
        return False
    a = point.address
    if isinstance(a, ModbusAddress):
        return a.object_type.value in ("coil", "holdingRegister")
    if isinstance(a, BacnetAddress):
        return not a.object_type.value.endswith("Input")
    return bool(a.command_topic)


def _check_command_value(where: str, point, value, error) -> None:
    """A value the BMS will write: the right kind, in the enum, inside the write limits."""
    if point.kind == Kind.BOOL and not isinstance(value, bool):
        error(where, "give true or false")
    elif point.kind == Kind.STR and (not isinstance(value, str) or value not in (point.enum or [])):
        error(where, f"give one of {list(point.enum or [])}")
    elif point.kind == Kind.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            error(where, "give a number")
        elif point.write_limits and not point.write_limits.min <= value <= point.write_limits.max:
            error(where, f"{value} is outside the write limits {point.write_limits.min} to {point.write_limits.max}")


def _check_writes(where: str, point, warning, error) -> None:
    if point.write_limits is not None and point.kind != Kind.NUMBER:
        error(where, "write limits are for Number points")
    if point.write_limits is not None and point.type.value == "sensor":
        error(where, "a sensor is never written, so it has no write limits")
    if isinstance(point.address, MqttAddress) and point.address.command_topic:
        if any(c in point.address.command_topic for c in "+#"):
            error(where, "the command topic cannot have wildcards")
        if point.type.value == "sensor":
            error(where, "a sensor has no command topic")
    if point.occupancy is not None:
        if not writable(point):
            error(where, "only a writable setpoint or command can follow a schedule (occupancy)")
        for state in ("occupied", "unoccupied"):
            _check_command_value(f"{where}.occupancy.{state}", point, getattr(point.occupancy, state), error)
    if writable(point) and point.kind == Kind.NUMBER and point.write_limits is None:
        warning(where, "a writable Number point needs write limits before it can be commanded (main spec 5.6)")


def _check_alarms(where: str, kind, enum, alarms, error) -> None:
    """Point alarms (main spec 5.5): limits need a Number point, states a Bool or Str one."""
    keys = [a.key for a in alarms]
    if len(keys) != len(set(keys)):
        error(where, "one alarm of each type per point (and one per state for state alarms)")
    for a in alarms:
        if a.type in ("high", "low") and kind != Kind.NUMBER:
            error(where, f"a {a.type} limit alarm needs a Number point")
        if a.type == "state":
            if kind == Kind.NUMBER:
                error(where, "a state alarm needs a Bool or Str point; use high or low for numbers")
            elif kind == Kind.BOOL and not isinstance(a.state, bool):
                error(where, "a state alarm on a Bool point needs state true or false")
            elif kind == Kind.STR and (not isinstance(a.state, str) or (enum and a.state not in enum)):
                error(where, f"a state alarm on a Str point needs one of its enum values {list(enum or [])}")
    highs = [a.limit for a in alarms if a.type == "high"]
    lows = [a.limit for a in alarms if a.type == "low"]
    if highs and lows and highs[0] <= lows[0]:
        error(where, "the high alarm limit must be above the low alarm limit")


def _check_equip_templates(config, error, warning) -> None:
    """Equipment templates and the equipment and rooms made from them (main spec 5.1.2)."""
    from devine_bms.templates.equip import builtin_equip_templates, find

    seen: set[tuple[str, str]] = set()
    for t in config.equip_templates:
        where = f"equipTemplates.{t.id}@{t.version}"
        if (t.id, t.version) in seen:
            error(where, "this template version is listed twice")
        seen.add((t.id, t.version))
        if t.id in builtin_equip_templates():
            error(where, f"'{t.id}' is a built-in template; save a copy under a new ID")
        roles = [r.role for r in t.roles]
        if len(roles) != len(set(roles)):
            error(where, "role names must be unique")
        for r in t.roles:
            _check_alarms(f"{where}.roles.{r.role}", r.kind, r.enum, r.alarms, error)
            if r.occupancy is not None:
                if r.type.value not in ("sp", "cmd"):
                    error(f"{where}.roles.{r.role}", "only a setpoint or command can follow a schedule (occupancy)")
                for state in ("occupied", "unoccupied"):
                    _check_command_value(f"{where}.roles.{r.role}.occupancy.{state}", r,
                                         getattr(r.occupancy, state), error)

    points = {p.name: p for p in config.points}
    owners = [("equips", e, "equip") for e in config.equips] + [("spaces", s, "space") for s in config.spaces]
    for label, item, kind in owners:
        link = item.template
        if link is None:
            continue
        where = f"{label}.{item.name}.template"
        template = find(config.equip_templates, link.id, link.version)
        if template is None:
            error(where, f"no equipment template '{link.id}' version {link.version}")
            continue
        if template.applies_to != kind:
            error(where, f"template '{link.id}' is for a {template.applies_to}, not a {kind}")
            continue
        roles = {r.role: r for r in template.roles}
        bound: dict[str, str] = {}
        for role, point_name in link.bindings.items():
            if role not in roles:
                error(where, f"template '{link.id}' {link.version} has no role '{role}'")
                continue
            point = points.get(point_name)
            if point is None:
                error(where, f"role '{role}' is bound to '{point_name}', which does not exist")
                continue
            owner = point.equip if kind == "equip" else (point.space if not point.equip else None)
            if owner != item.name:
                error(where, f"role '{role}' is bound to '{point_name}', which belongs to another {kind}")
            if point_name in bound:
                error(where, f"'{point_name}' fills both '{bound[point_name]}' and '{role}'")
            bound[point_name] = role
        missing = [r.role for r in template.roles if r.required and r.role not in link.bindings]
        if missing:
            warning(where, f"required roles not bound yet: {missing}")


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


def _check_mqtt_point(where, point, error) -> None:
    topic = point.address.topic
    if "+" in topic or "#" in topic:
        error(where, "an MQTT point needs one exact topic; wildcards (+ and #) are not allowed")
    if topic.startswith("$"):
        error(where, "topics starting with $ are broker topics, not device topics")
    overlap = {v.lower() for v in point.address.true_values} & {v.lower() for v in point.address.false_values}
    if overlap:
        error(where, f"payload texts {sorted(overlap)} are in both true_values and false_values")


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


def _check_graphics(config, error, warning) -> None:
    """Graphics (milestone 9A): names unique, bound points exist, commands writable, rooms exist,
    tab orders unique."""
    from devine_bms.graphics.library import symbol_ids
    from devine_bms.graphics.sanitize import is_clean

    seen_symbols: set[str] = set()
    for sym in config.symbols:
        where = f"symbols.{sym.id}"
        if sym.id in symbol_ids():
            error(where, f"'{sym.id}' is a library symbol; give the site's symbol another ID")
        if sym.id in seen_symbols:
            error(where, "symbol IDs must be unique")
        seen_symbols.add(sym.id)
        problem = is_clean(sym.svg, "symbol", sym.id)
        if problem:
            error(where, f"the symbol's SVG is refused: {problem}")
    known_symbols = symbol_ids() | seen_symbols

    points = {p.name: p for p in config.points}
    spaces = {s.name for s in config.spaces}
    names: set[str] = set()
    tabs: dict[int, str] = {}
    for g in config.graphics:
        where = f"graphics.{g.name}"
        if g.name in names:
            error(where, "graphic names must be unique")
        names.add(g.name)
        if g.space and g.space not in spaces:
            error(where, f"space '{g.space}' does not exist")
        if g.background is not None:
            problem = is_clean(g.background.svg, "background", f"bg-{g.name}")
            if problem:
                error(f"{where}.background", f"the background is refused: {problem}")
        if g.tab is not None:
            if g.tab.order in tabs:
                error(where, f"tab order {g.tab.order} is also used by '{tabs[g.tab.order]}'")
            tabs[g.tab.order] = g.name
        ids: dict[str, str] = {}
        for item in g.items:
            if item.id in ids:
                error(f"{where}.items.{item.id}", "item IDs must be unique in a graphic")
            ids[item.id] = item.type

        def point(at: str, name: str | None, numeric: bool = False) -> None:
            if name is None:
                return
            p = points.get(name)
            if p is None:
                error(at, f"point '{name}' does not exist")
            elif numeric and p.kind.value != "Number":
                error(at, f"'{name}' is not a number")

        def bands(at: str, rule) -> None:
            point(at, rule.point, numeric=True)
            limits = [b.below for b in rule.bands]
            if limits[-1] is not None:
                error(at, "the last band has no upper limit (leave 'below' out)")
            if any(b is None for b in limits[:-1]):
                error(at, "every band but the last needs 'below'")
            elif limits[:-1] != sorted(limits[:-1]) or len(set(limits[:-1])) != len(limits) - 1:
                error(at, "the band limits must go up")

        for item in g.items:
            at = f"{where}.items.{item.id}"
            if item.type == "symbol":
                if item.symbol not in known_symbols:
                    error(at, f"no symbol '{item.symbol}' in the library")
                point(at, item.bind.run)
                point(at, item.bind.value)
                point(at, item.bind.fault)
                point(at, item.bind.command)
                cmd = points.get(item.bind.command) if item.bind.command else None
                if cmd is not None and not writable(cmd):
                    error(at, f"'{cmd.name}' cannot be commanded, so it cannot be a symbol's command")
            elif item.type == "value":
                point(at, item.point)
            elif item.type in ("pipe", "duct") and item.rule is not None:
                rule = item.rule
                if rule.type == "follows":
                    if ids.get(rule.item) != "symbol":
                        error(at, f"follows '{rule.item}', which is not a symbol in this graphic")
                elif rule.type == "bands":
                    bands(at, rule)
                else:
                    point(at, rule.point, numeric=True)
                    if rule.max <= rule.min:
                        error(at, "the fill's max must be above its min")
            elif item.type == "room":
                if item.space not in spaces:
                    error(at, f"space '{item.space}' does not exist")
                for name in item.values:
                    point(at, name)
                if item.rule is not None:
                    bands(at, item.rule)
