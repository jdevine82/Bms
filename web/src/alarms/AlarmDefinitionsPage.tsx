// Setup → Alarm definitions: alarms on points, and offline alarms on devices (main spec 5.5).
import { useMemo, useState } from "react";
import type { Obj } from "../setup/api";
import { listItems, putItem } from "../setup/api";
import type { Outcome } from "../setup/ui";
import { ActionButton, Badge, OutcomeBox, outcomeFrom, useLoad } from "../setup/ui";
import { alarmSummary, AlarmListEditor } from "./AlarmListEditor";

export function AlarmDefinitionsPage() {
  const [tab, setTab] = useState<"points" | "devices">("points");
  return (
    <div>
      <div className="tabs pad" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "points"} onClick={() => setTab("points")}>Point alarms</button>
        <button type="button" role="tab" aria-selected={tab === "devices"} onClick={() => setTab("devices")}>Device offline alarms</button>
      </div>
      {tab === "points" ? <PointAlarms /> : <DeviceAlarms />}
    </div>
  );
}

function PointAlarms() {
  const points = useLoad(() => listItems("points"), []);
  const [filter, setFilter] = useState("");
  const [onlyWith, setOnlyWith] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Obj[] | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const list = useMemo(() => (points.data ?? []).filter((p) =>
    (!onlyWith || (p.alarms ?? []).length) && (!filter || `${p.name} ${p.dis}`.toLowerCase().includes(filter.toLowerCase()))), [points.data, filter, onlyWith]);
  const point = (points.data ?? []).find((p) => p.name === selected) ?? null;
  const alarms = draft ?? point?.alarms ?? [];
  return (
    <div className="split">
      <section className="list-pane" aria-label="Points">
        <input className="filter" aria-label="Filter points" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <label className="check small"><input type="checkbox" checked={onlyWith} onChange={(e) => setOnlyWith(e.target.checked)} /> Only points with alarms</label>
        <ul className="pick-list">
          {list.map((p) => (
            <li key={p.name}>
              <button type="button" className={selected === p.name ? "active" : ""} onClick={() => { setSelected(p.name); setDraft(null); setOutcome(null); }}>
                <span className="pick-main"><span>{p.dis}</span><span className="mono muted small">{p.name}</span>
                  {(p.alarms ?? []).length > 0 && <span className="small">{alarmSummary(p.alarms)}</span>}</span>
                <span className="pick-side"><span className="muted small">{p.kind}{p.unit ? `, ${p.unit}` : ""}</span></span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="detail-pane">
        {point ? (
          <>
            <h2>{point.dis}</h2>
            <p className="muted small"><span className="mono">{point.name}</span> · {point.kind}{point.unit ? ` in ${point.unit}` : ""}</p>
            <AlarmListEditor alarms={alarms} kind={point.kind} enumValues={point.enum} onChange={setDraft} />
            <div className="actions">
              <ActionButton primary disabled={draft === null} onClick={async () => {
                try {
                  const r = await putItem("points", point.name, { ...point, alarms: draft ?? [] });
                  setOutcome({ kind: "ok", message: "Saved; the alarms are live.", issues: r.warnings });
                  setDraft(null); await points.reload();
                } catch (e) { setOutcome(outcomeFrom(e)); }
              }}>Save alarms</ActionButton>
              <button type="button" className="btn" disabled={draft === null} onClick={() => setDraft(null)}>Discard</button>
            </div>
            <OutcomeBox outcome={outcome} />
            <p className="muted small">Limit and state alarms only react to Good values; a device that stops answering raises its offline alarm instead, if it has one.</p>
          </>
        ) : <p className="muted">Pick a point.</p>}
      </section>
    </div>
  );
}

function DeviceAlarms() {
  const devices = useLoad(() => listItems("devices"), []);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const save = async (d: Obj, offlineAlarm: Obj | undefined) => {
    const next = { ...d };
    if (offlineAlarm) next.offlineAlarm = offlineAlarm;
    else delete next.offlineAlarm;
    try { await putItem("devices", d.name, next); setOutcome({ kind: "ok", message: `Saved ${d.name}.` }); await devices.reload(); }
    catch (e) { setOutcome(outcomeFrom(e)); }
  };
  return (
    <div className="main-pane">
      <p className="muted small">A device offline alarm is raised when every point of the device stops answering for the delay. Only devices set here have one.</p>
      <OutcomeBox outcome={outcome} />
      <table className="grid" aria-label="Device offline alarms">
        <thead><tr><th>Device</th><th>Offline alarm</th><th>Level</th><th>Delay (s)</th><th /></tr></thead>
        <tbody>
          {(devices.data ?? []).map((d) => {
            const a = d.offlineAlarm;
            return (
              <tr key={d.name} data-device={d.name}>
                <td>{d.dis}<div className="mono muted small">{d.name}</div></td>
                <td>{a ? <Badge tone="info">On</Badge> : <span className="muted">Off</span>}</td>
                <td>{a && (
                  <select aria-label={`Level for ${d.name}`} value={a.level} onChange={(e) => void save(d, { ...a, level: e.target.value })}>
                    <option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option>
                  </select>)}</td>
                <td>{a && <input aria-label={`Delay for ${d.name}`} type="number" min={0} defaultValue={a.delayS ?? 300}
                  onBlur={(e) => Number(e.target.value) !== a.delayS && void save(d, { ...a, delayS: Number(e.target.value) })} />}</td>
                <td>
                  {a ? <ActionButton onClick={() => save(d, undefined)}>Turn off</ActionButton>
                    : <ActionButton onClick={() => save(d, { level: "warning", delayS: 300 })}>Add offline alarm</ActionButton>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
