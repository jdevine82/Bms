// Import (main spec 5.3.2): paste text or choose a .bmslogic.json file, then import it as a new
// diagram or paste it into the open one. It is checked first: errors are listed per block (with
// "Remove the failing blocks and continue"), unresolved points, variables and schedules are
// mapped in a table with suggestions, and clashing block IDs are renumbered.
import { useEffect, useMemo, useState } from "react";
import type { Point } from "../api";
import { post } from "../setup/api";
import { SetupError } from "../setup/api";
import { mergeFragment, type Fragment } from "./diagramOps";
import type { Diagram, Issue, SiteVariable } from "./logicApi";
import { SCHEMA, importLogic } from "./logicApi";

type Kind = "point" | "variable" | "schedule";
interface Ref { kind: Kind; name: string }

function similarity(a: string, b: string): number {
  const ta = new Set(a.toLowerCase().split(/[_\-\s.]+/).filter(Boolean));
  const tb = new Set(b.toLowerCase().split(/[_\-\s.]+/).filter(Boolean));
  let shared = 0;
  for (const t of ta) {
    if (tb.has(t)) shared++;
    else if ([...tb].some((u) => u.startsWith(t) || t.startsWith(u))) shared += 0.5; // meter / meter1
  }
  const last = a.split("_").pop() === b.split("_").pop() ? 0.5 : 0;
  return shared / Math.max(ta.size, tb.size, 1) + last;
}

export function ImportDialog({ current, points, variables, schedules, onClose, onPaste, onCreated, initialText }: {
  current?: Diagram;
  initialText?: string; // a snippet to map before inserting
  points: Point[];
  variables: SiteVariable[];
  schedules: { name: string; dis: string }[];
  onClose: () => void;
  onPaste?: (d: Diagram, added: string[]) => void;
  onCreated?: (name: string) => void;
}) {
  const [text, setText] = useState(initialText ?? "");
  const [parsed, setParsed] = useState<Fragment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [how, setHow] = useState<"new" | "paste">(current ? "paste" : "new");
  const [name, setName] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (initialText) read(initialText); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const read = (source: string) => {
    setIssues(null);
    setMapping({});
    try {
      const raw = JSON.parse(source);
      if (!raw || typeof raw !== "object" || !Array.isArray(raw.blocks)) throw new Error("it has no list of blocks");
      if (raw.schema && raw.schema !== SCHEMA) throw new Error(`it is ${raw.schema}, not ${SCHEMA}`);
      setParsed(raw);
      setName((n) => n || (typeof raw.name === "string" ? raw.name : ""));
      setError(null);
    } catch (e) {
      setParsed(null);
      setError(`This is not a logic diagram: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // What the text refers to that this site (and the diagram it lands in) does not have.
  const unresolved: Ref[] = useMemo(() => {
    if (!parsed) return [];
    const pointNames = new Set(points.map((p) => p.name));
    const varNames = new Set([...variables.filter((v) => v.scope === "site").map((v) => v.name),
                              ...(parsed.variables ?? []).map((v) => v.name),
                              ...(how === "paste" && current ? current.variables.map((v) => v.name) : [])]);
    const schedNames = new Set(schedules.map((s) => s.name));
    const out = new Map<string, Ref>();
    for (const b of parsed.blocks) {
      const s = b.settings ?? {};
      if ((b.type === "point_read" || b.type === "point_write") && s.point && !pointNames.has(s.point)) out.set(`point:${s.point}`, { kind: "point", name: s.point });
      if ((b.type === "variable_read" || b.type === "variable_write") && s.variable && !varNames.has(s.variable)) out.set(`variable:${s.variable}`, { kind: "variable", name: s.variable });
      if (b.type === "schedule" && s.schedule && !schedNames.has(s.schedule)) out.set(`schedule:${s.schedule}`, { kind: "schedule", name: s.schedule });
    }
    return [...out.values()];
  }, [parsed, points, variables, schedules, how, current]);

  const choices = (r: Ref): { name: string; label: string }[] => {
    const all = r.kind === "point" ? points.map((p) => ({ name: p.name, label: `${p.name} (${p.dis})` }))
      : r.kind === "schedule" ? schedules.map((s) => ({ name: s.name, label: `${s.name} (${s.dis})` }))
      : [...variables.filter((v) => v.scope === "site").map((v) => ({ name: v.name, label: `${v.name} (site)` })),
         ...(current?.variables ?? []).map((v) => ({ name: v.name, label: `${v.name} (this diagram)` }))];
    return all.sort((a, b) => similarity(r.name, b.name) - similarity(r.name, a.name));
  };

  /** The text with the mapping applied. */
  const mapped = (): Fragment | null => {
    if (!parsed) return null;
    const rename = (kind: Kind, n: string) => mapping[`${kind}:${n}`] || n;
    return { ...parsed, blocks: parsed.blocks.map((b) => {
      const s = { ...(b.settings ?? {}) };
      if (s.point) s.point = rename("point", s.point);
      if (s.variable) s.variable = rename("variable", s.variable);
      if (s.schedule) s.schedule = rename("schedule", s.schedule);
      return { ...b, settings: s };
    }) };
  };

  const candidate = (): { diagram: Diagram; added: string[] } | null => {
    const m = mapped();
    if (!m) return null;
    if (how === "paste" && current) return mergeFragment(current, m, { x: 0, y: 0 });
    const d: Diagram = { name: name.trim(), dis: (m.dis as string) ?? name, scope: (m.scope as Diagram["scope"]) ?? "site",
                         equip: (m.equip as string) ?? null, variables: m.variables ?? [], blocks: m.blocks, wires: m.wires ?? [] };
    return { diagram: d, added: d.blocks.map((b) => b.id) };
  };

  const check = async () => {
    const c = candidate();
    if (!c) return;
    if (how === "new" && !/^[a-z][a-z0-9_]*$/.test(c.diagram.name)) {
      setError("Give the new diagram a name: lowercase letters, digits and _, starting with a letter.");
      return;
    }
    setBusy(true);
    try {
      const r = await post<{ ok: boolean; valid: boolean; exists: boolean; issues: Issue[] }>("/api/logic/import",
        { diagram: c.diagram, name: c.diagram.name, check: true });
      const added = new Set(c.added);
      // When pasting, only the pasted blocks' problems matter here.
      const relevant = how === "paste" ? r.issues.filter((i) => !i.block || added.has(i.block)) : r.issues;
      setIssues(relevant);
      setError(how === "new" && r.exists ? `There is already a diagram '${c.diagram.name}'.` : null);
    } catch (e) {
      setError(e instanceof SetupError ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const errorBlocks = new Set((issues ?? []).filter((i) => i.level === "error" && i.block).map((i) => i.block!));
  const blockingWithoutBlock = (issues ?? []).some((i) => i.level === "error" && !i.block);

  const removeFailing = () => {
    if (!parsed) return;
    // Block IDs in the issues are after renumbering when pasting; map them back by order.
    const c = candidate()!;
    const failing = new Set(c.added.map((id, k) => (errorBlocks.has(id) ? parsed.blocks[k].id : null)).filter(Boolean) as string[]);
    const next = { ...parsed, blocks: parsed.blocks.filter((b) => !failing.has(b.id)),
                   wires: (parsed.wires ?? []).filter((w) => !failing.has(w.from.block) && !failing.has(w.to.block)) };
    setParsed(next);
    setText(JSON.stringify(next, null, 2));
    setIssues(null);
  };

  const apply = async () => {
    const c = candidate();
    if (!c) return;
    if (how === "paste") {
      onPaste?.(c.diagram, c.added);
      return;
    }
    setBusy(true);
    try {
      await importLogic(c.diagram, c.diagram.name);
      onCreated?.(c.diagram.name);
    } catch (e) {
      setError(e instanceof SetupError ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <div className="dialog-backdrop" role="dialog" aria-label="Import">
      <div className="dialog wide">
        <h3>Import logic</h3>
        <p className="muted small">Paste a diagram or blocks in the {SCHEMA} format, or choose a .bmslogic.json file. Imported logic is a draft until published.</p>
        <textarea className="json" aria-label="Logic JSON" value={text} placeholder='{"schema": "devine-bms-logic/1", "blocks": [...], "wires": [...]}'
                  onChange={(e) => { setText(e.target.value); setParsed(null); setIssues(null); }} />
        <div className="actions">
          <input type="file" accept=".json,application/json" aria-label="Logic file"
                 onChange={async (e) => { const f = e.target.files?.[0]; if (f) { const t = await f.text(); setText(t); read(t); } }} />
          <button type="button" className="btn" onClick={() => read(text)}>Read</button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {parsed && (
          <>
            <p>{parsed.blocks.length} block{parsed.blocks.length === 1 ? "" : "s"}, {(parsed.wires ?? []).length} wire{(parsed.wires ?? []).length === 1 ? "" : "s"}.</p>
            {current && (
              <div className="seg" role="group" aria-label="How to import">
                <button type="button" className={how === "paste" ? "active" : ""} onClick={() => { setHow("paste"); setIssues(null); }}>Paste into this diagram</button>
                <button type="button" className={how === "new" ? "active" : ""} onClick={() => { setHow("new"); setIssues(null); }}>Import as new diagram</button>
              </div>
            )}
            {how === "new" && (
              <div className="prop">
                <label htmlFor="import-name">New diagram's name</label>
                <input id="import-name" value={name} onChange={(e) => { setName(e.target.value); setIssues(null); }} />
              </div>
            )}
            {unresolved.length > 0 && (
              <>
                <h4>Not found on this site</h4>
                <table className="grid compact" aria-label="Mapping">
                  <thead><tr><th>In the text</th><th>Use</th></tr></thead>
                  <tbody>
                    {unresolved.map((r) => {
                      const key = `${r.kind}:${r.name}`;
                      return (
                        <tr key={key}>
                          <td>{r.kind} <code>{r.name}</code></td>
                          <td>
                            <select aria-label={`Map ${r.kind} ${r.name}`} value={mapping[key] ?? ""}
                                    onChange={(e) => { setMapping({ ...mapping, [key]: e.target.value }); setIssues(null); }}>
                              <option value="">Leave unresolved (fix it in the draft)</option>
                              {choices(r).map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
                            </select>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}
            {issues && (
              <>
                <h4>Check</h4>
                {issues.length === 0 && <p className="muted">No problems.</p>}
                <ul className="issue-list">
                  {issues.map((i, k) => (
                    <li key={k} className={i.level}><span className="issue-level">{i.level === "error" ? "Error" : "Warning"}</span>{" "}
                      {i.block ? `${i.block}: ` : ""}{i.message}</li>
                  ))}
                </ul>
                {errorBlocks.size > 0 && (
                  <button type="button" className="btn" onClick={removeFailing}>Remove the failing blocks and continue</button>
                )}
              </>
            )}
            <div className="actions">
              <button type="button" className="btn" disabled={busy} onClick={() => void check()}>Check</button>
              <button type="button" className="btn primary"
                      disabled={busy || issues === null || errorBlocks.size > 0 || blockingWithoutBlock || !!error}
                      onClick={() => void apply()}>
                {how === "paste" ? "Paste" : "Import"}
              </button>
            </div>
          </>
        )}
        <div className="actions"><button type="button" className="btn" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  );
}
