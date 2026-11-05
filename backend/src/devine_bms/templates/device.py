"""Device templates (main spec 5.1 and 20, milestone 4a).

A device template describes a type of device once: its point map, its default
communication settings and its watch-outs. Built-in templates ship for supported
hardware (main spec 20) in templates/builtin/. Expanding a template gives config
points (`devine-bms-config/0`) for one device; milestone 5 adds this to the web app.

    uv run python -m devine_bms.templates.device list
    uv run python -m devine_bms.templates.device show cwt-mb328b
    uv run python -m devine_bms.templates.device expand cwt-mb328b --device io1 --equip lights1 \\
        --name-prefix site_lights1_ --only di0,di1,do0
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from importlib import resources
from typing import Literal

from pydantic import Field, model_validator

from devine_bms.config.model import (
    BacnetAddress,
    Kind,
    ModbusAddress,
    MqttAddress,
    Name,
    ObjectType,
    PointType,
    PollClass,
    Tags,
    _Model,
)
from devine_bms.drivers.modbus.codec import DataType

SCHEMA_ID = "devine-bms-device-template/0"


class TemplateDefaults(_Model):
    unit_id: int = Field(default=1, ge=1, le=247)
    baudrate: int | None = None
    parity: Literal["N", "E", "O"] | None = None
    data_bits: Literal[7, 8] | None = None
    stop_bits: Literal[1, 2] | None = None
    tcp_host: str | None = None
    tcp_port: int | None = None
    block_size: int = Field(default=32, ge=1, le=125)


class TemplatePoint(_Model):
    key: Name
    dis: str
    type: PointType
    kind: Kind
    unit: str | None = None
    enum: list[str] | None = None
    tags: Tags = {}
    poll_class: PollClass = PollClass.NORMAL
    manual_address: str | None = Field(default=None, description="The address as the manual writes it")
    address: ModbusAddress | BacnetAddress | MqttAddress


class DeviceTemplate(_Model):
    schema_: Literal["devine-bms-device-template/0"] = Field(alias="schema")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str
    hardware_id: str | None = None
    vendor: str
    model: str
    dis: str
    status: Literal["datasheet only", "bench tested", "in service"]
    protocols: list[Literal["modbus_rtu", "modbus_tcp", "bacnet_ip", "bacnet_mstp", "mqtt"]]
    defaults: TemplateDefaults = TemplateDefaults()
    notes: list[str] = []
    points: list[TemplatePoint]

    @model_validator(mode="after")
    def _check(self) -> DeviceTemplate:
        keys = [p.key for p in self.points]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"duplicate point keys: {sorted(duplicates)}")
        families = {type(p.address) for p in self.points}
        if len(families) > 1:
            raise ValueError("a template's points must all be Modbus, all BACnet or all MQTT")
        seen: dict[tuple, str] = {}
        for p in self.points:
            a = p.address
            if isinstance(a, ModbusAddress):
                where = ("modbus", a.object_type, a.address)
                is_bit_object = a.object_type in (ObjectType.COIL, ObjectType.DISCRETE_INPUT)
                if is_bit_object != (a.data_type == DataType.BIT):
                    raise ValueError(
                        f"point '{p.key}': coils and discrete inputs use data type 'bit', registers must not")
            elif isinstance(a, BacnetAddress):
                where = ("bacnet", a.object_type, a.instance)
            else:
                where = ("mqtt", a.topic, a.json_path)
            if where in seen:
                raise ValueError(f"points '{seen[where]}' and '{p.key}' use the same address")
            seen[where] = p.key
        return self


def load_template(data: dict) -> DeviceTemplate:
    return DeviceTemplate.model_validate(data)


@lru_cache(maxsize=1)
def builtin_templates() -> dict[str, DeviceTemplate]:
    folder = resources.files("devine_bms.templates") / "builtin"
    templates = {}
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            template = load_template(json.loads(entry.read_text(encoding="utf-8")))
            templates[template.id] = template
    return templates


def _shift(address, n: int | None, step: int) -> dict:
    data = address.model_dump(mode="json", by_alias=True)
    if n is None:
        return data
    if isinstance(address, ModbusAddress):
        data["address"] = address.address + n * step
    elif isinstance(address, BacnetAddress):
        data["instance"] = address.instance + n * step
    else:
        data["topic"] = address.topic.format(n=n)
    return data


def expand(
    template: DeviceTemplate,
    device: str,
    name_prefix: str,
    equip: str | None = None,
    space: str | None = None,
    only: list[str] | None = None,
    n: int | None = None,
    step: int = 0,
) -> list[dict]:
    """Config points for one device made from `template`.

    Point names are `name_prefix` + the template key, for example site_lights1_ + do0.
    `only` picks some of the template's points (by key); the default is all of them.

    Bulk create (main spec 5.1): with a repeat number `n`, `{n}` in the name prefix,
    equipment and space is replaced (`{n:02}` pads it), and each address moves by
    `n * step`: Modbus addresses and BACnet instances add it, MQTT topics replace `{n}`.
    For a Mitsubishi BACnet gateway (H6, instance MM GG NN), step 100 moves to the next group.
    """
    if not equip and not space:
        raise ValueError("give an equip or a space for the points")
    if n is not None:
        name_prefix = name_prefix.format(n=n)
        equip = equip.format(n=n) if equip else equip
        space = space.format(n=n) if space else space
    wanted = set(only) if only else None
    if wanted:
        unknown = wanted - {p.key for p in template.points}
        if unknown:
            raise ValueError(f"template {template.id} has no points {sorted(unknown)}")
    points = []
    for p in template.points:
        if wanted is not None and p.key not in wanted:
            continue
        point = {
            "name": f"{name_prefix}{p.key}",
            "dis": p.dis,
            "device": device,
            "type": p.type.value,
            "kind": p.kind.value,
            "tags": dict(p.tags),
            "pollClass": p.poll_class.value,
            "address": _shift(p.address, n, step),
        }
        if equip:
            point["equip"] = equip
        if space:
            point["space"] = space
        if p.unit:
            point["unit"] = p.unit
        if p.enum:
            point["enum"] = list(p.enum)
        points.append(point)
    return points


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Devine BMS device templates")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the built-in templates")
    show = sub.add_parser("show", help="show one template")
    show.add_argument("template")
    exp = sub.add_parser("expand", help="print config points for one device")
    exp.add_argument("template")
    exp.add_argument("--device", required=True)
    exp.add_argument("--name-prefix", required=True, help="for example site_lights1_")
    exp.add_argument("--equip")
    exp.add_argument("--space")
    exp.add_argument("--only", help="comma-separated point keys, for example di0,do0")
    args = parser.parse_args(argv)

    templates = builtin_templates()
    if args.command == "list":
        for t in templates.values():
            print(f"{t.id:20} {t.hardware_id or '':4} {t.vendor} {t.model} ({len(t.points)} points, {t.status})")
        return 0
    template = templates.get(args.template)
    if template is None:
        print(f"no built-in template '{args.template}'; try: list", file=sys.stderr)
        return 1
    if args.command == "show":
        print(f"{template.vendor} {template.model} ({template.hardware_id}), version {template.version}, {template.status}")
        print(f"Defaults: {template.defaults.model_dump(by_alias=True, exclude_none=True)}")
        for note in template.notes:
            print(f"  - {note}")
        for p in template.points:
            a = p.address
            print(f"  {p.key:6} {a.object_type:16} wire {a.address:<5} manual {p.manual_address or '':6} {p.dis}")
        return 0
    only = args.only.split(",") if args.only else None
    points = expand(template, args.device, args.name_prefix, args.equip, args.space, only)
    print(json.dumps(points, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
