"""Checking a diagram (main spec 5.3.1 Publishing, 5.3.2 Validation; milestone 10A).

`plan()` turns a diagram into a Plan (settings parsed, ports typed, an evaluation order) and a
list of issues. Errors stop publishing (and importing); warnings do not. The site-wide checks
(points exist and can be written, variables and schedules exist, one writer per point, the
site's location for astro) need a `Site` view of the setup.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from devine_bms.logic.model import (
    BLOCKS,
    Diagram,
    Issue,
    Plan,
    TypeContext,
    VType,
    settings_of,
)


@dataclass
class SitePoint:
    name: str
    kind: str  # Number, Bool, Str
    writable: bool = False
    occupancy: bool = False  # follows a schedule (milestone 7B)
    equip: str | None = None
    enum: tuple[str, ...] | None = None


@dataclass
class SiteVariable:
    name: str
    type: VType
    kind: str = "constant"


@dataclass
class Site:
    """What the checks need to know about the site."""

    points: dict[str, SitePoint] = field(default_factory=dict)
    variables: dict[str, SiteVariable] = field(default_factory=dict)  # site-scope variables
    schedules: set[str] = field(default_factory=set)
    equips: set[str] = field(default_factory=set)
    located: bool = False  # the site has a latitude and longitude
    writers: dict[str, str] = field(default_factory=dict)  # point -> the other published diagram writing it


KIND_TYPE: dict[str, VType] = {"Number": "number", "Bool": "bool", "Str": "text"}


def _type_of(value: Any) -> VType:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    return "text"


def plan(diagram: Diagram | dict, site: Site | None = None) -> tuple[Plan | None, list[Issue]]:
    """Check a diagram. Returns the plan (None when the diagram cannot even be read) and issues."""
    issues: list[Issue] = []

    def error(message: str, block: str | None = None) -> None:
        issues.append(Issue("error", message, block))

    def warning(message: str, block: str | None = None) -> None:
        issues.append(Issue("warning", message, block))

    if isinstance(diagram, dict):
        try:
            diagram = Diagram.model_validate(diagram)
        except ValidationError as exc:
            for e in exc.errors()[:20]:
                where = ".".join(str(x) for x in e["loc"])
                error(f"{where}: {e['msg']}")
            return None, issues

    p = Plan(diagram=diagram)
    local_vars = {v.name: v for v in diagram.variables}
    if len(local_vars) != len(diagram.variables):
        error("variable names must be unique")
    for v in diagram.variables:
        if v.value is not None and _type_of(v.value) != v.type:
            error(f"variable '{v.name}' is {v.type}, but its value is {_type_of(v.value)}")
        if v.type != "number" and (v.min is not None or v.max is not None):
            error(f"variable '{v.name}': only numbers have limits")
        if v.min is not None and v.max is not None and v.min > v.max:
            error(f"variable '{v.name}': the minimum is above the maximum")

    def point_type(name: str) -> VType | None:
        pt = site.points.get(name) if site else None
        return KIND_TYPE.get(pt.kind) if pt else None

    def variable_type(name: str) -> VType | None:
        if name in local_vars:
            return local_vars[name].type
        sv = site.variables.get(name) if site else None
        return sv.type if sv else None

    ctx = TypeContext(point_type, variable_type)

    # Blocks: types, settings, ports.
    ids: set[str] = set()
    for b in diagram.blocks:
        if b.id in ids:
            error(f"the block ID '{b.id}' is used twice", b.id)
            continue
        ids.add(b.id)
        if b.type not in BLOCKS:
            error(f"unknown block type '{b.type}'", b.id)
            continue
        try:
            s = settings_of(b)
        except ValidationError as exc:
            for e in exc.errors()[:5]:
                where = ".".join(str(x) for x in e["loc"]) or "settings"
                error(f"{where}: {e['msg']}", b.id)
            continue
        except ValueError as exc:
            error(str(exc), b.id)
            continue
        spec = BLOCKS[b.type]
        p.settings[b.id] = s
        p.inputs[b.id] = spec.inputs(s, ctx)
        p.outputs[b.id] = spec.outputs(s, ctx)
        if b.position is None:
            warning("the block has no position; it is laid out automatically", b.id)
        for name, value in s.inputs.items():
            port = next((x for x in p.inputs[b.id] if x.name == name), None)
            if port is None:
                error(f"settings.inputs names '{name}', which is not an input of {spec.dis}", b.id)
            elif port.type and _type_of(value) != port.type:
                error(f"the value for '{name}' must be {port.type}", b.id)

    # Wires.
    for w in diagram.wires:
        src, dst = w.from_, w.to
        label = f"wire {src.block}.{src.port} → {dst.block}.{dst.port}"
        if src.block not in p.outputs or dst.block not in p.inputs:
            missing = src.block if src.block not in ids else dst.block
            if missing not in ids:
                error(f"{label}: there is no block '{missing}'")
            continue  # the block itself was reported
        out = next((x for x in p.outputs[src.block] if x.name == src.port), None)
        inp = next((x for x in p.inputs[dst.block] if x.name == dst.port), None)
        if out is None:
            error(f"{label}: '{src.port}' is not an output of {src.block}", dst.block)
            continue
        if inp is None:
            error(f"{label}: '{dst.port}' is not an input of {dst.block}", dst.block)
            continue
        if out.type and inp.type and out.type != inp.type:
            error(f"{label}: joins {out.type} to {inp.type}", dst.block)
            continue
        if (dst.block, dst.port) in p.feeds:
            error(f"input '{dst.port}' is wired twice", dst.block)
            continue
        if src.block == dst.block and not BLOCKS[diagram_block(diagram, dst.block).type].timing:
            error(f"{label}: a block cannot feed itself", dst.block)
            continue
        p.feeds[(dst.block, dst.port)] = (src.block, src.port)

    # Every required input is wired or given a value.
    for bid, ports in p.inputs.items():
        s = p.settings[bid]
        for port in ports:
            if port.required and (bid, port.name) not in p.feeds and port.name not in s.inputs:
                error(f"input '{port.name}' is not wired and has no value", bid)

    # Block-specific checks against the site.
    for b in diagram.blocks:
        s = p.settings.get(b.id)
        if s is None:
            continue
        if b.type in ("point_read", "point_write"):
            template = diagram.scope == "template"
            ref = s.role if template else s.point
            what = "role" if template else "point"
            if template and s.point:
                error("a template's logic names roles, not points", b.id)
            if not ref:
                error(f"no {what} chosen", b.id)
            elif site is not None:
                pt = site.points.get(ref)
                if pt is None:
                    error(f"{what} '{ref}' does not exist" + (" in the template" if template else ""), b.id)
                elif diagram.scope == "equipment" and diagram.equip and pt.equip not in (None, diagram.equip) \
                        and b.type == "point_write":
                    warning(f"'{ref}' belongs to {pt.equip}, not {diagram.equip}", b.id)
            if b.type == "point_read" and s.on_bad == "fallback":
                if s.fallback is None:
                    error("choose a fallback value for when the point is not Good, or use the fail-safe", b.id)
                elif point_type(ref) and _type_of(s.fallback) != point_type(ref):
                    error(f"the fallback must be {point_type(ref)}", b.id)
            if b.type == "point_write" and site is not None and ref in site.points:
                pt = site.points[ref]
                if not pt.writable:
                    error(f"'{ref}' cannot be written (only writable setpoints and commands)", b.id)
                if pt.occupancy:
                    error(f"'{ref}' already follows a schedule (its occupancy values); a point has one writer",
                          b.id)
                if ref in site.writers:
                    error(f"'{ref}' is already written by the diagram '{site.writers[ref]}'", b.id)
                if s.failsafe.mode == "value" and point_type(ref) \
                        and _type_of(s.failsafe.value) != point_type(ref):
                    error(f"the fail-safe value must be {point_type(ref)}", b.id)
        elif b.type in ("variable_read", "variable_write"):
            if not s.variable:
                error("no variable chosen", b.id)
            elif site is None and s.variable not in local_vars:
                pass  # a site variable: checked against the site
            elif variable_type(s.variable) is None:
                error(f"variable '{s.variable}' does not exist", b.id)
            elif b.type == "variable_write":
                kind = local_vars[s.variable].kind if s.variable in local_vars else (
                    site.variables[s.variable].kind if site and s.variable in site.variables else "memory")
                if kind == "constant":
                    error(f"'{s.variable}' is a constant (set by a person); only memory variables are written by logic",
                          b.id)
        elif b.type == "schedule":
            if not s.schedule:
                error("no schedule chosen", b.id)
            elif site is not None and s.schedule not in site.schedules:
                error(f"schedule '{s.schedule}' does not exist", b.id)
        elif b.type == "astro" and site is not None and not site.located:
            error("the astro time switch needs the site's latitude and longitude (Setup → Site)", b.id)
        elif b.type == "manual_auto":
            pass

    # Two outputs writing one point inside this diagram.
    written: dict[str, str] = {}
    for b in diagram.blocks:
        s = p.settings.get(b.id)
        if b.type == "point_write" and s is not None and (s.role or s.point):
            ref = s.role or s.point
            if ref in written:
                error(f"'{ref}' is also written by block {written[ref]}", b.id)
            written[ref] = b.id
        if b.type == "variable_write" and s is not None and s.variable:
            key = f"variable {s.variable}"
            if key in written:
                error(f"variable '{s.variable}' is also written by block {written[key]}", b.id)
            written[key] = b.id

    if diagram.scope == "template" and not diagram.template:
        error("a template's logic names its equipment template")
    if diagram.scope == "equipment":
        if not diagram.equip:
            error("an equipment diagram names its equipment")
        elif site is not None and diagram.equip not in site.equips:
            error(f"equipment '{diagram.equip}' does not exist")

    # Evaluation order: inputs to outputs; a loop must pass through a timing block.
    p.order, loop = _order(diagram, p)
    if loop:
        error(f"a loop without a timing block (delay on, delay off, minimum on/off): {' → '.join(loop)}", loop[0])

    # Unused outputs (not for outputs-only blocks).
    used = {src for src in p.feeds.values()}
    for bid, ports in p.outputs.items():
        for port in ports:
            if (bid, port.name) not in used:
                warning(f"output '{port.name}' is not used", bid)
    return p, issues


def diagram_block(diagram: Diagram, block_id: str):
    return next(b for b in diagram.blocks if b.id == block_id)


def _order(diagram: Diagram, p: Plan) -> tuple[list[str], list[str] | None]:
    """Kahn's algorithm over the blocks with checked settings. When stuck on a loop, a timing
    block in it is run early (it reads its input from the previous cycle); a loop with no
    timing block is returned so it can be reported."""
    blocks = [b.id for b in diagram.blocks if b.id in p.settings]
    deps: dict[str, set[str]] = defaultdict(set)
    for (dst, _port), (src, _out) in p.feeds.items():
        if src != dst:
            deps[dst].add(src)
    types = {b.id: b.type for b in diagram.blocks}
    done: list[str] = []
    left = list(blocks)
    while left:
        ready = [b for b in left if not (deps[b] - set(done))]
        if not ready:
            timing = [b for b in left if BLOCKS[types[b]].timing and _on_loop(b, deps, set(done))]
            if not timing:
                return done + left, _find_loop(left, deps, set(done))
            ready = [timing[0]]
        for b in ready:
            done.append(b)
            left.remove(b)
    return done, None


def _on_loop(start: str, deps: dict[str, set[str]], done: set[str]) -> bool:
    seen, stack = set(), [start]
    while stack:
        node = stack.pop()
        for d in deps[node] - done:
            if d == start:
                return True
            if d not in seen:
                seen.add(d)
                stack.append(d)
    return False


def _find_loop(left: list[str], deps: dict[str, set[str]], done: set[str]) -> list[str]:
    for start in left:
        path, node, seen = [start], start, {start}
        while True:
            nxt = next(iter(deps[node] - done), None)
            if nxt is None:
                break
            if nxt in seen:
                return path[path.index(nxt):] + [nxt]
            path.append(nxt)
            seen.add(nxt)
            node = nxt
    return left[:1]


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]
