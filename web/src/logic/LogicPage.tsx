// The Logic page (milestone 10B, decision 5): the diagrams and their state for everyone signed
// in; New diagram, Import and the site's variables for admins. A diagram opens at #/logic/<name>.
import { useEffect, useState } from "react";
import type { Point } from "../api";
import { getPoints } from "../api";
import { useAuth } from "../auth/AuthGate";
import { get } from "../setup/api";
import { SetupError } from "../setup/api";
import { useLoad } from "../setup/ui";
import { ImportDialog } from "./ImportDialog";
import * as api from "./logicApi";
import { LogicEditor } from "./LogicEditor";
import "./logic.css";

const STATE: Record<string, string> = { running: "Running", disabled: "Disabled", error: "Not running (error)", draft: "Draft only",
  template: "Template logic (runs on instances)", overridden: "Replaced by an equipment diagram", unknown: "" };

export function LogicPage({ open }: { open: string | null }) {
  const go = (name: string | null) => { location.hash = name ? `#/logic/${encodeURIComponent(name)}` : "#/logic"; };
  if (open) return <LogicEditor key={open} name={open} onClose={() => go(null)} />;
  return <LogicList onOpen={go} />;
}

function LogicList({ onOpen }: { onOpen: (name: string) => void }) {
  const { can } = useAuth();
  const admin = can("admin");
  const list = useLoad(() => api.listLogic(), []);
  const vars = useLoad(() => api.listVariables(), []);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDis, setNewDis] = useState("");
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [schedules, setSchedules] = useState<{ name: string; dis: string }[]>([]);

  useEffect(() => {
    if (!importing) return;
    getPoints().then(setPoints).catch(() => undefined);
    get<{ name: string; dis: string }[]>("/api/schedules").then(setSchedules).catch(() => undefined);
  }, [importing]);

  const create = async () => {
    if (!/^[a-z][a-z0-9_]*$/.test(newName)) {
      setError("A name is lowercase letters, digits and _, starting with a letter.");
      return;
    }
    if ((list.data ?? []).some((d) => d.name === newName)) {
      setError(`There is already a diagram '${newName}'.`);
      return;
    }
    try {
      await api.saveDraft(newName, { name: newName, dis: newDis || newName, scope: "site", variables: [], blocks: [], wires: [] });
      onOpen(newName);
    } catch (e) { setError(e instanceof SetupError ? e.message : String(e)); }
  };

  return (
    <div className="setup plain logic-list">
      <section className="main-pane" aria-labelledby="logic-h">
        <h2 id="logic-h">Logic</h2>
        <p className="muted">Control logic the BMS runs once a second. Each output has a fail-safe; operator hand mode always wins.</p>
        {list.error && <p className="error">{list.error}</p>}
        {list.data && list.data.length === 0 && <p className="muted">No logic yet.</p>}
        {list.data && list.data.length > 0 && (
          <table className="grid" aria-label="Diagrams">
            <thead><tr><th>Diagram</th><th>State</th><th>Version</th><th>Scope</th><th>Writes</th><th /></tr></thead>
            <tbody>
              {list.data.map((d) => (
                <tr key={d.name}>
                  <td><button type="button" className="link" onClick={() => onOpen(d.name)}>{d.dis}</button><br /><code className="small">{d.name}</code></td>
                  <td><span className={`state-chip state-${d.state}`}>{STATE[d.state] ?? d.state}</span>
                    {d.issues.length > 0 && <div className="error small">{d.issues[0].message}</div>}</td>
                  <td>{d.publishedVersion ?? "—"}</td>
                  <td>{d.instance ? `Template ${d.template} ${d.templateVersion} on ${d.equip}`
                    : d.scope === "template" ? `Template ${d.template} (its logic)`
                    : d.scope === "equipment" ? `Equipment ${d.equip ?? ""}` : "Site"}</td>
                  <td className="small">{d.writes.join(", ") || "—"}</td>
                  <td>
                    <button type="button" className="link" onClick={() => onOpen(d.name)}>{admin ? "Open" : "Live"}</button>
                    {admin && !d.instance && (
                      <>
                        {" · "}
                        <button type="button" className="link" onClick={async () => {
                          if (!confirm(`Remove ${d.dis}? Its outputs go to their fail-safe.`)) return;
                          try { await api.deleteLogic(d.name); await list.reload(); } catch (e) { setError(String(e)); }
                        }}>Remove</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {admin && !creating && (
          <div className="actions">
            <button type="button" className="btn primary" onClick={() => { setCreating(true); setError(null); }}>New diagram</button>
            <button type="button" className="btn" onClick={() => setImporting(true)}>Import</button>
          </div>
        )}
        {creating && (
          <div className="form">
            <div className="form-row"><label htmlFor="new-name">Name</label>
              <input id="new-name" value={newName} placeholder="for example ahu1_control" onChange={(e) => setNewName(e.target.value)} /></div>
            <div className="form-row"><label htmlFor="new-dis">Display name</label>
              <input id="new-dis" value={newDis} placeholder="AHU 1 control" onChange={(e) => setNewDis(e.target.value)} /></div>
            <div className="actions">
              <button type="button" className="btn primary" onClick={() => void create()}>Create</button>
              <button type="button" className="btn" onClick={() => setCreating(false)}>Cancel</button>
            </div>
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}

        <h3>Site variables</h3>
        <p className="muted small">Shared by all logic. Constants are set here by a person; memory values are written by logic and kept through a restart.</p>
        <SiteVariables vars={(vars.data ?? []).filter((v) => v.scope === "site")} admin={admin} onChange={() => void vars.reload()} />
      </section>
      {importing && (
        <ImportDialog points={points} variables={vars.data ?? []} schedules={schedules} onClose={() => setImporting(false)}
                      onCreated={(name) => { setImporting(false); onOpen(name); }} />
      )}
    </div>
  );
}

function SiteVariables({ vars, admin, onChange }: { vars: api.SiteVariable[]; admin: boolean; onChange: () => void }) {
  const [equips, setEquips] = useState<{ name: string; dis: string }[]>([]);
  const [pub, setPub] = useState<{ variable: string; point: string; equip: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    get<{ equips?: { name: string; dis: string }[] }>("/api/site").then((s) => setEquips(s.equips ?? [])).catch(() => undefined);
  }, []);
  const [draft, setDraft] = useState<{ name: string; type: api.VType; kind: "constant" | "memory"; value: string; unit: string;
                                       min: string; max: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const parse = (type: api.VType, v: string): api.Value | null =>
    v === "" ? null : type === "bool" ? v === "true" : type === "number" ? Number(v) : v;
  const save = async () => {
    if (!draft) return;
    try {
      await api.putSiteVariable(draft.name, {
        type: draft.type, kind: draft.kind, value: parse(draft.type, draft.value), unit: draft.unit || null,
        min: draft.min === "" ? null : Number(draft.min), max: draft.max === "" ? null : Number(draft.max),
      });
      setDraft(null);
      setError(null);
      onChange();
    } catch (e) { setError(e instanceof SetupError ? e.message : String(e)); }
  };
  return (
    <>
      {vars.length === 0 && <p className="muted">None yet.</p>}
      {vars.length > 0 && (
        <table className="grid compact" aria-label="Site variables">
          <thead><tr><th>Name</th><th>Type</th><th>Kind</th><th>Value</th><th>Range</th><th /></tr></thead>
          <tbody>
            {vars.map((v) => (
              <tr key={v.name}>
                <td><code>{v.name}</code></td><td>{v.type}</td><td>{v.kind}</td>
                <td>{v.value === null ? "--" : String(v.value)}{v.unit ? ` ${v.unit}` : ""}</td>
                <td>{v.min !== null || v.max !== null ? `${v.min ?? "…"} to ${v.max ?? "…"}` : ""}</td>
                <td>{admin && <button type="button" className="link" onClick={() => setDraft({ name: v.name, type: v.type, kind: v.kind,
                  value: v.value === null ? "" : String(v.value), unit: v.unit ?? "", min: v.min === null ? "" : String(v.min),
                  max: v.max === null ? "" : String(v.max) })}>Change</button>}
                  {admin && v.type !== "text" && <>{" · "}<button type="button" className="link" onClick={() => {
                    setPub({ variable: v.name, point: `${v.name}_point`, equip: "" }); setNote(null);
                  }}>Publish as point</button></>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {pub && (
        <div className="form" aria-label="Publish as point">
          <p className="muted small">A point on the BMS logic device: shown on graphics, trends and the HMI; a constant can be changed there within its limits.</p>
          <div className="form-row"><label htmlFor="pub-point">Point name for {pub.variable}</label>
            <input id="pub-point" value={pub.point} onChange={(e) => setPub({ ...pub, point: e.target.value })} /></div>
          <div className="form-row"><label htmlFor="pub-equip">Equipment</label>
            <select id="pub-equip" value={pub.equip} onChange={(e) => setPub({ ...pub, equip: e.target.value })}>
              <option value="">(choose)</option>
              {equips.map((e) => <option key={e.name} value={e.name}>{e.dis}</option>)}
            </select></div>
          <div className="actions">
            <button type="button" className="btn primary" disabled={!pub.point || !pub.equip} onClick={async () => {
              try {
                await api.publishVariable({ variable: pub.variable, point: pub.point, equip: pub.equip });
                setNote(`Published ${pub.variable} as the point ${pub.point}.`);
                setPub(null);
              } catch (e) { setNote(e instanceof SetupError ? e.message : String(e)); }
            }}>Publish</button>
            <button type="button" className="btn" onClick={() => setPub(null)}>Cancel</button>
          </div>
        </div>
      )}
      {note && <p role="status">{note}</p>}
      {admin && !draft && (
        <button type="button" className="btn" onClick={() => setDraft({ name: "", type: "number", kind: "constant", value: "", unit: "", min: "", max: "" })}>
          Add a site variable</button>
      )}
      {draft && (
        <div className="form">
          <div className="form-row"><label htmlFor="sv-name">Name</label>
            <input id="sv-name" value={draft.name} readOnly={vars.some((v) => v.name === draft.name)} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></div>
          <div className="form-row"><label htmlFor="sv-type">Type</label>
            <select id="sv-type" value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value as api.VType, value: "" })}>
              <option value="number">Number</option><option value="bool">True/false</option><option value="text">Text</option></select></div>
          <div className="form-row"><label htmlFor="sv-kind">Kind</label>
            <select id="sv-kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as "constant" })}>
              <option value="constant">Constant (set by a person)</option><option value="memory">Memory (written by logic)</option></select></div>
          <div className="form-row"><label htmlFor="sv-value">Value</label>
            {draft.type === "bool" ? (
              <select id="sv-value" value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })}>
                <option value="">(none)</option><option value="true">true</option><option value="false">false</option></select>
            ) : <input id="sv-value" type={draft.type === "number" ? "number" : "text"} step="any" value={draft.value}
                       onChange={(e) => setDraft({ ...draft, value: e.target.value })} />}</div>
          {draft.type === "number" && (
            <>
              <div className="form-row"><label htmlFor="sv-unit">Unit</label><input id="sv-unit" value={draft.unit} onChange={(e) => setDraft({ ...draft, unit: e.target.value })} /></div>
              <div className="form-row"><label htmlFor="sv-min">Minimum</label><input id="sv-min" type="number" step="any" value={draft.min} onChange={(e) => setDraft({ ...draft, min: e.target.value })} /></div>
              <div className="form-row"><label htmlFor="sv-max">Maximum</label><input id="sv-max" type="number" step="any" value={draft.max} onChange={(e) => setDraft({ ...draft, max: e.target.value })} /></div>
            </>
          )}
          <div className="actions">
            <button type="button" className="btn primary" onClick={() => void save()}>Save variable</button>
            <button type="button" className="btn" onClick={() => setDraft(null)}>Cancel</button>
          </div>
          {error && <p className="error" role="alert">{error}</p>}
        </div>
      )}
    </>
  );
}
