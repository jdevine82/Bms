// One block on the canvas: its name, a one-line summary of its settings, input ports on the
// left and output ports on the right (shape and colour by type), and in Live mode its values,
// timers, clamping and errors.
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { Block, Issue, LiveBlock } from "./logicApi";
import { BLOCKS, type Port } from "./blocks";

export interface BlockData extends Record<string, unknown> {
  block: Block;
  inputs: Port[];
  outputs: Port[];
  wired: Set<string>; // input ports with a wire
  issues: Issue[];
  live?: LiveBlock;
  output?: { state: string; written: unknown; error: string | null };
  units: Record<string, string | null>; // point name -> unit
}

export function fmt(v: unknown, unit?: string | null): string {
  if (v === null || v === undefined) return "bad";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return `${Number.isInteger(v) ? v : v.toFixed(Math.abs(v) >= 100 ? 1 : 2)}${unit ? ` ${unit}` : ""}`;
  return String(v);
}

function summary(b: Block): string {
  const s = b.settings ?? {};
  switch (b.type) {
    case "point_read": return s.role ? `role ${s.role}` : s.point || "(choose a point)";
    case "point_write": return `${s.role ? `role ${s.role}` : s.point || "(choose a point)"} · fail-safe: ${s.failsafe?.mode === "value" ? fmt(s.failsafe.value) : s.failsafe?.mode ?? "release"}`;
    case "variable_read": case "variable_write": return s.variable || "(choose a variable)";
    case "schedule": return s.schedule || "(choose a schedule)";
    case "compare": return `a ${s.op ?? ">"} b${s.deadband ? ` ± ${s.deadband}` : ""}`;
    case "limit": return s.rate ? `rate ${s.rate}/s` : "";
    case "scale": return `${s.inMin ?? 0}…${s.inMax ?? 100} → ${s.outMin ?? 0}…${s.outMax ?? 100}${s.unit ? ` ${s.unit}` : ""}`;
    case "astro": return `${s.active ?? "night"}${s.sunsetOffset ? `, sunset ${s.sunsetOffset > 0 ? "+" : ""}${s.sunsetOffset} min` : ""}${s.sunriseOffset ? `, sunrise ${s.sunriseOffset > 0 ? "+" : ""}${s.sunriseOffset} min` : ""}`;
    case "pid": return `Kp ${s.kp ?? 1} · Ti ${s.ti ?? 60} s · ${s.action ?? "reverse"}`;
    case "manual_auto": return s.valueType ?? "number";
    default: return "";
  }
}

const SHAPE: Record<string, string> = { bool: "port-bool", number: "port-number", text: "port-text" };

export function BlockNode({ data, selected }: NodeProps) {
  const d = data as BlockData;
  const b = d.block;
  const spec = BLOCKS[b.type];
  const errors = d.issues.filter((i) => i.level === "error");
  const live = d.live;
  const inputValue = (p: Port) => {
    const fixed = b.settings?.inputs?.[p.name];
    if (!d.wired.has(p.name) && fixed !== undefined) return `= ${fmt(fixed)}`;
    if (!d.wired.has(p.name) && !p.required) return p.default !== undefined ? `= ${fmt(p.default)}` : "";
    return "";
  };
  return (
    <div className={`lnode${selected ? " selected" : ""}${errors.length ? " has-error" : ""}${live?.error || live?.bad ? " is-bad" : ""}`}
         data-testid={`block-${b.id}`} title={spec?.help}>
      <div className={`lnode-head group-${(spec?.group ?? "x").split(" ")[0].toLowerCase()}`}>
        <span>{spec?.dis ?? b.type}</span>
        <code>{b.id}</code>
      </div>
      {(b.label || summary(b)) && <div className="lnode-sub">{b.label ? <strong>{b.label}</strong> : null}{b.label && summary(b) ? " · " : ""}{summary(b)}</div>}
      <div className="lnode-ports">
        <div className="lnode-in">
          {d.inputs.map((p) => (
            <div key={p.name} className="lnode-port">
              <Handle type="target" position={Position.Left} id={p.name} className={`lhandle ${SHAPE[p.type ?? ""] ?? "port-any"}`}
                      title={`${p.name} (${p.type ?? "?"})`} />
              <span>{p.name}</span>
              <em>{live ? fmt(live.inputs?.[p.name]) : inputValue(p)}</em>
            </div>
          ))}
        </div>
        <div className="lnode-out">
          {d.outputs.map((p) => (
            <div key={p.name} className="lnode-port out">
              {live && <em className={`val-${String(live.outputs?.[p.name])}`}>{fmt(live.outputs?.[p.name], b.type === "point_read" ? d.units[b.settings?.point] : null)}</em>}
              <span>{p.name}</span>
              <Handle type="source" position={Position.Right} id={p.name} className={`lhandle ${SHAPE[p.type ?? ""] ?? "port-any"}`}
                      title={`${p.name} (${p.type ?? "?"})`} />
            </div>
          ))}
        </div>
      </div>
      {live && (
        <div className="lnode-live">
          {live.error && <span className="badge bad">Error: {live.error}</span>}
          {live.bad && !live.error && <span className="badge bad">Bad input</span>}
          {typeof live.remaining === "number" && live.remaining > 0 && <span className="badge">{Math.ceil(live.remaining)} s left</span>}
          {live.clamping && <span className="badge warn">Clamping: {live.clamping}</span>}
          {live.mode && <span className="badge">{live.mode}</span>}
          {live.next && <span className="badge">Next switch {new Date(live.next).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
          {d.output && <span className={`badge ${d.output.state === "written" ? "ok" : d.output.state === "error" ? "bad" : "warn"}`}>
            {d.output.state === "written" ? `Written ${fmt(d.output.written, d.units[b.settings?.point])}` : d.output.state === "hand" ? "Hand mode (logic waits)"
              : d.output.state === "error" ? `Write failed: ${d.output.error}` : `Fail-safe: ${d.output.state}`}
          </span>}
        </div>
      )}
      {!live && errors.length > 0 && <div className="lnode-issue" role="note">{errors[0].message}{errors.length > 1 ? ` (+${errors.length - 1})` : ""}</div>}
    </div>
  );
}
