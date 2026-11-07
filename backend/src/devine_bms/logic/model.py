"""The logic format `devine-bms-logic/1` and the v1 blocks (main spec 5.3.1 and 5.3.2, milestone 10A).

A diagram lists blocks (id, type, settings, optional position), wires (from a block's output
port to a block's input port) and the private variables it uses. Points, variables and
schedules are referred to by name. Each block type says its ports and their value types
(`bool`, `number`, `text`); some depend on the settings or on the point a block reads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

SCHEMA = "devine-bms-logic/1"
VType = Literal["bool", "number", "text"]
Value = bool | float | int | str
Name = str  # checked with NAME_PATTERN where it matters
NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# ---------- block settings ----------

class _Settings(_Model):
    inputs: dict[str, Value] = {}  # values for inputs left unwired


class PointReadSettings(_Settings):
    point: str = ""
    on_bad: Literal["fallback", "failsafe"] = "fallback"
    fallback: Value | None = None


class Failsafe(_Model):
    mode: Literal["release", "hold", "value"] = "release"
    value: Value | None = None

    @model_validator(mode="after")
    def _value(self):
        if self.mode == "value" and self.value is None:
            raise ValueError("a fail-safe of 'value' needs the value")
        return self


class PointWriteSettings(_Settings):
    point: str = ""
    failsafe: Failsafe = Failsafe()


class VariableSettings(_Settings):
    variable: str = ""


class ScheduleSettings(_Settings):
    schedule: str = ""


class GateSettings(_Settings):
    count: int = Field(default=2, ge=2, le=8)


class NoSettings(_Settings):
    pass


class CompareSettings(_Settings):
    op: Literal["<", ">", "<=", ">=", "==", "!="] = ">"
    deadband: float = Field(default=0, ge=0)


class LimitSettings(_Settings):
    rate: float | None = Field(default=None, gt=0)  # units per second


class ScaleSettings(_Settings):
    in_min: float = 0
    in_max: float = 100
    out_min: float = 0
    out_max: float = 100
    clamp: bool = True
    unit: str | None = None

    @model_validator(mode="after")
    def _range(self):
        if self.in_min == self.in_max:
            raise ValueError("the input minimum and maximum must differ")
        return self


class AstroSettings(_Settings):
    active: Literal["night", "day"] = "night"
    sunset_offset: int = Field(default=0, ge=-240, le=240)  # minutes; negative is before sunset
    sunrise_offset: int = Field(default=0, ge=-240, le=240)


class PidSettings(_Settings):
    kp: float = Field(default=1.0, ge=0)
    ti: float = Field(default=60.0, ge=0)  # seconds; 0 is no integral
    td: float = Field(default=0.0, ge=0)  # seconds
    out_min: float = 0
    out_max: float = 100
    action: Literal["direct", "reverse"] = "reverse"  # reverse: output rises when PV is below SP (heating)
    sample: float = Field(default=1.0, ge=1, le=3600)

    @model_validator(mode="after")
    def _range(self):
        if self.out_max <= self.out_min:
            raise ValueError("the output maximum must be above the minimum")
        return self


class ManualAutoSettings(_Settings):
    value_type: Literal["number", "bool"] = "number"


# ---------- ports ----------

@dataclass(frozen=True)
class Port:
    name: str
    type: VType | None  # None: depends on something unknown (reported elsewhere)
    required: bool = True  # an input that must be wired or given a value in settings.inputs
    default: Any = None  # used when an optional input is unwired


@dataclass
class TypeContext:
    """What the ports depend on outside the block: the kinds of points and variables."""

    point_type: Callable[[str], VType | None] = lambda name: None
    variable_type: Callable[[str], VType | None] = lambda name: None


@dataclass(frozen=True)
class BlockType:
    type: str
    group: str
    dis: str
    settings: type[_Settings]
    inputs: Callable[[Any, TypeContext], list[Port]]
    outputs: Callable[[Any, TypeContext], list[Port]]
    timing: bool = False  # may sit on a feedback loop (reads its input from the previous cycle)
    stateful: bool = False


def _n(*names: str, required: bool = True) -> Callable:
    return lambda s, c: [Port(n, "number", required) for n in names]


BLOCKS: dict[str, BlockType] = {b.type: b for b in [
    BlockType("point_read", "Read and write", "Point read", PointReadSettings,
              lambda s, c: [], lambda s, c: [Port("out", c.point_type(s.point))]),
    BlockType("point_write", "Read and write", "Point write", PointWriteSettings,
              lambda s, c: [Port("in", c.point_type(s.point))], lambda s, c: []),
    BlockType("variable_read", "Read and write", "Variable read", VariableSettings,
              lambda s, c: [], lambda s, c: [Port("out", c.variable_type(s.variable))]),
    BlockType("variable_write", "Read and write", "Variable write", VariableSettings,
              lambda s, c: [Port("in", c.variable_type(s.variable)), Port("enable", "bool", False, True)],
              lambda s, c: []),
    BlockType("schedule", "Read and write", "Schedule", ScheduleSettings,
              lambda s, c: [], lambda s, c: [Port("out", "bool")]),
    BlockType("and", "Logic", "AND", GateSettings,
              lambda s, c: [Port(f"in{i}", "bool") for i in range(1, s.count + 1)], lambda s, c: [Port("out", "bool")]),
    BlockType("or", "Logic", "OR", GateSettings,
              lambda s, c: [Port(f"in{i}", "bool") for i in range(1, s.count + 1)], lambda s, c: [Port("out", "bool")]),
    BlockType("not", "Logic", "NOT", NoSettings,
              lambda s, c: [Port("in", "bool")], lambda s, c: [Port("out", "bool")]),
    BlockType("compare", "Logic", "Compare", CompareSettings,
              _n("a", "b"), lambda s, c: [Port("out", "bool")], stateful=True),
    BlockType("limit", "Math", "Limit", LimitSettings,
              _n("in", "min", "max"), lambda s, c: [Port("out", "number")], stateful=True),
    BlockType("scale", "Math", "Scale", ScaleSettings,
              _n("in"), lambda s, c: [Port("out", "number")]),
    BlockType("delay_on", "Timing", "Delay on", NoSettings,
              lambda s, c: [Port("in", "bool"), Port("time", "number")], lambda s, c: [Port("out", "bool")],
              timing=True, stateful=True),
    BlockType("delay_off", "Timing", "Delay off", NoSettings,
              lambda s, c: [Port("in", "bool"), Port("time", "number")], lambda s, c: [Port("out", "bool")],
              timing=True, stateful=True),
    BlockType("min_on_off", "Timing", "Minimum on/off", NoSettings,
              lambda s, c: [Port("in", "bool"), Port("minOn", "number"), Port("minOff", "number")],
              lambda s, c: [Port("out", "bool")], timing=True, stateful=True),
    BlockType("astro", "Timing", "Astro time switch", AstroSettings,
              lambda s, c: [Port("enable", "bool", False, True)], lambda s, c: [Port("out", "bool")]),
    BlockType("pid", "Control", "PID", PidSettings,
              lambda s, c: [Port("pv", "number"), Port("sp", "number"), Port("enable", "bool", False, True),
                            Port("track", "number", False, None)],
              lambda s, c: [Port("out", "number")], stateful=True),
    BlockType("manual_auto", "Control", "Manual/auto", ManualAutoSettings,
              lambda s, c: [Port("auto", s.value_type), Port("manual", s.value_type), Port("mode", "bool", False, False)],
              lambda s, c: [Port("out", s.value_type)]),
]}


# ---------- the diagram ----------

class XY(_Model):
    x: float
    y: float


class Block(_Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    type: str
    settings: dict[str, Any] = {}
    position: XY | None = None
    label: str | None = Field(default=None, max_length=80)


class End(_Model):
    block: str
    port: str


class Wire(_Model):
    from_: End = Field(alias="from")
    to: End


class VariableDef(_Model):
    """A variable private to the diagram (scope `diagram`); site variables are kept apart."""

    name: str = Field(pattern=NAME_PATTERN, max_length=60)
    dis: str | None = None
    type: VType = "number"
    unit: str | None = None
    kind: Literal["constant", "memory"] = "constant"
    value: Value | None = None  # the starting value
    min: float | None = None
    max: float | None = None


class Diagram(_Model):
    schema_: Literal["devine-bms-logic/1"] = Field(default=SCHEMA, alias="schema")
    name: str = Field(pattern=NAME_PATTERN, max_length=100)
    dis: str = ""
    scope: Literal["equipment", "site"] = "site"
    equip: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    variables: list[VariableDef] = Field(default=[], max_length=200)
    blocks: list[Block] = Field(default=[], max_length=500)
    wires: list[Wire] = Field(default=[], max_length=2000)


def settings_of(block: Block) -> _Settings:
    """The block's settings as their model (raises ValueError or pydantic's ValidationError)."""
    spec = BLOCKS.get(block.type)
    if spec is None:
        raise ValueError(f"unknown block type '{block.type}'")
    return spec.settings.model_validate(block.settings)


@dataclass
class Issue:
    level: Literal["error", "warning"]
    message: str
    block: str | None = None

    def as_dict(self) -> dict:
        return {"level": self.level, "message": self.message, **({"block": self.block} if self.block else {})}


@dataclass
class Plan:
    """A checked diagram ready to run: settings parsed, ports typed, the evaluation order."""

    diagram: Diagram
    settings: dict[str, Any] = field(default_factory=dict)  # block id -> settings model
    inputs: dict[str, list[Port]] = field(default_factory=dict)
    outputs: dict[str, list[Port]] = field(default_factory=dict)
    feeds: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)  # (block, in) -> (block, out)
    order: list[str] = field(default_factory=list)


def json_schema() -> dict[str, Any]:
    """The published JSON Schema for the logic format, with each block type's settings."""
    schema = Diagram.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA
    schema["title"] = "Devine BMS logic diagram"
    schema["blockSettings"] = {t: b.settings.model_json_schema(by_alias=True) for t, b in BLOCKS.items()}
    return schema
