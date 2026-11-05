// Device templates (main spec 5.1, section 20): built-in and this site's, with versions;
// view the point map, export, import, and apply to a device (bulk create).
import { useEffect, useState } from "react";
import type { Obj } from "./api";
import { applyTemplate, deleteTemplate, download, getTemplate, listItems, listTemplates, saveTemplate } from "./api";
import { tagsToText } from "./text";
import type { Outcome } from "./ui";
import { ActionButton, Badge, FilePick, OutcomeBox, outcomeFrom, useLoad } from "./ui";

function addressText(a: Obj): string {
  if (!a) return "";
  if (a.topic) return a.topic + (a.jsonPath ? ` · ${a.jsonPath}` : "");
  if (a.instance != null) return `${a.objectType} ${a.instance}`;
  return `${a.objectType} ${a.address} ${a.dataType ?? ""}${a.scale && a.scale !== 1 ? ` ×${a.scale}` : ""}`;
}

export function DeviceTemplatesPage() {
  const list = useLoad(() => listTemplates(), []);
  const devices = useLoad(() => listItems("devices"), []);
  const equips = useLoad(() => listItems("equips"), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Obj | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [apply, setApply] = useState<Obj>({ device: "", namePrefix: "", equip: "", only: "", repeat: false, from: 1, to: 1, step: 0, equipDis: "" });

  useEffect(() => {
    if (!selected && list.data?.length) setSelected(list.data[0].id);
  }, [list.data, selected]);
  useEffect(() => {
    if (!selected) return;
    getTemplate(selected).then(setDetail).catch((e) => setOutcome(outcomeFrom(e)));
  }, [selected]);

  const t = detail?.template;
  const run = async (check: boolean) => {
    if (!t) return;
    const body: Obj = { device: apply.device, namePrefix: apply.namePrefix, equip: apply.equip, version: t.version };
    if (apply.only.trim()) body.only = apply.only.split(/[\s,]+/).filter(Boolean);
    if (apply.repeat) body.repeat = { from: Number(apply.from), to: Number(apply.to), step: Number(apply.step) };
    if (apply.equipDis) body.equipDis = apply.equipDis;
    try {
      const r = await applyTemplate(t.id, body, check);
      setOutcome({
        kind: "ok",
        message: check ? "The checks pass. Nothing was saved yet." : `Created ${r.report?.created.point ?? 0} points; they are live.`,
        issues: r.warnings,
      });
      if (!check) void equips.reload();
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  return (
    <div className="three">
      <nav className="side-list" aria-label="Device templates">
        <h3>Device templates</h3>
        {(list.data ?? []).map((x) => (
          <button key={x.id} type="button" className={selected === x.id ? "active" : ""} onClick={() => setSelected(x.id)}>
            <span className="pick-main">
              <span>{x.vendor} {x.model}</span>
              <span className="muted small">{x.id} {x.version}</span>
            </span>
            <span>{x.points}</span>
          </button>
        ))}
        <FilePick
          label="Import template"
          accept=".json,application/json"
          onText={async (text) => {
            try {
              const r = await saveTemplate(JSON.parse(text));
              setOutcome({ kind: "ok", message: `Imported ${r.id} ${r.version}.` });
              await list.reload();
              setSelected(r.id);
            } catch (e) {
              setOutcome(outcomeFrom(e));
            }
          }}
        />
      </nav>
      <section className="main-pane">
        {t ? (
          <>
            <div className="pane-head">
              <div>
                <h2>
                  {t.vendor} {t.model} <span className="muted small">version {t.version}, {detail!.source === "builtin" ? "built-in" : "this site's"}</span>
                </h2>
                <p className="muted small">{t.dis}</p>
                <p className="small">
                  {t.hardwareId && <Badge tone="info">{t.hardwareId}</Badge>} <Badge tone={t.status === "bench tested" ? "ok" : "warn"}>{t.status}</Badge>{" "}
                  {(t.protocols ?? []).join(", ")}
                </p>
              </div>
              <div className="row">
                <button type="button" className="btn" onClick={() => download(`${t.id}-${t.version}.json`, JSON.stringify(t, null, 2))}>Export</button>
                {detail!.source === "site" && (
                  <ActionButton danger onClick={async () => {
                    if (!window.confirm(`Remove ${t.id} version ${t.version}?`)) return;
                    try { await deleteTemplate(t.id, t.version); setSelected(null); setDetail(null); await list.reload(); }
                    catch (e) { setOutcome(outcomeFrom(e)); }
                  }}>Remove version</ActionButton>
                )}
              </div>
            </div>
            {(t.notes ?? []).length > 0 && (
              <details className="notes"><summary>Notes ({t.notes.length})</summary><ul>{t.notes.map((n: string) => <li key={n}>{n}</li>)}</ul></details>
            )}
            <div className="table-scroll">
              <table className="grid compact" aria-label="Template points">
                <thead><tr><th>Key</th><th>Display name</th><th>Type</th><th>Address</th><th>Unit</th><th>Tags</th></tr></thead>
                <tbody>
                  {(t.points ?? []).map((p: Obj) => (
                    <tr key={p.key}>
                      <td className="mono">{p.key}</td><td>{p.dis}</td><td>{p.type} {p.kind}</td>
                      <td className="mono small">{addressText(p.address)}</td><td>{p.unit ?? (p.enum ? p.enum.join("/") : "")}</td><td className="small">{tagsToText(p.tags)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : <p className="muted">Pick a template.</p>}
      </section>
      <aside className="side-pane" aria-label="Apply to a device">
        <p className="eyebrow">Apply to a device</p>
        <h2>Bulk create points</h2>
        <p className="muted small">For a whole device with equipment, the Commission wizard is easier. This adds the template's points to a saved device directly.</p>
        <label className="stack"><span>Device</span>
          <select aria-label="Device" value={apply.device} onChange={(e) => setApply({ ...apply, device: e.target.value })}>
            <option value="">Choose…</option>
            {(devices.data ?? []).map((d) => <option key={d.name} value={d.name}>{d.dis} ({d.name})</option>)}
          </select>
        </label>
        <label className="stack"><span>Point name prefix</span><input aria-label="Name prefix" className="mono" value={apply.namePrefix} placeholder="plant_lights1_" onChange={(e) => setApply({ ...apply, namePrefix: e.target.value })} /></label>
        <label className="stack"><span>Equipment</span>
          <input aria-label="Equipment" className="mono" list="equip-names" value={apply.equip} onChange={(e) => setApply({ ...apply, equip: e.target.value })} />
          <datalist id="equip-names">{(equips.data ?? []).map((e) => <option key={e.name} value={e.name}>{e.dis}</option>)}</datalist>
        </label>
        <label className="stack"><span>Only these keys (optional)</span><input aria-label="Only keys" className="mono" value={apply.only} placeholder="di0 do0 do1" onChange={(e) => setApply({ ...apply, only: e.target.value })} /></label>
        <label className="check"><input type="checkbox" checked={apply.repeat} onChange={(e) => setApply({ ...apply, repeat: e.target.checked })} /> Repeat ({"{n}"} in prefix and equipment)</label>
        {apply.repeat && (
          <div className="row">
            <label className="stack"><span>From</span><input type="number" value={apply.from} onChange={(e) => setApply({ ...apply, from: e.target.value })} /></label>
            <label className="stack"><span>To</span><input type="number" value={apply.to} onChange={(e) => setApply({ ...apply, to: e.target.value })} /></label>
            <label className="stack"><span>Step</span><input type="number" value={apply.step} onChange={(e) => setApply({ ...apply, step: e.target.value })} /></label>
          </div>
        )}
        <label className="stack"><span>New equipment display name (optional)</span><input value={apply.equipDis} placeholder="Zone {n} lights" onChange={(e) => setApply({ ...apply, equipDis: e.target.value })} /></label>
        <div className="actions">
          <ActionButton disabled={!t || !apply.device} onClick={() => run(true)}>Check</ActionButton>
          <ActionButton primary disabled={!t || !apply.device} onClick={() => run(false)}>Create points</ActionButton>
        </div>
        <OutcomeBox outcome={outcome} />
      </aside>
    </div>
  );
}
