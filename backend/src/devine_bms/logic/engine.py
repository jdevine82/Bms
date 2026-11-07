"""The logic runtime (main spec 5.3.1 Execution; milestone 10A, decision 027).

Every published, enabled diagram runs once a second. Outputs are written at BACnet priority 10,
clamped to the point's write limits, when their value changes and again every 10 minutes, and
never over an operator's hand mode on Modbus or MQTT (BACnet's priorities sort that out). A bad
input, a failed block, disabling, unpublishing or removing a diagram, and stopping the server
apply each output's fail-safe: release (BACnet relinquishes 10; others stop writing), hold, or a
stated value. Variables are kept in the database through a restart.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from devine_bms.config.model import BacnetAddress, Kind, VariableAddress
from devine_bms.db import models as db
from devine_bms.logic.check import Site, SitePoint, SiteVariable, errors, plan
from devine_bms.logic.evaluate import Evaluator, Place, Step
from devine_bms.logic.model import Diagram, Plan
from devine_bms.values import Status

log = logging.getLogger(__name__)

LOGIC_PRIORITY = 10
REWRITE_S = 600.0
RETRY_S = 10.0


@dataclass
class Running:
    name: str
    version: int | str
    plan: Plan
    evaluator: Evaluator
    last: Step | None = None
    last_at: datetime | None = None


@dataclass
class OutputState:
    diagram: str
    point: str
    failsafe: Any
    wanted: Any = None  # what logic wants written now (None: none, the fail-safe applies)
    state: str = "waiting"  # written, failsafe, released, held, hand, error, waiting
    written: Any = None
    written_at: float = 0.0
    error: str | None = None
    failed_at: float = 0.0
    released: bool = False


@dataclass
class Var:
    scope: str
    owner: str
    name: str
    type: str
    kind: str
    value: Any = None
    unit: str | None = None
    dis: str | None = None
    min: float | None = None
    max: float | None = None
    dirty: bool = False

    def as_dict(self) -> dict:
        return {"scope": self.scope, "owner": self.owner or None, "name": self.name, "dis": self.dis,
                "type": self.type, "unit": self.unit, "kind": self.kind, "value": self.value,
                "min": self.min, "max": self.max}


class LogicEngine:
    def __init__(self, runtime, sessions, site_name: str, overrides=None, schedules=None, record=None,
                 tick_s: float = 1.0, rewrite_s: float = REWRITE_S) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.site_name = site_name
        self.overrides = overrides or (dict)  # point id -> hand-mode override (CommandService)
        self.schedules = schedules  # ScheduleService, for schedule blocks
        self.record = record
        self.tick_s = tick_s
        self.rewrite_s = rewrite_s
        self.running: dict[str, Running] = {}
        self.status: dict[str, dict] = {}  # diagram -> {"state": ..., "issues": [...]}
        self.outputs: dict[str, OutputState] = {}  # point name -> its logic output
        self.vars: dict[tuple[str, str, str], Var] = {}  # (scope, owner, name)
        self.place = Place(ZoneInfo("UTC"))
        self.site_id: uuid.UUID | None = None
        self._task: asyncio.Task | None = None
        self._writes: dict[str, asyncio.Task] = {}
        self._last_flush = 0.0
        self._pushed: dict = {}  # variable point id -> (value, when)
        self.instances: dict[str, dict] = {}  # template logic instances: name -> equip, template, version
        self.writes: dict[str, list[str]] = {}  # every diagram that may run -> the points it writes
        self.lock = asyncio.Lock()

    # ---------- life ----------

    async def start(self) -> None:
        await self.reload()
        self._task = asyncio.create_task(self._loop(), name="logic")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        # The server is stopping: every output to its fail-safe (main spec 5.3.1).
        try:
            await asyncio.wait_for(self._failsafe_all(list(self.outputs.values()), "the server stopped"), 10)
        except TimeoutError:
            log.warning("not every logic output reached its fail-safe before the server stopped")
        await self.flush_variables(force=True)

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.cycle()
            except Exception:
                log.exception("logic cycle failed")
            await asyncio.sleep(max(0.05, self.tick_s - (time.monotonic() - started)))

    # ---------- loading ----------

    async def reload(self) -> None:
        """Load the published, enabled diagrams and the template logic instances; outputs that
        no running diagram writes any more go to their fail-safe."""
        from devine_bms.setup.export import export_dict

        async with self.lock:
            async with self.sessions() as session:
                site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
                self.site_id = site.id
                self.place = Place(_zone(site.tz), site.geo_lat, site.geo_lng)
                rows = (await session.scalars(select(db.LogicDiagram).where(db.LogicDiagram.site_id == site.id))).all()
                versions = {}
                for r in rows:
                    if r.published_version is not None:
                        versions[r.name] = (r, await session.scalar(select(db.LogicVersion).where(
                            db.LogicVersion.diagram_id == r.id, db.LogicVersion.version == r.published_version)))
                var_rows = (await session.scalars(select(db.LogicVariable).where(
                    db.LogicVariable.site_id == site.id))).all()
                switches = {i.name: i.enabled for i in (await session.scalars(select(db.LogicInstance).where(
                    db.LogicInstance.site_id == site.id))).all()}
                setup = await export_dict(session, self.site_name)
            for v in var_rows:
                key = (v.scope, v.owner, v.name)
                if key not in self.vars or not self.vars[key].dirty:
                    self.vars[key] = Var(v.scope, v.owner, v.name, v.type, v.kind, (v.value or {}).get("v"), v.unit,
                                         v.dis, v.min, v.max)

            status: dict[str, dict] = {}
            # What may run: published diagrams (not a template's own logic, which runs as instances)...
            candidates: dict[str, dict] = {}
            for r in rows:
                scope = (r.draft or {}).get("scope")
                status[r.name] = {"state": "template" if scope == "template" else
                                  "draft" if r.published_version is None else "disabled", "issues": []}
            for name, (row, version) in versions.items():
                if version is None or version.body.get("scope") == "template":
                    continue
                candidates[name] = {"body": version.body, "version": version.version, "enabled": row.enabled}
            # ...and every instance of a template version that has logic (milestone 10C).
            overridden = {c["body"].get("equip") for c in candidates.values()
                          if c["enabled"] and c["body"].get("scope") == "equipment"}
            self.instances = {}
            for name, meta, body, missing in template_instances(setup):
                meta["enabled"] = switches.get(name, True)
                self.instances[name] = meta
                if meta["equip"] in overridden:
                    status[name] = {"state": "overridden", "issues": [{"level": "warning", "message":
                                    f"an equipment diagram for {meta['equip']} replaces the template's logic"}]}
                elif missing:
                    status[name] = {"state": "error", "issues": [{"level": "error", "message":
                                    f"roles not bound on {meta['equip']}: {', '.join(missing)}"}]}
                else:
                    status[name] = {"state": "disabled", "issues": []}
                    candidates[name] = {"body": body, "version": f"{meta['template']}@{meta['templateVersion']}",
                                        "enabled": meta["enabled"]}

            enabled = {n: c["body"] for n, c in candidates.items() if c["enabled"]}
            running: dict[str, Running] = {}
            for name, c in candidates.items():
                if not c["enabled"]:
                    continue
                old = self.running.get(name)
                p, issues = plan(c["body"], self.site_view(exclude=name, published=enabled))
                if p is None or errors(issues):
                    status[name] = {"state": "error", "issues": [i.as_dict() for i in errors(issues)]}
                    log.warning("logic %s (%s) cannot run: %s", name, c["version"],
                                "; ".join(i.message for i in errors(issues)))
                    continue
                self._ensure_diagram_vars(p.diagram)
                if old is not None and old.version == c["version"]:
                    old.plan = p  # same version: keep its timers and PID state
                    old.evaluator.plan = p
                    running[name] = old
                else:
                    running[name] = Running(name, c["version"], p, Evaluator(p, self.place))
                running[name].evaluator.place = self.place
                status[name] = {"state": "running", "issues": []}
            self.writes = {n: [b["settings"]["point"] for b in body.get("blocks", [])
                               if b.get("type") == "point_write" and (b.get("settings") or {}).get("point")]
                           for n, body in {**{n: c["body"] for n, c in candidates.items()}}.items()}

            # Outputs no running diagram writes any more go to their fail-safe.
            still = {o.settings_point: name for name, r in running.items() for o in _writes(r.plan)}
            gone = [o for point, o in self.outputs.items() if point not in still]
            for o in gone:
                del self.outputs[o.point]
            for name, r in running.items():
                for w in _writes(r.plan):
                    o = self.outputs.get(w.settings_point)
                    if o is None or o.diagram != name:
                        self.outputs[w.settings_point] = OutputState(name, w.settings_point, w.failsafe)
                    else:
                        o.failsafe = w.failsafe
            self.running = running
            self.status = status
        if gone:
            await self._failsafe_all(gone, "its diagram stopped")

    def site_view(self, exclude: str | None = None, published: dict | None = None) -> Site:
        """What the checks know about the site, from the running setup and the other diagrams."""
        cfg = self.runtime.config
        equips = {e.id: e.name for e in cfg.equips}
        points = {p.name: SitePoint(p.name, p.kind.value, p.writable, bool(p.occupancy), equips.get(p.equip_id),
                                    tuple(p.enum) if p.enum else None) for p in cfg.points}
        writers: dict[str, str] = {}
        if published is None:
            for name, r in self.running.items():
                if name != exclude:
                    for w in _writes(r.plan):
                        writers[w.settings_point] = name
        else:
            for name, body in published.items():  # name -> body of each enabled candidate
                if name == exclude:
                    continue
                for b in body.get("blocks", []):
                    if b.get("type") == "point_write" and (b.get("settings") or {}).get("point"):
                        writers[b["settings"]["point"]] = name
        variables = {v.name: SiteVariable(v.name, v.type, v.kind) for (scope, _o, _n), v in self.vars.items()
                     if scope == "site"}
        schedules = set(self.schedules.schedules) if self.schedules is not None else set()
        return Site(points=points, variables=variables, schedules=schedules, equips=set(equips.values()),
                    located=self.place.lat is not None and self.place.lng is not None, writers=writers)

    def _ensure_diagram_vars(self, d: Diagram) -> None:
        for v in d.variables:
            key = ("diagram", d.name, v.name)
            have = self.vars.get(key)
            if have is None or have.type != v.type:
                self.vars[key] = Var("diagram", d.name, v.name, v.type, v.kind, v.value, v.unit, v.dis, v.min, v.max,
                                     dirty=True)
            else:  # keep the value (kept through a restart); take the new definition
                have.kind, have.unit, have.dis, have.min, have.max = v.kind, v.unit, v.dis, v.min, v.max

    # ---------- running ----------

    async def cycle(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        async with self.lock:
            for name, r in self.running.items():
                step = r.evaluator.step(now, _Reader(self, name))
                r.last, r.last_at = step, now
                for vw in step.variables:
                    self._set_var(name, vw.variable, vw.value)
                for out in step.outputs:
                    o = self.outputs.get(out.point)
                    if o is not None and o.diagram == name:
                        o.wanted = out.value
                        o.failsafe = out.failsafe
                        self._drive(o)
        self.push_points()
        await self.flush_variables()

    def _point(self, name: str):
        return next((p for p in self.runtime.config.points if p.name == name), None)

    def _drive(self, o: OutputState) -> None:
        """Bring one output towards what logic wants (or its fail-safe), without waiting."""
        point = self._point(o.point)
        if point is None:
            o.state, o.error = "error", f"the point {o.point} no longer exists"
            return
        if o.point in self._writes and not self._writes[o.point].done():
            return  # a write is under way
        mono = time.monotonic()
        if o.wanted is None:
            mode = o.failsafe.mode
            if mode == "hold":
                o.state = "held"
                return
            if mode == "release":
                if o.released:
                    o.state = "released"
                    return
                if not isinstance(point.address, BacnetAddress):
                    o.released, o.state, o.written = True, "released", None
                    return
                target: Any = None
            else:
                target = o.failsafe.value
        else:
            target = o.wanted
        try:
            target = _coerce(point, target) if target is not None else None
        except ValueError as exc:
            o.state, o.error = "error", str(exc)
            return
        if point.id in self.overrides() and not isinstance(point.address, BacnetAddress):
            o.state = "hand"  # it gets logic's value when hand mode ends (value_for)
            return
        if o.error and mono - o.failed_at < RETRY_S:
            return
        same = (target == o.written and not o.error and (target is None and o.released or target is not None))
        if same and mono - o.written_at < self.rewrite_s:
            o.state = "failsafe" if o.wanted is None else "written"
            return
        self._writes[o.point] = asyncio.create_task(self._write(o, point, target))

    async def _write(self, o: OutputState, point, value: Any) -> None:
        from devine_bms.commands.writer import WriteError, write_point

        failsafe = o.wanted is None
        try:
            await write_point(self.runtime, point, value, LOGIC_PRIORITY)
        except WriteError as exc:
            if o.error != str(exc):
                log.warning("logic output %s: the write failed: %s", o.point, exc)
            o.state, o.error, o.failed_at = "error", str(exc), time.monotonic()
            return
        o.written, o.written_at, o.error = value, time.monotonic(), None
        o.released = value is None
        o.state = ("released" if value is None else "failsafe") if failsafe else "written"

    async def _failsafe_all(self, outputs: list[OutputState], why: str) -> None:
        for o in outputs:
            o.wanted = None
            point = self._point(o.point)
            if point is None or o.failsafe.mode == "hold":
                continue
            if o.failsafe.mode == "release" and not isinstance(point.address, BacnetAddress):
                continue
            from devine_bms.commands.writer import WriteError, write_point

            value = None if o.failsafe.mode == "release" else _coerce(point, o.failsafe.value)
            try:
                await write_point(self.runtime, point, value, LOGIC_PRIORITY)
                o.written, o.released = value, value is None
            except WriteError as exc:
                log.warning("logic output %s: the fail-safe write failed (%s): %s", o.point, why, exc)

    def value_for(self, point) -> Any:
        """Logic's value for a point now, or None (CommandService asks when hand mode ends)."""
        o = self.outputs.get(point.name)
        if o is None:
            return None
        if o.wanted is not None:
            return _coerce(point, o.wanted)
        return _coerce(point, o.failsafe.value) if o.failsafe.mode == "value" else None

    def owns(self, point) -> bool:
        return point.name in self.outputs

    # ---------- variables published as points (milestone 10C) ----------

    def _point_var(self, point) -> Var | None:
        a = point.address
        return self.vars.get(("diagram", a.diagram, a.variable)) if a.diagram else self.vars.get(("site", "", a.variable))

    async def write_variable_point(self, point, value: Any) -> None:
        """A command to a variable's point sets the variable (within its limits)."""
        from devine_bms.commands.writer import WriteError

        v = self._point_var(point)
        if v is None:
            raise WriteError(f"there is no logic variable '{point.address.variable}'")
        if value is None:
            raise WriteError("a logic variable has no priorities to release")
        if v.type == "number":
            value = float(value)
            if (v.min is not None and value < v.min) or (v.max is not None and value > v.max):
                raise WriteError(f"the variable allows {v.min} to {v.max}")
        elif v.type == "bool":
            value = bool(value)
        else:
            value = str(value)
        v.value, v.dirty = value, True
        await self.flush_variables(force=True)
        self.push_points(force=True)

    def push_points(self, force: bool = False) -> None:
        """Give each variable's point its value: on a change, and at least every 10 seconds so it
        does not go Stale."""
        tracker = getattr(self.runtime, "tracker", None)
        if tracker is None:
            return
        mono = time.monotonic()
        for p in self.runtime.config.points:
            if not isinstance(p.address, VariableAddress):
                continue
            v = self._point_var(p)
            value = None if v is None else v.value
            last = self._pushed.get(p.id)
            if not force and last is not None and last[0] == value and mono - last[1] < 10:
                continue
            self._pushed[p.id] = (value, mono)
            if v is None:
                tracker.bad(p, Status.FAULT, f"no logic variable '{p.address.variable}'")
            elif value is None:
                tracker.bad(p, Status.FAULT, "the variable has no value yet")
            else:
                try:
                    tracker.good(p, _coerce(p, value) if p.kind != Kind.NUMBER else float(value))
                except (TypeError, ValueError) as exc:
                    tracker.bad(p, Status.FAULT, str(exc))

    # ---------- variables ----------

    def variable(self, diagram: str, name: str) -> Var | None:
        return self.vars.get(("diagram", diagram, name)) or self.vars.get(("site", "", name))

    def _set_var(self, diagram: str, name: str, value: Any) -> None:
        v = self.variable(diagram, name)
        if v is None or v.kind != "memory":
            return
        if v.type == "number":
            value = float(value)
            if v.min is not None:
                value = max(value, v.min)
            if v.max is not None:
                value = min(value, v.max)
        if v.value != value:
            v.value, v.dirty = value, True

    async def flush_variables(self, force: bool = False) -> None:
        """Save changed variables (at most every 5 seconds), so they survive a restart."""
        if not force and time.monotonic() - self._last_flush < 5:
            return
        self._last_flush = time.monotonic()
        dirty = [v for v in self.vars.values() if v.dirty]
        if not dirty or self.site_id is None:
            return
        async with self.sessions() as session, session.begin():
            for v in dirty:
                row = await session.scalar(select(db.LogicVariable).where(
                    db.LogicVariable.site_id == self.site_id, db.LogicVariable.scope == v.scope,
                    db.LogicVariable.owner == v.owner, db.LogicVariable.name == v.name))
                if row is None:
                    row = db.LogicVariable(site_id=self.site_id, scope=v.scope, owner=v.owner, name=v.name)
                    session.add(row)
                row.type, row.kind, row.unit, row.dis, row.min, row.max = v.type, v.kind, v.unit, v.dis, v.min, v.max
                row.value = {"v": v.value}
                v.dirty = False

    # ---------- reading ----------

    def live(self, name: str) -> dict:
        r = self.running.get(name)
        status = self.status.get(name, {"state": "unknown", "issues": []})
        out: dict = {"name": name, **status, "version": r.version if r else None,
                     "at": r.last_at.isoformat() if r and r.last_at else None, "blocks": {}, "outputs": {},
                     "variables": [v.as_dict() for (scope, owner, _n), v in self.vars.items()
                                   if (scope == "diagram" and owner == name)]}
        if r is None or r.last is None:
            return out
        for bid in r.plan.order:
            info = dict(r.last.info.get(bid, {}))
            out["blocks"][bid] = {
                "inputs": info.pop("inputs", {}),
                "outputs": {p.name: r.last.values.get((bid, p.name)) for p in r.plan.outputs[bid]},
                **({"error": r.last.errors[bid]} if bid in r.last.errors else {}),
                **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in info.items()},
            }
        for o in self.outputs.values():
            if o.diagram == name:
                out["outputs"][o.point] = {"wanted": o.wanted, "state": o.state, "written": o.written,
                                           "error": o.error, "failsafe": o.failsafe.model_dump(mode="json")}
        return out

    # ---------- test runs ----------

    def test(self, body: dict, name: str, inputs: dict[str, Any], steps: int = 10, dt: float = 1.0,
             schedules: dict[str, bool] | None = None, start: datetime | None = None) -> dict:
        """Run a diagram (normally the draft) against simulated inputs, writing nothing. Points not
        given read their live values; a given value of null is a bad (offline) point."""
        p, issues = plan(body, self.site_view(exclude=name))
        if p is None or errors(issues):
            return {"ok": True, "valid": False, "issues": [i.as_dict() for i in issues]}
        sim_vars = {k: Var(**v.__dict__) for k, v in self.vars.items()}
        for v in p.diagram.variables:
            sim_vars.setdefault(("diagram", name, v.name), Var("diagram", name, v.name, v.type, v.kind, v.value))
        engine = self

        class SimReader:
            def point(self, n: str) -> Any:
                if n in inputs:
                    return inputs[n]
                return _Reader(engine, name).point(n)

            def variable(self, n: str) -> Any:
                v = sim_vars.get(("diagram", name, n)) or sim_vars.get(("site", "", n))
                return v.value if v else None

            def schedule(self, n: str) -> bool | None:
                if schedules and n in schedules:
                    return schedules[n]
                return _Reader(engine, name).schedule(n)

        ev = Evaluator(p, self.place)
        t = start or datetime.now(UTC)
        result = []
        for k in range(max(1, min(steps, 3600))):
            step = ev.step(t, SimReader())
            for vw in step.variables:
                v = sim_vars.get(("diagram", name, vw.variable)) or sim_vars.get(("site", "", vw.variable))
                if v is not None and v.kind == "memory":
                    v.value = vw.value
            result.append({
                "t": round(k * dt, 3),
                "outputs": {o.point: o.value if o.value is not None else _failsafe_text(o.failsafe)
                            for o in step.outputs},
                "values": {f"{b}.{port}": v for (b, port), v in step.values.items()},
                "errors": step.errors,
            })
            t += timedelta(seconds=dt)
        return {"ok": True, "valid": True, "issues": [i.as_dict() for i in issues], "steps": result}


class _Reader:
    def __init__(self, engine: LogicEngine, diagram: str) -> None:
        self.engine = engine
        self.diagram = diagram

    def point(self, name: str) -> Any:
        p = self.engine._point(name)
        if p is None:
            return None
        live = self.engine.runtime.cache.get(p.id)
        return live.value if live is not None and live.status == Status.OK else None

    def variable(self, name: str) -> Any:
        v = self.engine.variable(self.diagram, name)
        return v.value if v else None

    def schedule(self, name: str) -> bool | None:
        s = self.engine.schedules
        return s.occupied(name) if s is not None else None


@dataclass
class _Write:
    block: str
    settings_point: str
    failsafe: Any


def _writes(p: Plan) -> list[_Write]:
    return [_Write(b.id, p.settings[b.id].point, p.settings[b.id].failsafe)
            for b in p.diagram.blocks if b.type == "point_write" and b.id in p.settings]


def _coerce(point, value: Any) -> Any:
    """A value of the point's kind, a number clamped to its write limits (main spec 5.6)."""
    if point.kind == Kind.BOOL:
        return bool(value)
    if point.kind == Kind.STR:
        text = str(value)
        if point.enum and text not in point.enum:
            raise ValueError(f"'{text}' is not one of {list(point.enum)}")
        return text
    number = float(value)
    wl = point.write_limits
    if wl is not None:
        number = min(max(number, wl.min), wl.max)
    return number


def _failsafe_text(f) -> str:
    return "fail-safe: " + ("release to device" if f.mode == "release" else "hold" if f.mode == "hold"
                            else f"value {f.value}")


def _zone(tz: str) -> ZoneInfo:
    from devine_bms.api.timezones import iana_zone

    return iana_zone(tz)



INSTANCE_SUFFIX = "__tpl"


def template_instances(setup: dict) -> list[tuple[str, dict, dict, list[str]]]:
    """Each equipment or room on a template version with logic: (diagram name, meta, the
    diagram with roles mapped to the instance's bound points, roles it uses that are not bound)."""
    import copy

    templates = {(t["id"], t["version"]): t for t in setup.get("equipTemplates") or []}
    from devine_bms.templates.equip import builtin_equip_templates

    for t in builtin_equip_templates().values():
        templates.setdefault((t.id, t.version), t.model_dump(mode="json", by_alias=True))
    out = []
    for kind, items in (("equip", setup.get("equips") or []), ("space", setup.get("spaces") or [])):
        for item in items:
            link = item.get("template")
            t = templates.get((link["id"], link["version"])) if link else None
            if not t or not t.get("logic"):
                continue
            name = f"{item['name']}{INSTANCE_SUFFIX}"
            bindings = link.get("bindings") or {}
            body = copy.deepcopy(t["logic"])
            missing = []
            for b in body.get("blocks", []):
                s = b.get("settings") or {}
                if b.get("type") in ("point_read", "point_write") and s.get("role"):
                    role = s.pop("role")
                    if role in bindings:
                        s["point"] = bindings[role]
                    elif role not in missing:
                        missing.append(role)
                    b["settings"] = s
            body.update({"name": name, "dis": f"{item.get('dis', item['name'])}: {t.get('dis', t['id'])}",
                         "scope": "equipment" if kind == "equip" else "site",
                         "equip": item["name"] if kind == "equip" else None, "template": None})
            meta = {"equip": item["name"], "dis": item.get("dis", item["name"]), "kind": kind, "template": t["id"],
                    "templateVersion": t["version"]}
            out.append((name, meta, body, missing))
    return out
