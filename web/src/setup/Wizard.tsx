// The commissioning wizard (main spec 5.1.1): 1 add the device and test it, 2 create equipment
// from a template in one table, 3 verify and go live, 4 place on graphics (optional).
// The draft is kept in this browser and nothing goes live until step 3.
import { useEffect, useMemo, useState } from "react";
import type { Issue, Obj, SiteConfig, TestReadResult } from "./api";
import {
  applyTemplate,
  commit,
  createFromTemplate,
  getConfig,
  getTemplate,
  listEquipTemplates,
  listItems,
  listTemplates,
  SetupError,
  testRead,
} from "./api";
import { verdict } from "./checks";
import { DEVICE_SKELETONS } from "./ItemsPage";
import { PointGrid } from "./PointGrid";
import { TestReadView } from "./TestRead";
import { NAME_PATTERN } from "./text";
import type { Outcome } from "./ui";
import { ActionButton, Badge, OutcomeBox, outcomeFrom, useLoad } from "./ui";
import type { WizardDraft } from "./wizardDrafts";
import { getDraft, listDrafts, newDraft, removeDraft, saveDraft } from "./wizardDrafts";

const STEPS = ["Device", "Equipment", "Verify and go live", "Place on graphics"];

export function WizardPage() {
  const [draft, setDraft] = useState<WizardDraft | null>(() => {
    try {
      const id = sessionStorage.getItem("bms-commission-open");
      sessionStorage.removeItem("bms-commission-open");
      return id ? getDraft(id) : null;
    } catch {
      return null;
    }
  });
  const [drafts, setDrafts] = useState(listDrafts);

  const update = (d: WizardDraft) => {
    setDraft(d);
    saveDraft(d);
  };

  if (!draft) {
    return (
      <div className="main-pane narrow-page">
        <h2>Commission a device</h2>
        <p className="muted">
          Four steps: add the device and test it, create its equipment from a template in one table, verify and go
          live, then place it on graphics. Each step is kept as a draft in this browser and nothing goes live until
          step 3.
        </p>
        <div className="actions">
          <button type="button" className="btn primary" onClick={() => update(newDraft())}>
            New device
          </button>
          <ExistingDevicePicker onPick={(device) => update(newDraft(device, null, true))} />
        </div>
        <h3>Drafts</h3>
        {drafts.length ? (
          <ul className="draft-list">
            {drafts.map((d) => (
              <li key={d.id}>
                <button type="button" className="link" onClick={() => setDraft(d)}>
                  {d.title}
                </button>{" "}
                <span className="muted small">
                  step {d.step} of 4{d.live ? ", live" : ""} · {new Date(d.updated).toLocaleString()}
                </span>{" "}
                <button
                  type="button"
                  className="link danger"
                  onClick={() => {
                    removeDraft(d.id);
                    setDrafts(listDrafts());
                  }}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No drafts.</p>
        )}
      </div>
    );
  }

  return (
    <div className="wizard">
      <div className="wizard-head">
        <ol className="steps" aria-label="Steps">
          {STEPS.map((s, i) => {
            const n = (i + 1) as WizardDraft["step"];
            const done = draft.step > n || (draft.live && n <= 3);
            const reachable = n <= draft.step || (n === 4 && draft.live);
            return (
              <li key={s}>
                <button
                  type="button"
                  className={`step ${draft.step === n ? "on" : ""} ${done ? "done" : ""}`}
                  aria-current={draft.step === n ? "step" : undefined}
                  disabled={!reachable || (draft.live && n < 4)}
                  onClick={() => update({ ...draft, step: n })}
                >
                  {done ? "✓ " : `${n} `}
                  {s}
                </button>
              </li>
            );
          })}
        </ol>
        <button type="button" className="btn" onClick={() => { setDrafts(listDrafts()); setDraft(null); }}>
          All drafts
        </button>
      </div>
      {draft.step === 1 && <StepDevice draft={draft} update={update} />}
      {draft.step === 2 && <StepEquipment draft={draft} update={update} />}
      {draft.step === 3 && <StepVerify draft={draft} update={update} />}
      {draft.step === 4 && <StepPlace draft={draft} />}
    </div>
  );
}

function ExistingDevicePicker({ onPick }: { onPick: (device: Obj) => void }) {
  const devices = useLoad(() => listItems("devices"), []);
  const [name, setName] = useState("");
  return (
    <span className="row">
      <select aria-label="Existing device" value={name} onChange={(e) => setName(e.target.value)}>
        <option value="">Existing device…</option>
        {(devices.data ?? []).map((d) => (
          <option key={d.name} value={d.name}>
            {d.dis} ({d.name})
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn"
        disabled={!name}
        onClick={() => {
          const d = devices.data?.find((x) => x.name === name);
          if (d) onPick(d);
        }}
      >
        Start
      </button>
    </span>
  );
}

// ---------- step 1: the device and a test read ----------

/** The draft setup with the wizard's device (and new bus) in it. */
function withDevice(config: SiteConfig, draft: WizardDraft): SiteConfig {
  const out: SiteConfig = structuredClone(config);
  if (draft.bus && !(out.buses ?? []).some((b) => b.name === draft.bus!.name)) out.buses = [...(out.buses ?? []), draft.bus];
  if (!draft.existingDevice) out.devices = [...out.devices.filter((d) => d.name !== draft.device.name), draft.device];
  return out;
}

/** The device template's points for this device (first group), worked out on a draft. */
async function templatePoints(draft: WizardDraft, base: SiteConfig): Promise<Obj[]> {
  if (!draft.deviceTemplate) return [];
  const config = withDevice(base, draft);
  config.equips = [...config.equips, { name: "commission_test", dis: "Test", tags: {} }];
  const r = await applyTemplate(draft.deviceTemplate, {
    draft: config,
    device: draft.device.name,
    namePrefix: "commission_test_",
    equip: "commission_test",
  });
  const before = new Set(config.points.map((p) => p.name));
  return (r.config?.points ?? []).filter((p) => !before.has(p.name));
}

function StepDevice({ draft, update }: { draft: WizardDraft; update: (d: WizardDraft) => void }) {
  const templates = useLoad(() => listTemplates(), []);
  const buses = useLoad(() => listItems("buses"), []);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [result, setResult] = useState<TestReadResult | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const d = draft.device;
  const setDevice = (change: Obj) => update({ ...draft, device: { ...d, ...change }, title: change.dis || d.dis || change.name || d.name || "New device" });
  const setConn = (k: string, v: unknown) => setDevice({ connection: { ...d.connection, [k]: v } });

  useEffect(() => {
    if (!draft.deviceTemplate) {
      setNotes([]);
      return;
    }
    getTemplate(draft.deviceTemplate)
      .then((t) => setNotes([t.template.dis, ...(t.template.notes ?? [])].filter(Boolean)))
      .catch(() => setNotes([]));
  }, [draft.deviceTemplate]);

  const busKind = d.protocol === "modbus" ? "bus" : d.protocol === "bacnet_mstp" ? "trunk" : d.protocol === "mqtt" ? "broker" : null;
  const busOptions = (buses.data ?? []).filter((b) =>
    busKind === "bus" ? String(b.type).startsWith("modbus") : busKind === "trunk" ? b.type === "bacnet_mstp" : b.type === "mqtt_broker",
  );
  const fitting = (templates.data ?? []).filter((t) => !t.protocols?.length || t.protocols.includes(d.protocol) ||
    (d.protocol === "modbus" && t.protocols.includes("modbus_tcp")) || (d.protocol === "modbus_tcp" && t.protocols.includes("modbus")));
  const nameOk = NAME_PATTERN.test(d.name ?? "");

  const test = async () => {
    setResult(null);
    if (!draft.deviceTemplate) {
      setOutcome({ kind: "info", message: "Pick a device template first: the test reads its whole point map." });
      return;
    }
    try {
      const { config } = await getConfig();
      const points = await templatePoints(draft, config);
      setOutcome({ kind: "info", message: `Reading ${points.length} points…` });
      const busName = busKind ? d.connection?.[busKind] : null;
      const bus = (config.buses ?? []).find((b) => b.name === busName) ?? (draft.bus?.name === busName ? draft.bus : null);
      const r = await testRead({ config: { device: d, points, ...(bus ? { bus } : {}) }, waitS: 10 });
      setResult(r);
      setOutcome(null);
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  return (
    <div className="split">
      <section className="main-pane">
        <h2>1. Add the device and test it</h2>
        <div className="form">
          <div className="form-row">
            <label htmlFor="w-template">Device template</label>
            <select
              id="w-template"
              value={draft.deviceTemplate ?? ""}
              onChange={(e) => update({ ...draft, deviceTemplate: e.target.value || null })}
            >
              <option value="">None (blank device)</option>
              {fitting.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.vendor} {t.model} ({t.id} {t.version}, {t.points} points)
                </option>
              ))}
            </select>
          </div>
          {notes.length > 0 && (
            <details className="notes">
              <summary>Template notes</summary>
              <ul>{notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </details>
          )}
          {draft.existingDevice ? (
            <p>
              Existing device <strong>{d.dis}</strong> <span className="mono">{d.name}</span> ({d.protocol}). Its
              connection is not changed here.
            </p>
          ) : (
            <>
              <div className="form-row">
                <label htmlFor="w-name">Device name</label>
                <input id="w-name" className={nameOk || !d.name ? "" : "invalid"} value={d.name} placeholder="gw1"
                  onChange={(e) => setDevice({ name: e.target.value })} />
              </div>
              <div className="form-row">
                <label htmlFor="w-dis">Display name</label>
                <input id="w-dis" value={d.dis} placeholder="Mitsubishi gateway" onChange={(e) => setDevice({ dis: e.target.value })} />
              </div>
              <div className="form-row">
                <label htmlFor="w-protocol">Protocol</label>
                <select id="w-protocol" value={d.protocol}
                  onChange={(e) => setDevice({ protocol: e.target.value, connection: structuredClone(DEVICE_SKELETONS[e.target.value].connection) })}>
                  {Object.keys(DEVICE_SKELETONS).map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </div>
              <fieldset>
                <legend>Connection</legend>
                {Object.entries(d.connection ?? {}).map(([k, v]) =>
                  k === busKind ? (
                    <div className="form-row" key={k}>
                      <label htmlFor={`w-c-${k}`}>{k === "bus" ? "Bus" : k === "trunk" ? "MS/TP trunk" : "MQTT broker"}</label>
                      <select id={`w-c-${k}`} value={String(v ?? "")} onChange={(e) => setConn(k, e.target.value)}>
                        <option value="">Choose…</option>
                        {busOptions.map((b) => (
                          <option key={b.name} value={b.name}>
                            {b.dis} ({b.name})
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : typeof v === "boolean" ? (
                    <div className="form-row check" key={k}>
                      <label>
                        <input type="checkbox" checked={v} onChange={(e) => setConn(k, e.target.checked)} /> {k}
                      </label>
                    </div>
                  ) : (
                    <div className="form-row" key={k}>
                      <label htmlFor={`w-c-${k}`}>{k}</label>
                      <input id={`w-c-${k}`} type={typeof v === "number" ? "number" : "text"} value={String(v ?? "")}
                        onChange={(e) => setConn(k, typeof v === "number" ? Number(e.target.value) : e.target.value)} />
                    </div>
                  ),
                )}
              </fieldset>
            </>
          )}
        </div>
        <div className="actions">
          <ActionButton onClick={test} disabled={!nameOk}>Test connection</ActionButton>
          <button type="button" className="btn primary" disabled={!nameOk || (!draft.existingDevice && !d.dis)}
            onClick={() => update({ ...draft, step: 2 })}>
            Next: equipment
          </button>
        </div>
        <OutcomeBox outcome={outcome} />
      </section>
      <aside className="side-pane" aria-label="Test result">
        <p className="eyebrow">Test connection</p>
        {result ? <TestReadView result={result} /> : (
          <p className="muted">
            Reads the template's whole point map (the first group, for a gateway) and shows pass or fail per point with
            the raw answers. Nothing is saved or written.
          </p>
        )}
      </aside>
    </div>
  );
}

// ---------- step 2: equipment from a template, in one table ----------

function newNames(draft: WizardDraft): Set<string> {
  const base = new Set((draft.base?.points ?? []).map((p) => p.name));
  return new Set((draft.config?.points ?? []).map((p) => p.name).filter((n) => !base.has(n)));
}

function StepEquipment({ draft, update }: { draft: WizardDraft; update: (d: WizardDraft) => void }) {
  const equipTemplates = useLoad(() => listEquipTemplates(), []);
  const savedSpaces = useLoad(() => listItems("spaces"), []);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const c = draft.create;
  const setCreate = (change: Obj) => update({ ...draft, create: { ...c, ...change } });
  const chosen = equipTemplates.data?.find((t) => t.id === draft.equipTemplate);
  const scope = useMemo(() => [...newNames(draft)], [draft]);

  const build = async () => {
    if (draft.config && !window.confirm("Build the table again? Edits made in the table are lost.")) return;
    try {
      const { config: base, revision } = await getConfig();
      const start = withDevice(base, draft);
      const repeat = c.repeat ? { from: Number(c.from), to: Number(c.to), step: Number(c.step) } : undefined;
      let result;
      if (draft.equipTemplate) {
        const body: Obj = { draft: start, name: c.name, dis: c.dis || c.name, repeat };
        if (chosen?.appliesTo !== "space" && c.space) body.space = c.space;
        if (draft.deviceTemplate) body.fromDeviceTemplate = { template: draft.deviceTemplate, device: draft.device.name };
        result = await createFromTemplate(draft.equipTemplate, body);
      } else {
        if (!draft.deviceTemplate) throw new Error("Pick an equipment template, or go back and pick a device template.");
        const equip = c.name || draft.device.name;
        result = await applyTemplate(draft.deviceTemplate, {
          draft: start,
          device: draft.device.name,
          namePrefix: c.namePrefix || `${c.space || "site"}_${equip}_`,
          equip,
          equipDis: c.dis || equip,
          equipSpace: c.space || undefined,
          repeat,
        });
      }
      update({ ...draft, base, revision, config: result.config ?? null, tested: {} });
      const made = (result.config?.points.length ?? 0) - base.points.length;
      setOutcome({ kind: "ok", message: `${made} points in the draft. Check the table, then go to Verify.`, issues: result.warnings });
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  const testNew = async () => {
    if (!draft.config) return;
    try {
      const points = draft.config.points.filter((p) => scope.includes(p.name) && p.device === draft.device.name);
      const busName = draft.device.connection?.bus ?? draft.device.connection?.trunk ?? draft.device.connection?.broker;
      const bus = (draft.config.buses ?? []).find((b) => b.name === busName);
      const r = await testRead({ config: { device: draft.device, points, ...(bus ? { bus } : {}) }, waitS: 10 });
      update({ ...draft, tested: Object.fromEntries(r.results.map((x) => [x.point, x.status])) });
      const ok = r.results.filter((x) => x.status === "ok").length;
      setOutcome({ kind: ok === r.results.length ? "ok" : "error", message: `${ok} of ${r.results.length} points read.` });
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  const newEquips = draft.config ? draft.config.equips.filter((e) => !(draft.base?.equips ?? []).some((b) => b.name === e.name)) : [];
  const newSpaces = draft.config ? draft.config.spaces.filter((e) => !(draft.base?.spaces ?? []).some((b) => b.name === e.name)) : [];
  const spaces = draft.config?.spaces ?? savedSpaces.data ?? [];
  const setConfig = (config: SiteConfig) => update({ ...draft, config });

  return (
    <div className="main-pane">
      <h2>2. Create equipment from a template</h2>
      <div className="form inline-form">
        <label className="stack">
          <span>Equipment or room template</span>
          <select aria-label="Equipment template" value={draft.equipTemplate ?? ""} onChange={(e) => update({ ...draft, equipTemplate: e.target.value || null })}>
            <option value="">None: points from the device template only</option>
            {(equipTemplates.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.dis} ({t.appliesTo === "space" ? "room" : "equipment"}, {t.version})
              </option>
            ))}
          </select>
        </label>
        <label className="stack">
          <span>{chosen?.appliesTo === "space" ? "Room name" : "Equipment name"} {c.repeat ? "({n} = number)" : ""}</span>
          <input aria-label="Name pattern" className="mono" value={c.name} placeholder={c.repeat ? "ac{n}" : "ac1"} onChange={(e) => setCreate({ name: e.target.value })} />
        </label>
        <label className="stack">
          <span>Display name</span>
          <input aria-label="Display name pattern" value={c.dis} placeholder={c.repeat ? "AC {n}" : "AC 1"} onChange={(e) => setCreate({ dis: e.target.value })} />
        </label>
        {chosen?.appliesTo !== "space" && (
          <label className="stack">
            <span>Room</span>
            <select aria-label="Room" value={c.space} onChange={(e) => setCreate({ space: e.target.value })}>
              <option value="">No room (set per row below)</option>
              {(savedSpaces.data ?? []).map((s) => (
                <option key={s.name} value={s.name}>{s.dis}</option>
              ))}
            </select>
          </label>
        )}
        <label className="check">
          <input type="checkbox" checked={!!c.repeat} onChange={(e) => setCreate({ repeat: e.target.checked })} /> Bulk create (repeat)
        </label>
        {c.repeat && (
          <>
            <label className="stack"><span>From</span><input aria-label="Repeat from" type="number" value={c.from} onChange={(e) => setCreate({ from: e.target.value })} /></label>
            <label className="stack"><span>To</span><input aria-label="Repeat to" type="number" value={c.to} onChange={(e) => setCreate({ to: e.target.value })} /></label>
            <label className="stack"><span>Address step</span><input aria-label="Address step" type="number" value={c.step} onChange={(e) => setCreate({ step: e.target.value })} /></label>
          </>
        )}
        {!draft.equipTemplate && (
          <label className="stack">
            <span>Point name prefix</span>
            <input aria-label="Point name prefix" className="mono" value={c.namePrefix} placeholder="<room>_<equipment>_" onChange={(e) => setCreate({ namePrefix: e.target.value })} />
          </label>
        )}
      </div>
      <div className="actions">
        <button type="button" className="btn" onClick={() => update({ ...draft, step: 1 })}>Back</button>
        <ActionButton primary={!draft.config} onClick={build} disabled={!c.name && !!draft.equipTemplate}>
          {draft.config ? "Build the table again" : "Build the table"}
        </ActionButton>
        {draft.config && <ActionButton onClick={testNew}>Test the new points</ActionButton>}
        {draft.config && <button type="button" className="btn primary" onClick={() => update({ ...draft, step: 3 })}>Next: verify</button>}
      </div>
      <OutcomeBox outcome={outcome} />
      {draft.config && (
        <>
          {(newEquips.length > 0 || newSpaces.length > 0) && (
            <div className="block">
              <h3>New {chosen?.appliesTo === "space" ? "rooms" : "equipment"}</h3>
              <table className="grid compact editable" aria-label="New equipment">
                <thead>
                  <tr><th>Name</th><th>Display name</th>{newEquips.length > 0 && <th>Room</th>}<th>Template</th></tr>
                </thead>
                <tbody>
                  {[...newEquips.map((e) => ({ e, kind: "equips" as const })), ...newSpaces.filter((s) => s.template).map((e) => ({ e, kind: "spaces" as const }))].map(({ e, kind }) => (
                    <tr key={`${kind}-${e.name}`}>
                      <td className="mono">{e.name}</td>
                      <td>
                        <input aria-label={`Display name of ${e.name}`} value={e.dis}
                          onChange={(ev) => setConfig({ ...draft.config!, [kind]: draft.config![kind].map((x: Obj) => (x.name === e.name ? { ...x, dis: ev.target.value } : x)) })} />
                      </td>
                      {kind === "equips" && (
                        <td>
                          <select aria-label={`Room of ${e.name}`} value={e.space ?? ""}
                            onChange={(ev) => setConfig({ ...draft.config!, equips: draft.config!.equips.map((x) => (x.name === e.name ? { ...x, space: ev.target.value || undefined } : x)) })}>
                            <option value="">No room</option>
                            {spaces.map((s) => <option key={s.name} value={s.name}>{s.dis}</option>)}
                          </select>
                        </td>
                      )}
                      <td>{e.template ? <Badge tone="ok">{e.template.id} {e.template.version}</Badge> : <span className="muted">–</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <h3>Points ({scope.length})</h3>
          <PointGrid config={draft.config} onChange={setConfig} scope={scope} statuses={draft.tested}
            canRename={(n) => scope.includes(n)} showDevice={false} />
        </>
      )}
    </div>
  );
}

// ---------- step 3: verify and go live ----------

function StepVerify({ draft, update }: { draft: WizardDraft; update: (d: WizardDraft) => void }) {
  const [server, setServer] = useState<Issue[]>([]);
  const [checked, setChecked] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const scope = useMemo(() => [...newNames(draft)], [draft]);
  const config = draft.config;

  const runCheck = async () => {
    if (!config || !draft.revision) return;
    try {
      const r = await commit(config, draft.revision, "", true);
      setServer(r.warnings);
      setOutcome(null);
    } catch (e) {
      const o = outcomeFrom(e);
      setServer(o.issues?.length ? o.issues : [{ severity: "error", where: "setup", message: o.message }]);
      if (e instanceof SetupError && e.status === 409) setOutcome(o);
    }
    setChecked(true);
  };
  useEffect(() => {
    void runCheck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!config) return <p className="muted">Build the table in step 2 first.</p>;
  // Warnings about the rest of the site are not this device's to fix; errors always block.
  const mine = new Set([
    ...scope.map((n) => `points.${n}`),
    `devices.${draft.device.name}`,
    ...config.equips.filter((e) => !(draft.base?.equips ?? []).some((b) => b.name === e.name)).map((e) => `equips.${e.name}`),
    ...config.spaces.filter((e) => !(draft.base?.spaces ?? []).some((b) => b.name === e.name)).map((e) => `spaces.${e.name}`),
  ]);
  const relevant = server.filter((i) => i.severity === "error" || [...mine].some((m) => i.where === m || i.where.startsWith(`${m}.`)));
  const v = verdict(config, draft.tested, relevant, scope);
  const points = config.points.filter((p) => scope.includes(p.name));
  const logged = points.filter((p) => p.his !== false).length;
  const classes = points.reduce<Record<string, number>>((a, p) => ({ ...a, [p.pollClass ?? "normal"]: (a[p.pollClass ?? "normal"] ?? 0) + 1 }), {});
  const writable = points.filter((p) => p.type === "cmd" || p.type === "sp").length;
  const notTested = points.filter((p) => !draft.tested[p.name]).length;

  return (
    <div className="split wide-right">
      <section className="main-pane">
        <h2>3. Verify and go live</h2>
        <p className="muted">Limits, alarms and logging come from the template defaults. Fix the errors; warnings can be accepted.</p>
        <table className="grid compact checklist" aria-label="Checklist">
          <tbody>
            <tr><th>New points</th><td>{points.length} on {draft.device.dis || draft.device.name}</td></tr>
            <tr><th>Logging</th><td>{logged} of {points.length} logged to history</td></tr>
            <tr><th>Poll speed</th><td>{Object.entries(classes).map(([k, n]) => `${n} ${k}`).join(", ")}</td></tr>
            <tr><th>Communicating</th><td>{notTested ? `${notTested} not tested yet (Test the new points in step 2)` : `${points.filter((p) => draft.tested[p.name] === "ok").length} of ${points.length} read`}</td></tr>
            <tr><th>Write limits</th><td>{writable} writable points. Commands and write limits arrive in milestone 7; until then every point is read-only.</td></tr>
            <tr><th>Alarms</th><td>Alarm limits arrive in milestone 6.</td></tr>
          </tbody>
        </table>
        <div className="actions">
          <button type="button" className="btn" onClick={() => update({ ...draft, step: 2 })}>Back to the table</button>
          <ActionButton onClick={runCheck}>Check again</ActionButton>
        </div>
      </section>
      <aside className="side-pane" aria-label="Verify">
        <p className="eyebrow">Verify and go live</p>
        <h2>{!checked ? "Checking…" : v.errors.length ? `${v.errors.length} error${v.errors.length > 1 ? "s" : ""} block go-live` : "Ready to go live"}</h2>
        {v.errors.length > 0 && (
          <div className="note bad"><strong>Errors</strong><ul>{v.errors.map((e) => <li key={e}>{e}</li>)}</ul></div>
        )}
        {v.warnings.length > 0 && (
          <div className="note warn"><strong>Warnings (can be accepted)</strong><ul>{v.warnings.map((e) => <li key={e}>{e}</li>)}</ul></div>
        )}
        <OutcomeBox outcome={outcome} />
        <ActionButton
          primary
          disabled={!checked || v.errors.length > 0}
          onClick={async () => {
            try {
              const r = await commit(config, draft.revision!, `commissioned ${draft.device.name}: ${points.length} points`);
              update({ ...draft, live: true, step: 4 });
              setOutcome({ kind: "ok", message: "Live.", issues: r.warnings });
            } catch (e) {
              const o = outcomeFrom(e);
              if (e instanceof SetupError && e.status === 409) {
                o.message += " Go back to step 2 and build the table again on the current setup.";
              }
              setOutcome(o);
            }
          }}
        >
          Go live
        </ActionButton>
        <p className="muted small">Go live is blocked until errors are fixed. The change is recorded in the change log and can be exported as a setup file.</p>
      </aside>
    </div>
  );
}

// ---------- step 4: graphics (optional) ----------

function StepPlace({ draft }: { draft: WizardDraft }) {
  const equips = (draft.config?.equips ?? []).filter((e) => !(draft.base?.equips ?? []).some((b) => b.name === e.name));
  return (
    <div className="main-pane narrow-page">
      <h2>4. Place on graphics (optional)</h2>
      <p className="note">
        {draft.device.dis || draft.device.name} is live. The graphics editor arrives in milestone 9; until then each room shows
        its points as a list, so there is nothing to place yet. Skip this step; equipment can be placed later from the
        graphics editor.
      </p>
      {equips.length > 0 && (
        <ul>
          {equips.map((e) => (
            <li key={e.name}>{e.dis} <span className="muted mono">{e.name}</span></li>
          ))}
        </ul>
      )}
      <div className="actions">
        <button type="button" className="btn primary" onClick={() => { removeDraft(draft.id); location.hash = "#/setup/mapping"; }}>
          Finish and open the mapping table
        </button>
      </div>
    </div>
  );
}

