// Edit a point's alarms (or a template role's defaults): type, level, limit or state, deadband,
// delay and message. The backend checks them with the config file's checks when saved.
import type { Obj } from "../setup/api";

export const BLANK: Record<string, Obj> = {
  high: { type: "high", level: "warning", limit: 0, deadband: 0, delayS: 0 },
  low: { type: "low", level: "warning", limit: 0, deadband: 0, delayS: 0 },
  state: { type: "state", level: "warning", state: true, delayS: 0 },
  stale: { type: "stale", level: "info", delayS: 0 },
};

export function alarmSummary(alarms: Obj[] | undefined): string {
  return (alarms ?? [])
    .map((a) => (a.type === "high" ? `> ${a.limit}` : a.type === "low" ? `< ${a.limit}` : a.type === "state" ? `= ${String(a.state)}` : "stale") + ` ${a.level}`)
    .join("; ");
}

export function AlarmListEditor({ alarms, kind, enumValues, onChange }: {
  alarms: Obj[];
  kind: string; // Number, Bool or Str
  enumValues?: string[] | null;
  onChange: (alarms: Obj[]) => void;
}) {
  const set = (i: number, change: Obj) => onChange(alarms.map((a, j) => (j === i ? { ...a, ...change } : a)));
  const types = kind === "Number" ? ["high", "low", "stale"] : ["state", "stale"];
  return (
    <div className="alarm-editor">
      {alarms.length === 0 && <p className="muted small">No alarms.</p>}
      {alarms.map((a, i) => (
        <div key={i} className="alarm-row" data-alarm-type={a.type}>
          <select aria-label="Alarm type" value={a.type} onChange={(e) => set(i, { ...BLANK[e.target.value], level: a.level })}>
            {types.map((t) => <option key={t} value={t}>{{ high: "High limit", low: "Low limit", state: "State", stale: "Stale data" }[t]}</option>)}
          </select>
          <select aria-label="Alarm level" value={a.level} onChange={(e) => set(i, { level: e.target.value })}>
            <option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option>
          </select>
          {(a.type === "high" || a.type === "low") && (
            <>
              <label>Limit <input aria-label="Limit" type="number" step="any" value={a.limit ?? ""} onChange={(e) => set(i, { limit: Number(e.target.value) })} /></label>
              <label>Deadband <input aria-label="Deadband" type="number" step="any" min={0} value={a.deadband ?? 0} onChange={(e) => set(i, { deadband: Number(e.target.value) })} /></label>
            </>
          )}
          {a.type === "state" && (
            <label>When
              {kind === "Bool" ? (
                <select aria-label="State" value={String(a.state)} onChange={(e) => set(i, { state: e.target.value === "true" })}>
                  <option value="true">on (true)</option><option value="false">off (false)</option>
                </select>
              ) : (
                <select aria-label="State" value={String(a.state ?? "")} onChange={(e) => set(i, { state: e.target.value })}>
                  {(enumValues ?? []).map((v) => <option key={v}>{v}</option>)}
                </select>
              )}
            </label>
          )}
          <label>Delay (s) <input aria-label="Delay" type="number" min={0} value={a.delayS ?? 0} onChange={(e) => set(i, { delayS: Number(e.target.value) })} /></label>
          <input aria-label="Alarm message" className="wide" placeholder="Message (optional, no sensitive detail)" value={a.message ?? ""}
            onChange={(e) => set(i, { message: e.target.value || undefined })} />
          <button type="button" className="link danger" onClick={() => onChange(alarms.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <div className="row">
        {types.map((t) => (
          <button key={t} type="button" className="btn" onClick={() => onChange([...alarms, { ...BLANK[t], ...(t === "state" && kind === "Str" ? { state: enumValues?.[0] ?? "" } : {}) }])}>
            Add {{ high: "high limit", low: "low limit", state: "state alarm", stale: "stale alarm" }[t]}
          </button>
        ))}
      </div>
    </div>
  );
}
