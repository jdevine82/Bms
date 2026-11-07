// Equipment templates (main spec 5.1.2; mockup sheet 2): roles, versions, roll-out with a
// preview, side-by-side difference, instances with their overrides.
import { Fragment, useEffect, useMemo, useState } from "react";
import { alarmSummary, AlarmListEditor } from "../alarms/AlarmListEditor";
import type { Obj } from "./api";
import {
  deleteEquipTemplate,
  diffEquipTemplate,
  download,
  getConfig,
  getEquipTemplate,
  getInstance,
  post,
  listEquipTemplates,
  resetOverride,
  rollout,
  saveEquipTemplate,
  unlinkInstance,
} from "./api";
import { tagsToText, textToTags } from "./text";
import type { Outcome } from "./ui";
import { ActionButton, Badge, FilePick, OutcomeBox, outcomeFrom, useLoad } from "./ui";

type Tab = "points" | "parameters" | "instances" | "versions";

function nextVersion(versions: string[]): string {
  const last = versions[versions.length - 1] ?? "1.0.0";
  const parts = last.split(".").map((x) => Number(x));
  if (parts.length === 3 && parts.every((n) => Number.isInteger(n))) return `${parts[0]}.${parts[1] + 1}.0`;
  return `${last}.1`;
}

const show = (v: unknown) => (v == null ? "–" : typeof v === "object" ? (Array.isArray(v) ? v.join(", ") : tagsToText(v as Obj)) : String(v));

export function EquipTemplatesPage() {
  const list = useLoad(() => listEquipTemplates(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Obj | null>(null);
  const [edit, setEdit] = useState<Obj | null>(null); // the template being edited (a new version)
  const [tab, setTab] = useState<Tab>("points");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [preview, setPreview] = useState<Obj | null>(null);
  const [instance, setInstance] = useState<Obj | null>(null);
  const [alarmRole, setAlarmRole] = useState<number | null>(null);

  const load = async (id: string, version?: string) => {
    try {
      const d = await getEquipTemplate(id, version);
      setDetail(d);
      setEdit(null);
      setPreview(null);
      setInstance(null);
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  useEffect(() => {
    if (!selected && list.data?.length) setSelected(list.data[0].id);
  }, [list.data, selected]);
  useEffect(() => {
    if (selected) void load(selected);
    setOutcome(null);
  }, [selected]);

  const t = edit ?? detail?.template;
  const builtin = detail?.source === "builtin";
  const editing = !!edit;
  const dirty = useMemo(() => !!edit && JSON.stringify({ ...edit, version: "" }) !== JSON.stringify({ ...detail?.template, version: "" }), [edit, detail]);

  const setRole = (i: number, change: Obj) => {
    if (!edit) return;
    const roles = edit.roles.slice();
    roles[i] = { ...roles[i], ...change };
    for (const k of Object.keys(change)) if (change[k] === undefined || change[k] === "") delete roles[i][k];
    setEdit({ ...edit, roles });
    setPreview(null);
  };

  const previewRollout = async () => {
    if (!edit || !detail) return;
    if (!detail.instances.length) {
      setPreview({ instances: [] });
      return;
    }
    try {
      const { config } = await getConfig();
      config.equipTemplates = [...(config.equipTemplates ?? []), edit];
      const r = await rollout(edit.id, { draft: config, version: edit.version, all: true });
      setPreview(r);
      setOutcome(null);
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  return (
    <div className="three">
      <nav className="side-list" aria-label="Equipment templates">
        <h3>Equipment templates</h3>
        {(list.data ?? []).map((x) => (
          <button key={x.id} type="button" className={selected === x.id ? "active" : ""} onClick={() => setSelected(x.id)}>
            <span className="pick-main">
              <span>{x.dis.replace(/ \(.*\)$/, "")} {x.appliesTo === "space" && <Badge tone="muted">room</Badge>}</span>
              {x.source === "site" && <span className="muted small">{x.id}, this site's</span>}
            </span>
            <span>{x.instances}</span>
          </button>
        ))}
        <FilePick
          label="Import template"
          accept=".json,application/json"
          onText={async (text) => {
            try {
              const r = await saveEquipTemplate(JSON.parse(text));
              setOutcome({ kind: "ok", message: `Imported ${r.id ?? "the template"}.`, issues: r.warnings });
              await list.reload();
            } catch (e) {
              setOutcome(outcomeFrom(e));
            }
          }}
        />
      </nav>
      <section className="main-pane">
        {t && detail ? (
          <>
            <div className="pane-head">
              <div>
                <h2>
                  {t.dis}{" "}
                  <span className="muted small">
                    version {t.version}, {builtin ? "built-in starter" : "this site's"}, {t.appliesTo === "space" ? "room" : "equipment"} template
                  </span>
                </h2>
                <p className="muted small">Point roles are bound to device points when equipment is made from this template.</p>
              </div>
              <div className="row">
                <button type="button" className="btn" onClick={() => download(`${t.id}-${t.version}.json`, JSON.stringify(detail.template, null, 2))}>
                  Export
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    const id = window.prompt("ID for the copy (lower case, digits and dashes)", `${t.id}-site`);
                    if (!id) return;
                    setDetail({ ...detail, source: "site", versions: [], instances: [], template: { ...detail.template, id, version: "1.0.0" } });
                    setEdit({ ...structuredClone(detail.template), id, version: "1.0.0", dis: `${detail.template.dis} (copy)` });
                    setTab("points");
                  }}
                >
                  Copy template
                </button>
                {!builtin && !editing && (
                  <button type="button" className="btn" onClick={async () => {
                    try {
                      const r = await post<{ name: string }>(`/api/logic/template-draft/${encodeURIComponent(t.id)}`, {});
                      location.hash = `#/logic/${encodeURIComponent(r.name)}`;
                    } catch (e) { alert(e instanceof Error ? e.message : String(e)); }
                  }}>
                    Logic{detail.template.logic ? "" : " (none yet)"}
                  </button>
                )}
                {!builtin && !editing && (
                  <button type="button" className="btn primary" onClick={() => { setEdit({ ...structuredClone(detail.template), version: nextVersion(detail.versions) }); setTab("points"); }}>
                    Edit as a new version
                  </button>
                )}
              </div>
            </div>
            {builtin && !editing && <p className="note">Built-in templates are read-only. Copy one to change it for this site.</p>}
            <div className="tabs" role="tablist">
              {(["points", "parameters", "instances", "versions"] as Tab[]).map((x) => (
                <button key={x} type="button" role="tab" aria-selected={tab === x} onClick={() => setTab(x)}>
                  {x === "points" ? "Points" : x === "parameters" ? "Parameters" : x === "instances" ? `Instances (${detail.instances.length})` : "Versions"}
                </button>
              ))}
            </div>
            {tab === "points" && (
              <div className="table-scroll">
                <table className="grid editable" aria-label="Point roles">
                  <thead>
                    <tr>
                      <th>Point role</th><th>Display name</th><th>Type</th><th>Kind</th><th>Units</th><th>Tags</th><th>Speed</th><th>Log</th><th>Alarms</th><th>Use</th>
                      {editing && <th />}
                    </tr>
                  </thead>
                  <tbody>
                    {t.roles.map((r: Obj, i: number) => (
                      <Fragment key={i}>
                      <tr>
                        <td>{editing ? <input className="mono" aria-label="Role" value={r.role} onChange={(e) => setRole(i, { role: e.target.value })} /> : <span className="mono">{r.role}</span>}</td>
                        <td>{editing ? <input aria-label={`Display name of ${r.role}`} value={r.dis} onChange={(e) => setRole(i, { dis: e.target.value })} /> : r.dis}</td>
                        <td>
                          {editing ? (
                            <select aria-label="Type" value={r.type} onChange={(e) => setRole(i, { type: e.target.value })}>
                              <option>sensor</option><option>sp</option><option>cmd</option>
                            </select>
                          ) : r.type}
                        </td>
                        <td>
                          {editing ? (
                            <select aria-label="Kind" value={r.kind} onChange={(e) => setRole(i, { kind: e.target.value })}>
                              <option>Number</option><option>Bool</option><option>Str</option>
                            </select>
                          ) : r.kind}
                        </td>
                        <td>{editing ? <input className="narrow" aria-label={`Unit of ${r.role}`} value={r.unit ?? ""} onChange={(e) => setRole(i, { unit: e.target.value })} /> : r.unit ?? (r.enum ? r.enum.join("/") : "–")}</td>
                        <td>{editing ? <input aria-label={`Tags of ${r.role}`} defaultValue={tagsToText(r.tags)} onBlur={(e) => setRole(i, { tags: textToTags(e.target.value) })} /> : tagsToText(r.tags)}</td>
                        <td>
                          {editing ? (
                            <select aria-label="Speed" value={r.pollClass ?? "normal"} onChange={(e) => setRole(i, { pollClass: e.target.value })}>
                              <option>fast</option><option>normal</option><option>slow</option>
                            </select>
                          ) : r.pollClass ?? "normal"}
                        </td>
                        <td>{editing ? <input type="checkbox" aria-label="Logged" checked={r.his !== false} onChange={(e) => setRole(i, { his: e.target.checked })} /> : r.his === false ? "no" : "yes"}</td>
                        <td className="small">
                          {editing ? (
                            <button type="button" className="link" onClick={() => setAlarmRole(alarmRole === i ? null : i)}>
                              {(r.alarms ?? []).length ? alarmSummary(r.alarms) : "None"} (edit)
                            </button>
                          ) : (alarmSummary(r.alarms) || "–")}
                        </td>
                        <td>
                          {editing ? (
                            <select aria-label="Use" value={r.required === false ? "optional" : "required"} onChange={(e) => setRole(i, { required: e.target.value === "required" })}>
                              <option value="required">Required</option><option value="optional">Optional</option>
                            </select>
                          ) : <Badge tone={r.required === false ? "muted" : "info"}>{r.required === false ? "Optional" : "Required"}</Badge>}
                        </td>
                        {editing && (
                          <td>
                            <button type="button" className="link danger" onClick={() => { setEdit({ ...edit, roles: edit!.roles.filter((_: Obj, j: number) => j !== i) }); setPreview(null); }}>Remove</button>
                          </td>
                        )}
                      </tr>
                      {editing && alarmRole === i && (
                        <tr>
                          <td colSpan={11}>
                            <AlarmListEditor alarms={r.alarms ?? []} kind={r.kind} enumValues={r.enum}
                              onChange={(alarms) => setRole(i, { alarms: alarms.length ? alarms : undefined })} />
                          </td>
                        </tr>
                      )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
                {editing && (
                  <button type="button" className="btn" onClick={() => setEdit({ ...edit!, roles: [...edit!.roles, { role: "new_role", dis: "New point", type: "sensor", kind: "Number", tags: {}, required: false }] })}>
                    Add a role
                  </button>
                )}
              </div>
            )}
            {tab === "parameters" && (
              <div>
                <p className="muted small">Values set per instance, such as a setpoint range. Graphic and logic references arrive with milestones 9 and 10.</p>
                {editing ? (
                  <textarea className="json" aria-label="Parameters" defaultValue={JSON.stringify(edit!.parameters ?? {}, null, 2)}
                    onBlur={(e) => { try { setEdit({ ...edit!, parameters: JSON.parse(e.target.value) }); } catch { setOutcome({ kind: "error", message: "Parameters must be JSON." }); } }} />
                ) : (
                  <pre>{JSON.stringify(t.parameters ?? {}, null, 2)}</pre>
                )}
                <h3>Equipment tags</h3>
                {editing ? <input aria-label="Template tags" defaultValue={tagsToText(edit!.tags)} onBlur={(e) => setEdit({ ...edit!, tags: textToTags(e.target.value) })} /> : <p>{tagsToText(t.tags) || "–"}</p>}
                {(t.notes ?? []).length > 0 && <><h3>Notes</h3><ul>{t.notes.map((n: string) => <li key={n}>{n}</li>)}</ul></>}
              </div>
            )}
            {tab === "instances" && <Instances detail={detail} onOpen={async (kind, name) => {
              try { setInstance({ kind, ...(await getInstance(kind, name)) }); } catch (e) { setOutcome(outcomeFrom(e)); }
            }} />}
            {tab === "versions" && <Versions detail={detail} onDeleted={async () => { await list.reload(); await load(detail.template.id); }} setOutcome={setOutcome} />}
          </>
        ) : (
          <p className="muted">{list.loading ? "Loading…" : "Pick a template."}</p>
        )}
      </section>
      <aside className="side-pane" aria-label="Change preview">
        {instance ? (
          <InstanceView instance={instance} onChanged={async () => {
            setInstance({ kind: instance.kind, ...(await getInstance(instance.kind, instance.name ?? instance.equip ?? instance.space)) });
          }} onClose={() => setInstance(null)} />
        ) : editing ? (
          <>
            <p className="eyebrow">Change preview</p>
            <h2>Save as version {edit!.version}</h2>
            <label className="stack">
              <span>Version</span>
              <input aria-label="Version" value={edit!.version} onChange={(e) => setEdit({ ...edit!, version: e.target.value })} />
            </label>
            <p className="small">{detail?.instances.length ?? 0} instances use this template.</p>
            <ActionButton onClick={previewRollout} disabled={!dirty}>Preview the changes per instance</ActionButton>
            {preview && <RolloutPreview preview={preview} />}
            <OutcomeBox outcome={outcome} />
            <div className="actions column">
              <ActionButton
                primary
                disabled={!dirty && detail?.versions.length !== 0}
                onClick={async () => {
                  try {
                    const saved = edit!;
                    const r = await saveEquipTemplate(saved);
                    await list.reload();
                    setSelected(saved.id);
                    await load(saved.id);
                    setTimeout(() => setOutcome({ kind: "ok", message: `Saved version ${saved.version}. Instances stay on their version until you roll it out.`, issues: r.warnings }), 0);
                  } catch (e) {
                    setOutcome(outcomeFrom(e));
                  }
                }}
              >
                Save as version {edit!.version}
              </ActionButton>
              <button type="button" className="btn" onClick={() => { setEdit(null); setPreview(null); if (detail && !detail.versions.length) setSelected(null); }}>
                Discard changes
              </button>
            </div>
            <p className="muted small">Saving keeps the earlier versions, so an instance can stay on or go back to one.</p>
          </>
        ) : detail ? (
          <RolloutPanel detail={detail} onDone={async () => { await list.reload(); await load(detail.template.id); }} />
        ) : null}
        {!editing && <OutcomeBox outcome={outcome} />}
      </aside>
    </div>
  );
}

function RolloutPreview({ preview }: { preview: Obj }) {
  const instances: Obj[] = preview.instances ?? [];
  if (!instances.length) return <p className="muted small">No instances use this template yet.</p>;
  const updated = instances.filter((i) => (i.changes ?? []).some((c: Obj) => !c.note)).length;
  const kept = instances.filter((i) => (i.changes ?? []).some((c: Obj) => c.note === "override kept"));
  const needs = instances.filter((i) => (i.needsBinding ?? []).length);
  return (
    <div className="preview">
      <dl className="kv">
        <div><dt>Will be updated</dt><dd className="ok-text">{updated}</dd></div>
        <div><dt>Keep their own value (overridden)</dt><dd className="warn-text">{kept.length}</dd></div>
        <div><dt>Need a new binding</dt><dd>{needs.length}</dd></div>
      </dl>
      {kept.map((i) => (
        <p key={i.name} className="note warn small">
          <strong>{i.name}</strong> keeps{" "}
          {i.changes.filter((c: Obj) => c.note).map((c: Obj) => `${c.field} ${show(c.kept)} (template: ${show(c.template)})`).join("; ")}. An override always wins; reset it from the instance if you want the template value.
        </p>
      ))}
      {needs.map((i) => (
        <p key={i.name} className="note small"><strong>{i.name}</strong> needs a point for {i.needsBinding.join(", ")}.</p>
      ))}
      <details>
        <summary>Every change</summary>
        <ul className="small">
          {instances.flatMap((i) => (i.changes ?? []).map((c: Obj, n: number) => (
            <li key={`${i.name}-${n}`}><span className="mono">{c.point}</span> {c.field}: {c.note ? `kept ${show(c.kept)}` : `${show(c.old)} → ${show(c.new)}`}</li>
          )))}
        </ul>
      </details>
    </div>
  );
}

function RolloutPanel({ detail, onDone }: { detail: Obj; onDone: () => Promise<void> }) {
  const latest = detail.versions[detail.versions.length - 1];
  const behind = detail.instances.filter((i: Obj) => i.version !== latest);
  const [picked, setPicked] = useState<string[]>([]);
  const [preview, setPreview] = useState<Obj | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const counts = detail.instances.reduce((a: Obj, i: Obj) => ({ ...a, [i.version]: (a[i.version] ?? 0) + 1 }), {});
  const body = () => (picked.length ? { version: latest, names: picked } : { version: latest, all: true });
  return (
    <>
      <p className="eyebrow">Versions in use</p>
      <h2>{detail.instances.length} instance{detail.instances.length === 1 ? "" : "s"}</h2>
      <dl className="kv">
        {Object.entries(counts).map(([v, n]) => (
          <div key={v}><dt>Version {v}</dt><dd>{String(n)}</dd></div>
        ))}
      </dl>
      {behind.length > 0 ? (
        <>
          <p className="small">{behind.length} not on the latest version ({latest}). Roll out to all, or try one first:</p>
          <select multiple aria-label="Instances to update" value={picked} onChange={(e) => setPicked([...e.target.selectedOptions].map((o) => o.value))}>
            {behind.map((i: Obj) => <option key={i.name} value={i.name}>{i.name} ({i.version})</option>)}
          </select>
          <ActionButton onClick={async () => {
            try { setPreview(await rollout(detail.template.id, body(), true)); setOutcome(null); } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Preview</ActionButton>
          {preview && <RolloutPreview preview={preview} />}
          <ActionButton primary onClick={async () => {
            try {
              const r = await rollout(detail.template.id, body());
              setOutcome({ kind: "ok", message: `Updated ${r.instances.length} to ${latest}.`, issues: r.warnings });
              setPreview(null);
              await onDone();
            } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>
            {picked.length ? `Update ${picked.length} selected` : `Update all ${behind.length}`} to {latest}
          </ActionButton>
        </>
      ) : (
        <p className="muted small">Every instance is on the latest version.</p>
      )}
      <OutcomeBox outcome={outcome} />
    </>
  );
}

function Instances({ detail, onOpen }: { detail: Obj; onOpen: (kind: "equips" | "spaces", name: string) => void }) {
  if (!detail.instances.length) return <p className="muted">No equipment uses this template yet. Make some in Commission.</p>;
  return (
    <table className="grid compact" aria-label="Instances">
      <thead><tr><th>Name</th><th>Version</th><th /></tr></thead>
      <tbody>
        {detail.instances.map((i: Obj) => (
          <tr key={i.name}>
            <td className="mono">{i.name}</td>
            <td>{i.version}</td>
            <td><button type="button" className="link" onClick={() => onOpen(i.kind === "space" || i.kind === "spaces" ? "spaces" : "equips", i.name)}>Overrides</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function InstanceView({ instance, onChanged, onClose }: { instance: Obj; onChanged: () => Promise<void>; onClose: () => void }) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const name = instance.name ?? instance.equip ?? instance.space;
  return (
    <>
      <p className="eyebrow">Selected instance</p>
      <h2>{name}</h2>
      <p className="muted small">Template: {instance.template?.id ?? instance.templateId} version {instance.template?.version ?? instance.version}</p>
      <ul className="role-list">
        {(instance.roles ?? []).map((r: Obj) => {
          const fields = Object.keys(r.overrides ?? {});
          return (
            <li key={r.role} className={fields.length ? "overridden" : ""}>
              <div className="role-head">
                <span className="mono">{r.role}</span>
                {!r.point ? <Badge tone={r.required ? "bad" : "muted"}>{r.required ? "Not bound" : "Disabled (optional)"}</Badge>
                  : fields.length ? <Badge tone="warn">Overridden</Badge> : <Badge tone="ok">From template</Badge>}
              </div>
              {fields.map((f) => (
                <div key={f} className="override">
                  <span className="small">
                    {f}: <strong>{show(r.overrides[f].point)}</strong> here, template {show(r.overrides[f].template)}
                  </span>
                  <ActionButton danger onClick={async () => {
                    try { await resetOverride(instance.kind, name, r.role, f); await onChanged(); setOutcome({ kind: "ok", message: `Reset ${r.role} ${f} to the template value.` }); }
                    catch (e) { setOutcome(outcomeFrom(e)); }
                  }}>Reset {f} to template</ActionButton>
                </div>
              ))}
            </li>
          );
        })}
        {(instance.extraPoints ?? instance.extra ?? []).map((p: string) => (
          <li key={p}><div className="role-head"><span className="mono">{p}</span><Badge tone="muted">Extra point</Badge></div></li>
        ))}
      </ul>
      <OutcomeBox outcome={outcome} />
      <div className="actions column">
        <ActionButton danger onClick={async () => {
          if (!window.confirm(`Unlink ${name}? It becomes plain equipment that no longer follows the template.`)) return;
          try { await unlinkInstance(instance.kind, name); onClose(); } catch (e) { setOutcome(outcomeFrom(e)); }
        }}>Unlink from template</ActionButton>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>
    </>
  );
}

function Versions({ detail, onDeleted, setOutcome }: { detail: Obj; onDeleted: () => Promise<void>; setOutcome: (o: Outcome | null) => void }) {
  const versions: string[] = detail.versions;
  const [from, setFrom] = useState(versions[Math.max(0, versions.length - 2)] ?? "");
  const [to, setTo] = useState(versions[versions.length - 1] ?? "");
  const [diff, setDiff] = useState<Obj | null>(null);
  useEffect(() => {
    if (from && to && from !== to) diffEquipTemplate(detail.template.id, from, to).then(setDiff).catch(() => setDiff(null));
    else setDiff(null);
  }, [from, to, detail.template.id]);
  return (
    <div>
      <p>Versions: {versions.join(", ")}</p>
      {versions.length > 1 ? (
        <>
          <div className="row">
            <label>From <select aria-label="From version" value={from} onChange={(e) => setFrom(e.target.value)}>{versions.map((v) => <option key={v}>{v}</option>)}</select></label>
            <label>To <select aria-label="To version" value={to} onChange={(e) => setTo(e.target.value)}>{versions.map((v) => <option key={v}>{v}</option>)}</select></label>
          </div>
          {diff && (
            <table className="grid compact" aria-label="Difference">
              <thead><tr><th>Role</th><th>Field</th><th>{diff.from}</th><th>{diff.to}</th></tr></thead>
              <tbody>
                {diff.added.map((r: string) => <tr key={`a-${r}`} className="row-ok"><td className="mono">{r}</td><td colSpan={3}>added</td></tr>)}
                {diff.removed.map((r: string) => <tr key={`r-${r}`} className="row-bad"><td className="mono">{r}</td><td colSpan={3}>removed</td></tr>)}
                {Object.entries(diff.changed).flatMap(([role, fields]) =>
                  Object.entries(fields as Obj).map(([f, v]) => (
                    <tr key={`${role}-${f}`}><td className="mono">{role}</td><td>{f}</td><td>{show((v as Obj).old)}</td><td>{show((v as Obj).new)}</td></tr>
                  )),
                )}
                {Object.entries(diff.template).map(([f, v]) => (
                  <tr key={`t-${f}`}><td>(template)</td><td>{f}</td><td>{show((v as Obj).old)}</td><td>{show((v as Obj).new)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : <p className="muted">One version so far.</p>}
      {detail.source === "site" && (
        <ActionButton danger onClick={async () => {
          if (!window.confirm(`Remove version ${detail.template.version}? A version still used by equipment cannot be removed.`)) return;
          try { await deleteEquipTemplate(detail.template.id, detail.template.version); await onDeleted(); setOutcome({ kind: "ok", message: "Removed." }); }
          catch (e) { setOutcome(outcomeFrom(e)); }
        }}>Remove version {detail.template.version}</ActionButton>
      )}
    </div>
  );
}
