"""Running a checked diagram one cycle at a time (main spec 5.3.1 Execution; milestone 10A).

The same code runs live diagrams (engine.py) and test runs against simulated inputs. A value is
`None` when it is bad (main spec 5.2.1): a block with a bad input gives a bad output, and an
output block with a bad input goes to its fail-safe. Timing blocks on a loop read their input
from the previous cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from devine_bms.logic.astro import daylight
from devine_bms.logic.model import Plan

MISSING = object()  # an optional input left unwired with no default (PID's track)


class Reader(Protocol):
    def point(self, name: str) -> Any: ...  # the value, or None when not Good
    def variable(self, name: str) -> Any: ...
    def schedule(self, name: str) -> bool | None: ...


@dataclass
class Place:
    tz: ZoneInfo
    lat: float | None = None
    lng: float | None = None


@dataclass
class Output:
    block: str
    point: str
    value: Any  # None when the input is bad: apply the fail-safe
    failsafe: Any  # the block's Failsafe settings


@dataclass
class VariableWrite:
    block: str
    variable: str
    value: Any


@dataclass
class Step:
    values: dict[tuple[str, str], Any] = field(default_factory=dict)  # (block, port) -> value
    info: dict[str, dict] = field(default_factory=dict)  # block -> what Live mode shows
    outputs: list[Output] = field(default_factory=list)
    variables: list[VariableWrite] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # block -> why it failed this cycle


def _num(x: Any) -> float:
    return float(x)


class Evaluator:
    def __init__(self, plan: Plan, place: Place) -> None:
        self.plan = plan
        self.place = place
        self.types = {b.id: b.type for b in plan.diagram.blocks}
        self.state: dict[str, dict] = {b: {} for b in plan.order}
        self.previous: dict[tuple[str, str], Any] = {}
        self.last_time: datetime | None = None

    def step(self, now: datetime, reader: Reader) -> Step:
        dt = 0.0 if self.last_time is None else max(0.0, (now - self.last_time).total_seconds())
        self.last_time = now
        out = Step()
        for bid in self.plan.order:
            s = self.plan.settings[bid]
            inputs: dict[str, Any] = {}
            for port in self.plan.inputs[bid]:
                src = self.plan.feeds.get((bid, port.name))
                if src is not None:
                    # Not run yet this cycle only on a loop through a timing block: last cycle's value.
                    inputs[port.name] = out.values[src] if src in out.values else self.previous.get(src)
                elif port.name in s.inputs:
                    inputs[port.name] = s.inputs[port.name]
                elif port.default is not None:
                    inputs[port.name] = port.default
                else:
                    inputs[port.name] = MISSING
            try:
                results, info = self._run(bid, self.types[bid], s, inputs, now, dt, reader, out)
            except Exception as exc:  # noqa: BLE001 -- a failing block gives bad outputs (its outputs fail safe)
                out.errors[bid] = str(exc) or type(exc).__name__
                results, info = {p.name: None for p in self.plan.outputs[bid]}, {}
                if self.types[bid] == "point_write":
                    out.outputs.append(Output(bid, s.point, None, s.failsafe))
            for port, value in results.items():
                out.values[(bid, port)] = value
            info["inputs"] = {k: (None if v is MISSING else v) for k, v in inputs.items()}
            out.info[bid] = info
        self.previous = dict(out.values)
        return out

    # ---------- the blocks ----------

    def _run(self, bid: str, kind: str, s: Any, i: dict, now: datetime, dt: float, reader: Reader,
             out: Step) -> tuple[dict, dict]:
        st = self.state[bid]
        bad = any(v is None for k, v in i.items())

        if kind == "point_read":
            value = reader.point(s.point)
            if value is None:
                return {"out": s.fallback if s.on_bad == "fallback" else None}, {"bad": True}
            return {"out": value}, {}
        if kind == "point_write":
            out.outputs.append(Output(bid, s.point, i["in"], s.failsafe))
            return {}, {}
        if kind == "variable_read":
            return {"out": reader.variable(s.variable)}, {}
        if kind == "variable_write":
            if i["in"] is not None and i.get("enable") is True:
                out.variables.append(VariableWrite(bid, s.variable, i["in"]))
            return {}, {}
        if kind == "schedule":
            return {"out": reader.schedule(s.schedule)}, {}
        if bad and kind not in ("pid",):
            # A timing block forgets its timing while its input is bad, so it starts cleanly.
            if kind in ("delay_on", "delay_off", "min_on_off", "limit"):
                st.clear()
            return {p.name: None for p in self.plan.outputs[bid]}, {"bad": True}

        if kind == "and":
            return {"out": all(i[f"in{n}"] for n in range(1, s.count + 1))}, {}
        if kind == "or":
            return {"out": any(i[f"in{n}"] for n in range(1, s.count + 1))}, {}
        if kind == "not":
            return {"out": not i["in"]}, {}
        if kind == "compare":
            a, b, db, op = _num(i["a"]), _num(i["b"]), s.deadband, s.op
            last = st.get("out")
            if op in (">", ">="):
                edge = b - db if last else b + db
                result = a > edge if op == ">" else a >= edge
            elif op in ("<", "<="):
                edge = b + db if last else b - db
                result = a < edge if op == "<" else a <= edge
            elif op == "==":
                result = abs(a - b) <= db
            else:
                result = abs(a - b) > db
            st["out"] = result
            return {"out": result}, {}
        if kind == "limit":
            lo, hi, x = _num(i["min"]), _num(i["max"]), _num(i["in"])
            want = min(max(x, lo), hi)
            clamping = "min" if x < lo else "max" if x > hi else None
            last = st.get("out")
            if s.rate and last is not None and dt > 0:
                step = s.rate * dt
                if abs(want - last) > step:
                    want = last + step if want > last else last - step
                    clamping = clamping or "rate"
            st["out"] = want
            return {"out": want}, {"clamping": clamping}
        if kind == "scale":
            x = _num(i["in"])
            y = s.out_min + (x - s.in_min) * (s.out_max - s.out_min) / (s.in_max - s.in_min)
            if s.clamp:
                y = min(max(y, min(s.out_min, s.out_max)), max(s.out_min, s.out_max))
            return {"out": y}, {}
        if kind == "delay_on":
            t = _num(i["time"])
            if i["in"]:
                st.setdefault("since", now)
                held = (now - st["since"]).total_seconds()
                return {"out": held >= t}, {"remaining": max(0.0, t - held)}
            st.pop("since", None)
            return {"out": False}, {}
        if kind == "delay_off":
            t = _num(i["time"])
            if i["in"]:
                st["on"] = True
                st.pop("off_since", None)
                return {"out": True}, {}
            if not st.get("on"):
                return {"out": False}, {}
            st.setdefault("off_since", now)
            held = (now - st["off_since"]).total_seconds()
            if held >= t:
                st["on"] = False
                return {"out": False}, {}
            return {"out": True}, {"remaining": t - held}
        if kind == "min_on_off":
            current = st.get("out", False)
            changed = st.get("changed")
            need = _num(i["minOn"]) if current else _num(i["minOff"])
            held = (now - changed).total_seconds() if changed else float("inf")
            if bool(i["in"]) != current and held >= need:
                current = bool(i["in"])
                st["out"], st["changed"] = current, now
                held, need = 0.0, (_num(i["minOn"]) if current else _num(i["minOff"]))
            remaining = max(0.0, need - held) if bool(i["in"]) != current else 0.0
            return {"out": current}, {"remaining": remaining}
        if kind == "astro":
            if self.place.lat is None or self.place.lng is None:
                raise ValueError("the site has no latitude and longitude")
            is_day, nxt = daylight(now, self.place.lat, self.place.lng, self.place.tz,
                                   s.sunrise_offset, s.sunset_offset)
            on = is_day if s.active == "day" else not is_day
            return {"out": bool(i["enable"]) and on}, {"next": nxt.isoformat() if nxt else None}
        if kind == "pid":
            return self._pid(st, s, i, dt)
        if kind == "manual_auto":
            return {"out": i["manual"] if i["mode"] else i["auto"]}, {"mode": "Manual" if i["mode"] else "Auto"}
        raise ValueError(f"unknown block type {kind}")

    def _pid(self, st: dict, s: Any, i: dict, dt: float) -> tuple[dict, dict]:
        lo, hi = s.out_min, s.out_max
        track = i.get("track", MISSING)
        if i["pv"] is None or i["sp"] is None or i["enable"] is None:
            st.pop("last_e", None)
            return {"out": None}, {"bad": True}
        e = (_num(i["pv"]) - _num(i["sp"])) if s.action == "direct" else (_num(i["sp"]) - _num(i["pv"]))
        if not i["enable"]:
            # Tracking: the output follows `track` (or holds), and the integral is set so that
            # enabling again starts from here (bumpless).
            held = st.get("out", lo)
            value = held if track is MISSING or track is None else min(max(_num(track), lo), hi)
            st["out"], st["integral"], st["last_e"], st["acc"] = value, value - s.kp * e, e, 0.0
            return {"out": value}, {"mode": "tracking"}
        st["acc"] = st.get("acc", 0.0) + dt
        if "out" in st and st["acc"] < s.sample:
            return {"out": st["out"]}, {}  # between samples: hold
        # The first run has no elapsed time, so it does not integrate.
        step_dt, st["acc"] = st["acc"], 0.0
        integral = st.get("integral", lo)
        increment = (s.kp * step_dt / s.ti * e) if s.ti > 0 else 0.0
        derivative = (s.kp * s.td * (e - st["last_e"]) / step_dt) if s.td > 0 and "last_e" in st and step_dt > 0 else 0.0
        raw = s.kp * e + integral + increment + derivative
        # Anti-windup: the integral does not grow while the output is held at a limit.
        if (raw > hi and increment > 0) or (raw < lo and increment < 0):
            increment = 0.0
        integral += increment
        value = min(max(s.kp * e + integral + derivative, lo), hi)
        st["integral"], st["last_e"], st["out"] = integral, e, value
        return {"out": value}, {"error": e}
