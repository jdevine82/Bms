// The v1 blocks on the client (the same as backend/src/devine_bms/logic/model.py): groups,
// ports and their types, and default settings for a new block.
import type { Block, VType } from "./logicApi";

export interface Port { name: string; type: VType | null; required: boolean; default?: unknown }
export interface TypeInfo {
  pointType: (name: string) => VType | null;
  variableType: (name: string) => VType | null;
}

interface Spec {
  group: string;
  dis: string;
  inputs: (s: Record<string, any>, t: TypeInfo) => Port[];
  outputs: (s: Record<string, any>, t: TypeInfo) => Port[];
  defaults: Record<string, any>;
  help: string;
}

const P = (name: string, type: VType | null, required = true, dflt?: unknown): Port => ({ name, type, required, default: dflt });
const num = (...names: string[]) => () => names.map((n) => P(n, "number"));
const out = (type: VType) => () => [P("out", type)];

export const BLOCKS: Record<string, Spec> = {
  point_read: { group: "Read and write", dis: "Point read", inputs: () => [], outputs: (s, t) => [P("out", t.pointType(s.role || s.point || ""))],
    defaults: { point: "", onBad: "fallback", fallback: null }, help: "A point's live value; when it is not Good, a fallback value or the fail-safe." },
  point_write: { group: "Read and write", dis: "Point write", inputs: (s, t) => [P("in", t.pointType(s.role || s.point || ""))], outputs: () => [],
    defaults: { point: "", failsafe: { mode: "release" } }, help: "Writes a point at priority 10. Its fail-safe applies when the input is bad or logic stops." },
  variable_read: { group: "Read and write", dis: "Variable read", inputs: () => [], outputs: (s, t) => [P("out", t.variableType(s.variable ?? ""))],
    defaults: { variable: "" }, help: "A variable's value." },
  variable_write: { group: "Read and write", dis: "Variable write", inputs: (s, t) => [P("in", t.variableType(s.variable ?? "")), P("enable", "bool", false, true)],
    outputs: () => [], defaults: { variable: "" }, help: "Writes a memory variable (kept through a restart)." },
  schedule: { group: "Read and write", dis: "Schedule", inputs: () => [], outputs: out("bool"), defaults: { schedule: "" },
    help: "True while the schedule is Occupied." },
  and: { group: "Logic", dis: "AND", inputs: (s) => Array.from({ length: s.count ?? 2 }, (_, i) => P(`in${i + 1}`, "bool")),
    outputs: out("bool"), defaults: { count: 2 }, help: "True when every input is true." },
  or: { group: "Logic", dis: "OR", inputs: (s) => Array.from({ length: s.count ?? 2 }, (_, i) => P(`in${i + 1}`, "bool")),
    outputs: out("bool"), defaults: { count: 2 }, help: "True when any input is true." },
  not: { group: "Logic", dis: "NOT", inputs: () => [P("in", "bool")], outputs: out("bool"), defaults: {}, help: "The opposite." },
  compare: { group: "Logic", dis: "Compare", inputs: num("a", "b"), outputs: out("bool"), defaults: { op: ">", deadband: 0 },
    help: "a against b; a deadband stops chatter (on above b + deadband, off below b − deadband)." },
  limit: { group: "Math", dis: "Limit", inputs: num("in", "min", "max"), outputs: out("number"), defaults: { rate: null },
    help: "Holds a number between min and max; an optional rate limit (units per second)." },
  scale: { group: "Math", dis: "Scale", inputs: num("in"), outputs: out("number"),
    defaults: { inMin: 0, inMax: 100, outMin: 0, outMax: 100, clamp: true }, help: "A straight-line conversion between ranges (swap the output to invert)." },
  delay_on: { group: "Timing", dis: "Delay on", inputs: () => [P("in", "bool"), P("time", "number")], outputs: out("bool"),
    defaults: { inputs: { time: 10 } }, help: "True only after the input has been true for the time (s)." },
  delay_off: { group: "Timing", dis: "Delay off", inputs: () => [P("in", "bool"), P("time", "number")], outputs: out("bool"),
    defaults: { inputs: { time: 10 } }, help: "Stays true for the time (s) after the input goes false." },
  min_on_off: { group: "Timing", dis: "Minimum on/off", inputs: () => [P("in", "bool"), P("minOn", "number"), P("minOff", "number")],
    outputs: out("bool"), defaults: { inputs: { minOn: 60, minOff: 60 } }, help: "Holds the output on or off for a minimum time (s)." },
  astro: { group: "Timing", dis: "Astro time switch", inputs: () => [P("enable", "bool", false, true)], outputs: out("bool"),
    defaults: { active: "night", sunsetOffset: 0, sunriseOffset: 0 }, help: "True at night (or by day) at the site, from sunrise and sunset with offsets." },
  pid: { group: "Control", dis: "PID", inputs: () => [P("pv", "number"), P("sp", "number"), P("enable", "bool", false, true), P("track", "number", false)],
    outputs: out("number"), defaults: { kp: 1, ti: 60, td: 0, outMin: 0, outMax: 100, action: "reverse", sample: 1 },
    help: "Drives the output so the process value reaches the setpoint; tracks while disabled (bumpless)." },
  manual_auto: { group: "Control", dis: "Manual/auto", inputs: (s) => [P("auto", s.valueType ?? "number"), P("manual", s.valueType ?? "number"), P("mode", "bool", false, false)],
    outputs: (s) => [P("out", s.valueType ?? "number")], defaults: { valueType: "number" }, help: "The auto value in Auto, the manual value in Manual (mode true)." },
};

export const GROUPS = ["Read and write", "Logic", "Math", "Timing", "Control"];
export const TIMING = new Set(["delay_on", "delay_off", "min_on_off"]);

export function portsOf(b: Block, t: TypeInfo): { inputs: Port[]; outputs: Port[] } {
  const spec = BLOCKS[b.type];
  if (!spec) return { inputs: [], outputs: [] };
  const s = b.settings ?? {};
  return { inputs: spec.inputs(s, t), outputs: spec.outputs(s, t) };
}

export const KIND_TYPE: Record<string, VType> = { Number: "number", Bool: "bool", Str: "text" };
