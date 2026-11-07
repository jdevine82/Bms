// The test strip (main spec 5.3.1 Layout): simulated values for the points the draft reads and
// its schedules, then a run of a number of steps; nothing is written to any device.
import { useMemo, useState } from "react";
import type { Point } from "../api";
import { SetupError } from "../setup/api";
import { fmt } from "./BlockNode";
import type { Diagram, Issue, TestStep } from "./logicApi";
import { testRun } from "./logicApi";

type Sim = { mode: "live" | "value" | "offline"; value: string };

export function TestStrip({ name, diagram, points }: { name: string; diagram: Diagram; points: Point[] }) {
  const [open, setOpen] = useState(true);
  const [sim, setSim] = useState<Record<string, Sim>>({});
  const [sched, setSched] = useState<Record<string, "live" | "on" | "off">>({});
  const [steps, setSteps] = useState(10);
  const [dt, setDt] = useState(1);
  const [result, setResult] = useState<TestStep[] | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [busy, setBusy] = useState(false);

  const reads = useMemo(() => [...new Set(diagram.blocks.filter((b) => b.type === "point_read" && b.settings?.point)
    .map((b) => b.settings.point as string))], [diagram.blocks]);
  const schedules = useMemo(() => [...new Set(diagram.blocks.filter((b) => b.type === "schedule" && b.settings?.schedule)
    .map((b) => b.settings.schedule as string))], [diagram.blocks]);
  const byName = Object.fromEntries(points.map((p) => [p.name, p]));

  const run = async () => {
    const inputs: Record<string, unknown> = {};
    for (const [point, s] of Object.entries(sim)) {
      const kind = byName[point]?.kind;
      if (s.mode === "offline") inputs[point] = null;
      else if (s.mode === "value") inputs[point] = kind === "Bool" ? s.value === "true" : kind === "Number" ? Number(s.value) : s.value;
    }
    const schedValues: Record<string, boolean> = {};
    for (const [n, v] of Object.entries(sched)) if (v !== "live") schedValues[n] = v === "on";
    setBusy(true);
    try {
      const r = await testRun(name, diagram, inputs, schedValues, steps, dt);
      setIssues(r.issues.filter((i) => i.level === "error"));
      setResult(r.valid ? r.steps ?? [] : null);
    } catch (e) {
      setIssues([{ level: "error", message: e instanceof SetupError ? e.message : String(e) }]);
      setResult(null);
    } finally { setBusy(false); }
  };

  const outputs = result?.length ? Object.keys(result[result.length - 1].outputs) : [];
  const shownSteps = result ? (result.length > 12 ? [...result.slice(0, 6), ...result.slice(-6)] : result) : [];

  return (
    <section className="test-strip" aria-label="Test">
      <header>
        <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} Test</button>
        <span className="muted small">Runs the draft against these values. Nothing is written to any device.</span>
      </header>
      {open && (
        <div className="test-body">
          <div className="test-inputs">
            {reads.length === 0 && schedules.length === 0 && <p className="muted small">The draft reads no points or schedules.</p>}
            {reads.map((p) => {
              const s = sim[p] ?? { mode: "live", value: "" };
              const pt = byName[p];
              const set = (patch: Partial<Sim>) => setSim({ ...sim, [p]: { ...s, ...patch } });
              return (
                <div key={p} className="test-input">
                  <span title={pt?.dis}>{p}{pt?.unit ? ` (${pt.unit})` : ""}</span>
                  <select aria-label={`Source for ${p}`} value={s.mode} onChange={(e) => set({ mode: e.target.value as Sim["mode"] })}>
                    <option value="live">Live value</option><option value="value">Simulated</option><option value="offline">Offline</option>
                  </select>
                  {s.mode === "value" && (pt?.kind === "Bool" ? (
                    <select aria-label={`Simulated ${p}`} value={s.value} onChange={(e) => set({ value: e.target.value })}>
                      <option value="">(choose)</option><option value="true">true</option><option value="false">false</option>
                    </select>
                  ) : (
                    <input aria-label={`Simulated ${p}`} type={pt?.kind === "Number" ? "number" : "text"} step="any" value={s.value}
                           onChange={(e) => set({ value: e.target.value })} />
                  ))}
                </div>
              );
            })}
            {schedules.map((n) => (
              <div key={n} className="test-input">
                <span>Schedule {n}</span>
                <select aria-label={`Schedule ${n}`} value={sched[n] ?? "live"} onChange={(e) => setSched({ ...sched, [n]: e.target.value as "live" })}>
                  <option value="live">Live</option><option value="on">Occupied</option><option value="off">Unoccupied</option>
                </select>
              </div>
            ))}
            <div className="test-input">
              <label>Steps <input type="number" min={1} max={3600} value={steps} onChange={(e) => setSteps(Number(e.target.value) || 1)} /></label>
              <label>every <input type="number" min={0.1} max={3600} step="any" value={dt} onChange={(e) => setDt(Number(e.target.value) || 1)} /> s</label>
              <button type="button" className="btn primary" disabled={busy} onClick={() => void run()}>Run</button>
            </div>
          </div>
          {issues.length > 0 && <ul className="issue-list">{issues.map((i, k) => <li key={k} className="error">{i.block ? `${i.block}: ` : ""}{i.message}</li>)}</ul>}
          {result && (
            <div className="test-result">
              {outputs.length === 0 ? <p className="muted small">Ran {result.length} steps; the diagram writes no points.</p> : (
                <table className="grid compact" aria-label="Test result">
                  <thead><tr><th>Time</th>{outputs.map((o) => <th key={o}>{o}</th>)}</tr></thead>
                  <tbody>
                    {shownSteps.map((st, k) => (
                      <tr key={k}><td>{st.t} s</td>{outputs.map((o) => <td key={o}>{typeof st.outputs[o] === "string" ? st.outputs[o] as string : fmt(st.outputs[o], byName[o]?.unit)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
