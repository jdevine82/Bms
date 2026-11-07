// The properties panel (main spec 5.3.1 Layout): the selected block's settings; with nothing
// selected, the diagram's own settings and variables; the check results; and in Live mode the
// variables' values (admins can set constants).
import { useId, useState } from "react";
import type { Point } from "../api";
import { SetupError } from "../setup/api";
import { BLOCKS, portsOf, type TypeInfo } from "./blocks";
import type { Block, Diagram, Issue, Live, SiteVariable, Value, VariableDef } from "./logicApi";
import * as api from "./logicApi";

interface Props {
  diagram: Diagram;
  block: Block | null;
  mode: "edit" | "live";
  admin: boolean;
  points: Point[];
  variables: SiteVariable[];
  schedules: { name: string; dis: string }[];
  equips: { name: string; dis: string }[];
  issues: Issue[];
  live: Live | null;
  typeInfo: TypeInfo;
  onDiagram: (d: Diagram) => void;
  onSelect: (id: string) => void;
  onVariables: () => void;
}

const num = (v: string): number | undefined => (v.trim() === "" || Number.isNaN(Number(v)) ? undefined : Number(v));

function Field({ label, children, hint }: { label: string; children: (id: string) => React.ReactNode; hint?: React.ReactNode }) {
  const id = useId();
  return (
    <div className="prop">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <span className="muted small">{hint}</span>}
    </div>
  );
}

function ValueInput({ id, type, value, onChange }: { id: string; type: string | null; value: unknown; onChange: (v: Value | undefined) => void }) {
  if (type === "bool") {
    return (
      <select id={id} value={value === undefined || value === null ? "" : String(value)}
              onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "true")}>
        <option value="">(none)</option><option value="true">true</option><option value="false">false</option>
      </select>
    );
  }
  if (type === "number") {
    return <input id={id} type="number" step="any" value={value === undefined || value === null ? "" : String(value)}
                  onChange={(e) => onChange(num(e.target.value))} />;
  }
  return <input id={id} value={value === undefined || value === null ? "" : String(value)} onChange={(e) => onChange(e.target.value || undefined)} />;
}

export function Properties(props: Props) {
  const { diagram, block, mode, issues, onSelect } = props;
  const errors = issues.filter((i) => i.level === "error");
  const warnings = issues.filter((i) => i.level === "warning");
  return (
    <aside className="logic-props" aria-label="Properties">
      {mode === "edit" && block && <BlockProps {...props} block={block} />}
      {mode === "edit" && !block && <DiagramProps {...props} />}
      {mode === "live" && <LiveVariables {...props} />}
      {mode === "edit" && (
        <section aria-label="Checks">
          <h3>Checks</h3>
          {issues.length === 0 && <p className="muted small">{diagram.blocks.length ? "No problems." : "Add blocks from the left."}</p>}
          <ul className="issue-list">
            {[...errors, ...warnings].map((i, k) => (
              <li key={k} className={i.level}>
                <span className="issue-level">{i.level === "error" ? "Error" : "Warning"}</span>{" "}
                {i.block ? <button type="button" className="link" onClick={() => onSelect(i.block!)}>{i.block}</button> : null}
                {i.block ? ": " : ""}{i.message}
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}

function BlockProps({ diagram, block: b, points, variables, schedules, typeInfo, onDiagram }: Props & { block: Block }) {
  const spec = BLOCKS[b.type];
  const s = b.settings ?? {};
  const set = (patch: Record<string, unknown>) =>
    onDiagram({ ...diagram, blocks: diagram.blocks.map((x) => (x.id === b.id ? { ...x, settings: { ...s, ...patch } } : x)) });
  const setInput = (port: string, v: Value | undefined) => {
    const inputs = { ...(s.inputs ?? {}) };
    if (v === undefined) delete inputs[port]; else inputs[port] = v;
    set({ inputs });
  };
  const ports = portsOf(b, typeInfo);
  const wired = new Set(diagram.wires.filter((w) => w.to.block === b.id).map((w) => w.to.port));
  const pointList = useId();
  const writable = b.type === "point_write";
  const choices = points.filter((p) => !writable || p.writable);
  const known = points.find((p) => p.name === s.point);
  const allVars = [...diagram.variables.map((v) => ({ name: v.name, label: `${v.name} (this diagram, ${v.kind})`, kind: v.kind })),
                   ...variables.filter((v) => v.scope === "site").map((v) => ({ name: v.name, label: `${v.name} (site, ${v.kind})`, kind: v.kind }))];

  return (
    <section aria-label="Block">
      <h3>{spec?.dis ?? b.type} <code className="small">{b.id}</code></h3>
      {spec && <p className="muted small">{spec.help}</p>}
      <Field label="Label">{(id) => <input id={id} value={b.label ?? ""} maxLength={80}
        onChange={(e) => onDiagram({ ...diagram, blocks: diagram.blocks.map((x) => (x.id === b.id ? { ...x, label: e.target.value || undefined } : x)) })} />}</Field>

      {(b.type === "point_read" || b.type === "point_write") && (
        <Field label="Point" hint={s.point ? (known ? `${known.dis}${known.unit ? ` (${known.unit})` : ""}` : "No such point") : undefined}>
          {(id) => (
            <>
              <input id={id} list={pointList} value={s.point ?? ""} autoComplete="off" placeholder="type to search"
                     onChange={(e) => set({ point: e.target.value.trim() })} />
              <datalist id={pointList}>{choices.map((p) => <option key={p.id} value={p.name}>{p.dis}</option>)}</datalist>
            </>
          )}
        </Field>
      )}
      {b.type === "point_read" && (
        <>
          <Field label="When the point is not Good">
            {(id) => (
              <select id={id} value={s.onBad ?? "fallback"} onChange={(e) => set({ onBad: e.target.value })}>
                <option value="fallback">Use a fallback value</option>
                <option value="failsafe">Go to the fail-safe (bad value)</option>
              </select>
            )}
          </Field>
          {(s.onBad ?? "fallback") === "fallback" && (
            <Field label="Fallback value">{(id) => <ValueInput id={id} type={typeInfo.pointType(s.point ?? "")} value={s.fallback}
                                                                onChange={(v) => set({ fallback: v ?? null })} />}</Field>
          )}
        </>
      )}
      {b.type === "point_write" && (
        <>
          <Field label="Fail-safe" hint="When the input is bad, a block fails, logic is disabled or the server stops.">
            {(id) => (
              <select id={id} value={s.failsafe?.mode ?? "release"} onChange={(e) => set({ failsafe: { ...(s.failsafe ?? {}), mode: e.target.value } })}>
                <option value="release">Release to the device (default)</option>
                <option value="hold">Hold the last value</option>
                <option value="value">Go to a value</option>
              </select>
            )}
          </Field>
          {s.failsafe?.mode === "value" && (
            <Field label="Fail-safe value">{(id) => <ValueInput id={id} type={typeInfo.pointType(s.point ?? "")} value={s.failsafe?.value}
                                                                  onChange={(v) => set({ failsafe: { ...s.failsafe, value: v ?? null } })} />}</Field>
          )}
          {known?.writeLimits && <p className="muted small">Write limits {known.writeLimits.min} to {known.writeLimits.max}: values outside are clamped.</p>}
        </>
      )}
      {(b.type === "variable_read" || b.type === "variable_write") && (
        <Field label="Variable" hint={b.type === "variable_write" ? "Only memory variables can be written by logic." : undefined}>
          {(id) => (
            <select id={id} value={s.variable ?? ""} onChange={(e) => set({ variable: e.target.value })}>
              <option value="">(choose)</option>
              {allVars.filter((v) => b.type === "variable_read" || v.kind === "memory").map((v) => <option key={v.label} value={v.name}>{v.label}</option>)}
              {s.variable && !allVars.some((v) => v.name === s.variable) && <option value={s.variable}>{s.variable} (missing)</option>}
            </select>
          )}
        </Field>
      )}
      {b.type === "schedule" && (
        <Field label="Schedule">
          {(id) => (
            <select id={id} value={s.schedule ?? ""} onChange={(e) => set({ schedule: e.target.value })}>
              <option value="">(choose)</option>
              {schedules.map((x) => <option key={x.name} value={x.name}>{x.dis}</option>)}
              {s.schedule && !schedules.some((x) => x.name === s.schedule) && <option value={s.schedule}>{s.schedule} (missing)</option>}
            </select>
          )}
        </Field>
      )}
      {(b.type === "and" || b.type === "or") && (
        <Field label="Inputs">{(id) => <input id={id} type="number" min={2} max={8} value={s.count ?? 2}
                                              onChange={(e) => set({ count: Math.max(2, Math.min(8, num(e.target.value) ?? 2)) })} />}</Field>
      )}
      {b.type === "compare" && (
        <div className="prop-row">
          <Field label="Test">{(id) => (
            <select id={id} value={s.op ?? ">"} onChange={(e) => set({ op: e.target.value })}>
              {["<", ">", "<=", ">=", "==", "!="].map((o) => <option key={o} value={o}>a {o} b</option>)}
            </select>)}</Field>
          <Field label="Deadband">{(id) => <input id={id} type="number" min={0} step="any" value={s.deadband ?? 0}
                                                 onChange={(e) => set({ deadband: num(e.target.value) ?? 0 })} />}</Field>
        </div>
      )}
      {b.type === "limit" && (
        <Field label="Rate limit (units per second)" hint="Empty for none.">{(id) => <input id={id} type="number" min={0} step="any" value={s.rate ?? ""}
                                                                         onChange={(e) => set({ rate: num(e.target.value) ?? null })} />}</Field>
      )}
      {b.type === "scale" && (
        <>
          <div className="prop-row two">
            {(["inMin", "inMax", "outMin", "outMax"] as const).map((k) => (
              <Field key={k} label={{ inMin: "In min", inMax: "In max", outMin: "Out min", outMax: "Out max" }[k]}>
                {(id) => <input id={id} type="number" step="any" value={s[k] ?? 0} onChange={(e) => set({ [k]: num(e.target.value) ?? 0 })} />}
              </Field>
            ))}
          </div>
          <label className="prop-inline"><input type="checkbox" checked={s.clamp ?? true} onChange={(e) => set({ clamp: e.target.checked })} /> Clamp to the output range</label>
          <Field label="Output unit">{(id) => <input id={id} value={s.unit ?? ""} onChange={(e) => set({ unit: e.target.value || null })} />}</Field>
        </>
      )}
      {b.type === "astro" && (
        <>
          <Field label="True">{(id) => (
            <select id={id} value={s.active ?? "night"} onChange={(e) => set({ active: e.target.value })}>
              <option value="night">At night</option><option value="day">By day</option>
            </select>)}</Field>
          <div className="prop-row two">
            <Field label="Sunset offset (min)" hint="Negative is before.">{(id) => <input id={id} type="number" min={-240} max={240} value={s.sunsetOffset ?? 0}
                                                                         onChange={(e) => set({ sunsetOffset: num(e.target.value) ?? 0 })} />}</Field>
            <Field label="Sunrise offset (min)">{(id) => <input id={id} type="number" min={-240} max={240} value={s.sunriseOffset ?? 0}
                                                               onChange={(e) => set({ sunriseOffset: num(e.target.value) ?? 0 })} />}</Field>
          </div>
        </>
      )}
      {b.type === "pid" && (
        <>
          <div className="prop-row two">
            {([["kp", "Gain (Kp)"], ["ti", "Integral time (s)"], ["td", "Derivative time (s)"], ["sample", "Sample time (s)"],
               ["outMin", "Output min"], ["outMax", "Output max"]] as const).map(([k, label]) => (
              <Field key={k} label={label}>{(id) => <input id={id} type="number" step="any" value={s[k] ?? ""}
                                                           onChange={(e) => set({ [k]: num(e.target.value) ?? 0 })} />}</Field>
            ))}
          </div>
          <Field label="Action">{(id) => (
            <select id={id} value={s.action ?? "reverse"} onChange={(e) => set({ action: e.target.value })}>
              <option value="reverse">Reverse (output rises when below setpoint: heating)</option>
              <option value="direct">Direct (output rises when above setpoint: cooling)</option>
            </select>)}</Field>
        </>
      )}
      {b.type === "manual_auto" && (
        <Field label="Value type">{(id) => (
          <select id={id} value={s.valueType ?? "number"} onChange={(e) => set({ valueType: e.target.value })}>
            <option value="number">Number</option><option value="bool">True/false</option>
          </select>)}</Field>
      )}

      {ports.inputs.some((p) => !wired.has(p.name)) && <h4>Unwired inputs</h4>}
      {ports.inputs.filter((p) => !wired.has(p.name)).map((p) => (
        <Field key={p.name} label={`${p.name}${p.required ? "" : " (optional)"}`}
               hint={!p.required && p.default !== undefined ? `Default ${String(p.default)}` : undefined}>
          {(id) => <ValueInput id={id} type={p.type} value={s.inputs?.[p.name]} onChange={(v) => setInput(p.name, v)} />}
        </Field>
      ))}
    </section>
  );
}

function DiagramProps({ diagram: d, equips, admin, onDiagram }: Props) {
  const set = (patch: Partial<Diagram>) => onDiagram({ ...d, ...patch });
  const used = new Set(d.blocks.map((b) => b.settings?.variable).filter(Boolean));
  const setVar = (i: number, patch: Partial<VariableDef>) => set({ variables: d.variables.map((v, k) => (k === i ? { ...v, ...patch } : v)) });
  return (
    <section aria-label="Diagram">
      <h3>Diagram</h3>
      <Field label="Display name">{(id) => <input id={id} value={d.dis} onChange={(e) => set({ dis: e.target.value })} />}</Field>
      <Field label="Scope">{(id) => (
        <select id={id} value={d.scope} onChange={(e) => set({ scope: e.target.value as Diagram["scope"], equip: e.target.value === "site" ? null : d.equip })}>
          <option value="site">Site (points from anywhere)</option>
          <option value="equipment">One piece of equipment</option>
        </select>)}</Field>
      {d.scope === "equipment" && (
        <Field label="Equipment">{(id) => (
          <select id={id} value={d.equip ?? ""} onChange={(e) => set({ equip: e.target.value || null })}>
            <option value="">(choose)</option>
            {equips.map((e) => <option key={e.name} value={e.name}>{e.dis}</option>)}
          </select>)}</Field>
      )}
      <Field label="Description">{(id) => <textarea id={id} rows={3} value={d.description ?? ""} onChange={(e) => set({ description: e.target.value || undefined })} />}</Field>

      <h4>This diagram's variables</h4>
      {d.variables.length === 0 && <p className="muted small">None. Site variables are on the Logic page.</p>}
      {d.variables.map((v, i) => (
        <div key={i} className="var-row">
          <div className="prop-row two">
            <Field label="Name">{(id) => <input id={id} value={v.name} readOnly={used.has(v.name)} onChange={(e) => setVar(i, { name: e.target.value })} />}</Field>
            <Field label="Type">{(id) => (
              <select id={id} value={v.type} onChange={(e) => setVar(i, { type: e.target.value as VariableDef["type"], value: null })}>
                <option value="number">Number</option><option value="bool">True/false</option><option value="text">Text</option>
              </select>)}</Field>
            <Field label="Kind">{(id) => (
              <select id={id} value={v.kind} onChange={(e) => setVar(i, { kind: e.target.value as VariableDef["kind"] })}>
                <option value="constant">Constant (set by a person)</option><option value="memory">Memory (written by logic)</option>
              </select>)}</Field>
            <Field label="Starting value">{(id) => <ValueInput id={id} type={v.type} value={v.value} onChange={(x) => setVar(i, { value: x ?? null })} />}</Field>
            {v.type === "number" && (
              <>
                <Field label="Unit">{(id) => <input id={id} value={v.unit ?? ""} onChange={(e) => setVar(i, { unit: e.target.value || undefined })} />}</Field>
                <Field label="Min">{(id) => <input id={id} type="number" step="any" value={v.min ?? ""} onChange={(e) => setVar(i, { min: num(e.target.value) ?? null })} />}</Field>
                <Field label="Max">{(id) => <input id={id} type="number" step="any" value={v.max ?? ""} onChange={(e) => setVar(i, { max: num(e.target.value) ?? null })} />}</Field>
              </>
            )}
          </div>
          <button type="button" className="link" disabled={used.has(v.name)}
                  title={used.has(v.name) ? "Still used by a block" : undefined}
                  onClick={() => set({ variables: d.variables.filter((_, k) => k !== i) })}>
            {used.has(v.name) ? "In use (cannot remove)" : "Remove variable"}
          </button>
        </div>
      ))}
      {admin && (
        <button type="button" className="btn" onClick={() => {
          const names = new Set(d.variables.map((v) => v.name));
          let n = 1;
          while (names.has(`var${n}`)) n++;
          set({ variables: [...d.variables, { name: `var${n}`, type: "number", kind: "constant", value: 0 }] });
        }}>Add a variable</button>
      )}
    </section>
  );
}

function LiveVariables({ diagram, live, variables, admin, onVariables }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const own = live?.variables ?? [];
  const usedSite = new Set(diagram.blocks.map((b) => b.settings?.variable).filter(Boolean));
  const site = variables.filter((v) => v.scope === "site" && usedSite.has(v.name));
  const rows = [...own, ...site];
  const save = async (v: SiteVariable) => {
    const raw = edit[`${v.scope}.${v.name}`];
    const value: Value = v.type === "bool" ? raw === "true" : v.type === "number" ? Number(raw) : raw;
    try {
      if (v.scope === "site") await api.putSiteVariable(v.name, { value } as Partial<SiteVariable>);
      else await api.setDiagramVariable(diagram.name, v.name, value);
      setError(null);
      setEdit((e) => { const n = { ...e }; delete n[`${v.scope}.${v.name}`]; return n; });
      onVariables();
    } catch (e) { setError(e instanceof SetupError ? e.message : String(e)); }
  };
  return (
    <section aria-label="Variables">
      <h3>Live</h3>
      {live && <p className="muted small">{live.state === "running" ? `Running version ${live.version}` : `State: ${live.state}`}
        {live.at ? ` · ${new Date(live.at).toLocaleTimeString()}` : ""}</p>}
      {live?.issues?.map((i, k) => <p key={k} className="error small">{i.message}</p>)}
      <h4>Variables</h4>
      {rows.length === 0 && <p className="muted small">This diagram uses no variables.</p>}
      <table className="vars">
        <tbody>
          {rows.map((v) => {
            const key = `${v.scope}.${v.name}`;
            const editable = admin && v.kind === "constant";
            return (
              <tr key={key}>
                <th scope="row">{v.name}<span className="muted small"> {v.scope}</span></th>
                <td>
                  {editable ? (
                    <span className="prop-inline">
                      {v.type === "bool" ? (
                        <select aria-label={`Value of ${v.name}`} value={edit[key] ?? String(v.value)} onChange={(e) => setEdit({ ...edit, [key]: e.target.value })}>
                          <option value="true">true</option><option value="false">false</option>
                        </select>
                      ) : (
                        <input aria-label={`Value of ${v.name}`} type={v.type === "number" ? "number" : "text"} step="any"
                               value={edit[key] ?? String(v.value ?? "")} onChange={(e) => setEdit({ ...edit, [key]: e.target.value })} />
                      )}
                      {edit[key] !== undefined && <button type="button" className="btn" onClick={() => void save(v)}>Set</button>}
                    </span>
                  ) : <strong>{v.value === null ? "--" : String(v.value)}</strong>}
                  {v.unit ? ` ${v.unit}` : ""}
                  {v.min !== null || v.max !== null ? <span className="muted small"> ({v.min ?? "…"} to {v.max ?? "…"})</span> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {error && <p className="error small" role="alert">{error}</p>}
    </section>
  );
}
