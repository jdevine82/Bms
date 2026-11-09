// Maintenance counters on equipment (main spec 5.10): run hours and starts counted by the BMS (or
// reported by the device), the counts since the last service, the service interval and Service done.
import { useState } from "react";
import { useAuth } from "../auth/AuthGate";
import type { Obj } from "./api";
import { get, post, put } from "./api";
import type { Outcome } from "./ui";
import { ActionButton, OutcomeBox, outcomeFrom, useLoad } from "./ui";

export interface CounterStatus {
  equip: string; dis: string; runPoint: string; running: boolean;
  hours: number; starts: number; hoursSource: "bms" | "device"; startsSource: "bms" | "device";
  estimated: boolean; unseenHours: number;
  sinceService: { hours: number; starts: number };
  service: { hours?: number; starts?: number; level?: string; message?: string } | null;
  due: boolean; servicedAt: string | null; servicedBy: string | null;
}

export const getCounters = (equip: string) =>
  get<CounterStatus[]>(`/api/counters?equip=${encodeURIComponent(equip)}`).then((r) => r[0] ?? null);

const fmt = (n: number, digits = 1) => n.toLocaleString(undefined, { maximumFractionDigits: digits });

/** One line for the tap-to-control panel: "1,234 h run · 56 starts · Service due". */
export function CounterLine({ equip }: { equip: string }) {
  const c = useLoad(() => getCounters(equip), [equip]);
  if (!c.data) return null;
  const s = c.data;
  return (
    <div className="muted small counter-line" aria-label="Run hours and starts">
      {fmt(s.hours)} h run · {fmt(s.starts, 0)} starts{s.estimated ? " (estimated)" : ""}
      {s.due && <span className="counter-due"> · Service due</span>}
    </div>
  );
}

export function EquipCounters({ equip, item, onSaved }: { equip: string; item: Obj; onSaved: () => void }) {
  const { can } = useAuth();
  const c = useLoad(() => getCounters(equip), [equip]);
  const svc = (item.service ?? {}) as Obj;
  const [form, setForm] = useState<{ hours: string; starts: string; level: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const f = form ?? { hours: svc.hours != null ? String(svc.hours) : "", starts: svc.starts != null ? String(svc.starts) : "",
                      level: String(svc.level ?? "info") };
  if (!c.data) {
    return (
      <section className="equip-counters" aria-label="Maintenance counters">
        <h3>Maintenance counters</h3>
        <p className="muted small">No run-state point: counters start when the equipment has a true/false sensor tagged <code>run</code>.</p>
      </section>
    );
  }
  const s = c.data;
  const interval = s.service;
  return (
    <section className="equip-counters" aria-label="Maintenance counters">
      <h3>Maintenance counters</h3>
      <table className="grid compact">
        <tbody>
          <tr><th>Run hours</th><td data-testid="counter-hours">{fmt(s.hours, s.hours < 10 ? 2 : 1)} h</td>
            <td className="muted small">{s.hoursSource === "device" ? "from the device" : `counted by the BMS from ${s.runPoint}`}</td></tr>
          <tr><th>Starts</th><td data-testid="counter-starts">{fmt(s.starts, 0)}</td>
            <td className="muted small">{s.startsSource === "device" ? "from the device" : "counted by the BMS"}</td></tr>
          <tr><th>Since the last service</th><td>{fmt(s.sinceService.hours)} h · {fmt(s.sinceService.starts, 0)} starts</td>
            <td className="muted small">{s.servicedAt ? `serviced ${new Date(s.servicedAt).toLocaleDateString()}${s.servicedBy ? ` by ${s.servicedBy}` : ""}` : "never marked"}</td></tr>
        </tbody>
      </table>
      {s.estimated && <p className="muted small">Estimated: the run state could not be seen for {fmt(s.unseenHours, 2)} h (Bad or Stale), which is not counted.</p>}
      {s.due && <p className="note bad" role="status">Service due{interval?.hours ? ` (every ${fmt(interval.hours, 3)} h)` : ""}{interval?.starts ? ` (every ${fmt(interval.starts, 0)} starts)` : ""}.</p>}
      {can("admin") && (
        <ActionButton onClick={async () => {
          if (!window.confirm(`Mark the service of ${equip} as done? The counts since the last service start again; totals stay.`)) return;
          try { await post(`/api/counters/${encodeURIComponent(equip)}/service-done`, {}); setOutcome({ kind: "ok", message: "Service marked as done." }); await c.reload(); }
          catch (e) { setOutcome(outcomeFrom(e)); }
        }}>Service done</ActionButton>
      )}
      {can("admin") && (
        <div className="form" aria-label="Service interval">
          <h4>Service interval</h4>
          <p className="muted small">An alarm when the hours or the starts since the last service reach these.{!item.service && interval ? " The template's interval applies until you set one here." : ""}</p>
          <div className="form-row"><label htmlFor="sv-hours">Every (run hours)</label>
            <input id="sv-hours" inputMode="numeric" value={f.hours} onChange={(e) => setForm({ ...f, hours: e.target.value })} /></div>
          <div className="form-row"><label htmlFor="sv-starts">Every (starts)</label>
            <input id="sv-starts" inputMode="numeric" value={f.starts} onChange={(e) => setForm({ ...f, starts: e.target.value })} /></div>
          <div className="form-row"><label htmlFor="sv-level">Alarm level</label>
            <select id="sv-level" value={f.level} onChange={(e) => setForm({ ...f, level: e.target.value })}>
              <option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option>
            </select></div>
          <ActionButton onClick={async () => {
            const hours = f.hours.trim() ? Number(f.hours) : null;
            const starts = f.starts.trim() ? Number(f.starts) : null;
            const service = hours || starts ? { ...(hours ? { hours } : {}), ...(starts ? { starts } : {}), level: f.level } : null;
            try {
              await put(`/api/setup/equips/${encodeURIComponent(equip)}`, { ...item, service });
              setOutcome({ kind: "ok", message: service ? "Service interval saved." : "Service interval removed." });
              setForm(null); onSaved(); await c.reload();
            } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Save interval</ActionButton>
        </div>
      )}
      <OutcomeBox outcome={outcome} />
    </section>
  );
}
