// The logic editor (main spec 5.3.1, milestone 10B): Edit mode on the draft (saved a second after
// each change) and Live mode on the published version.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, BackgroundVariant, Controls, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow,
  type Connection, type Edge, type Node, type NodeChange, type EdgeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Point } from "../api";
import { getPoints } from "../api";
import { useAuth } from "../auth/AuthGate";
import { get } from "../setup/api";
import { SetupError } from "../setup/api";
import { BLOCKS, KIND_TYPE, TIMING, portsOf, type TypeInfo } from "./blocks";
import { BlockNode, fmt, type BlockData } from "./BlockNode";
import { ImportDialog } from "./ImportDialog";
import type { Block, Detail, Diagram, Issue, Live, SiteVariable, Wire } from "./logicApi";
import * as api from "./logicApi";
import { Palette } from "./Palette";
import { Properties } from "./Properties";
import { TestStrip } from "./TestStrip";
import { autoLayout, fragmentOf, mergeFragment, wireId } from "./diagramOps";
import "./logic.css";

const nodeTypes = { block: BlockNode };
const GRID: [number, number] = [20, 20];

export function LogicEditor({ name, onClose }: { name: string; onClose: () => void }) {
  return (
    <ReactFlowProvider>
      <Editor name={name} onClose={onClose} />
    </ReactFlowProvider>
  );
}

function Editor({ name, onClose }: { name: string; onClose: () => void }) {
  const { can } = useAuth();
  const admin = can("admin");
  const flow = useReactFlow();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [d, setD] = useState<Diagram | null>(null);
  const [mode, setMode] = useState<"edit" | "live">(admin ? "edit" : "live");
  const [points, setPoints] = useState<Point[]>([]);
  const [variables, setVariables] = useState<SiteVariable[]>([]);
  const [schedules, setSchedules] = useState<{ name: string; dis: string }[]>([]);
  const [equips, setEquips] = useState<{ name: string; dis: string }[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [saving, setSaving] = useState<"saved" | "saving" | "error" | "idle">("idle");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectedEdges, setSelectedEdges] = useState<Set<string>>(new Set());
  const [live, setLive] = useState<Live | null>(null);
  const [dialog, setDialog] = useState<null | "publish" | "versions" | "import" | "export">(null);
  const [menu, setMenu] = useState<{ x: number; y: number; id: string } | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const past = useRef<Diagram[]>([]);
  const future = useRef<Diagram[]>([]);
  const dragStart = useRef<Diagram | null>(null);
  const clipboard = useRef<string | null>(null);
  const loaded = useRef(false);
  const [, bump] = useState(0);

  const reload = useCallback(async () => {
    const got = await api.getLogic(name);
    setDetail(got);
    const draft = { ...got.draft, variables: got.draft.variables ?? [], blocks: got.draft.blocks ?? [], wires: got.draft.wires ?? [],
                    scope: got.draft.scope ?? "site", dis: got.draft.dis ?? name };
    setD(autoLayout(draft));
    loaded.current = false;
  }, [name]);

  useEffect(() => {
    reload().catch((e) => setMessage({ kind: "error", text: e instanceof SetupError ? e.message : String(e) }));
    getPoints().then(setPoints).catch(() => undefined);
    api.listVariables().then(setVariables).catch(() => undefined);
    get<{ name: string; dis: string }[]>("/api/schedules").then(setSchedules).catch(() => undefined);
    get<{ equips?: { name: string; dis: string }[] }>("/api/site").then((s) => setEquips(s.equips ?? [])).catch(() => undefined);
  }, [reload]);

  // Autosave the draft a second after each change (main spec 5.3.1: drafts save automatically).
  useEffect(() => {
    if (!d || !admin) return;
    if (!loaded.current) {
      loaded.current = true;
      if (d.blocks.length) api.checkDraft(name, d).then((r) => setIssues(r.issues)).catch(() => undefined);
      return;
    }
    setSaving("saving");
    const timer = window.setTimeout(() => {
      api.saveDraft(name, d)
        .then((r) => { setIssues(r.issues); setSaving("saved"); })
        .catch(() => setSaving("error"));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [d, name, admin]);

  // Live mode: the published version's values, once a second.
  useEffect(() => {
    if (mode !== "live") return;
    let stop = false;
    const tick = () => api.getLive(name).then((l) => !stop && setLive(l)).catch(() => undefined);
    void tick();
    const timer = window.setInterval(tick, 1000);
    return () => { stop = true; window.clearInterval(timer); };
  }, [mode, name]);

  const typeInfo: TypeInfo = useMemo(() => {
    const kinds = Object.fromEntries(points.map((p) => [p.name, KIND_TYPE[p.kind]]));
    const varTypes: Record<string, any> = Object.fromEntries(variables.filter((v) => v.scope === "site").map((v) => [v.name, v.type]));
    for (const v of d?.variables ?? []) varTypes[v.name] = v.type;
    return { pointType: (n) => kinds[n] ?? null, variableType: (n) => varTypes[n] ?? null };
  }, [points, variables, d?.variables]);

  /** A change that can be undone. */
  const change = useCallback((next: Diagram, before?: Diagram) => {
    setD((cur) => {
      if (cur) {
        past.current.push(before ?? cur);
        if (past.current.length > 300) past.current.shift();
        future.current = [];
      }
      return next;
    });
    bump((n) => n + 1);
  }, []);
  const undo = () => {
    const prev = past.current.pop();
    if (prev && d) { future.current.push(d); setD(prev); bump((n) => n + 1); }
  };
  const redo = () => {
    const next = future.current.pop();
    if (next && d) { past.current.push(d); setD(next); bump((n) => n + 1); }
  };

  const shown: Diagram | null = mode === "live" ? (detail?.published ?? null) : d;
  const units = useMemo(() => Object.fromEntries(points.map((p) => [p.name, p.unit])), [points]);

  const nodes: Node[] = useMemo(() => {
    if (!shown) return [];
    const wired = new Map<string, Set<string>>();
    for (const w of shown.wires) {
      if (!wired.has(w.to.block)) wired.set(w.to.block, new Set());
      wired.get(w.to.block)!.add(w.to.port);
    }
    return autoLayout(shown).blocks.map((b): Node => {
      const ports = portsOf(b, typeInfo);
      const out = b.type === "point_write" && live ? live.outputs[b.settings?.point] : undefined;
      const data: BlockData = { block: b, inputs: ports.inputs, outputs: ports.outputs, wired: wired.get(b.id) ?? new Set(),
                                issues: mode === "edit" ? issues.filter((i) => i.block === b.id) : [],
                                live: mode === "live" ? live?.blocks[b.id] : undefined, output: out ?? undefined, units };
      return { id: b.id, type: "block", position: b.position ?? { x: 0, y: 0 }, data, selected: selected.has(b.id) };
    });
  }, [shown, typeInfo, issues, live, mode, selected, units]);

  const edges: Edge[] = useMemo(() => {
    if (!shown) return [];
    const portType = (b: Block | undefined, port: string) =>
      b ? portsOf(b, typeInfo).outputs.find((p) => p.name === port)?.type ?? null : null;
    return shown.wires.map((w): Edge => {
      const id = wireId(w);
      const src = shown.blocks.find((b) => b.id === w.from.block);
      const type = portType(src, w.from.port);
      const value = mode === "live" ? live?.blocks[w.from.block]?.outputs?.[w.from.port] : undefined;
      const liveClass = mode !== "live" ? "" : value === true ? " wire-true" : value === false ? " wire-false"
        : value === null || value === undefined ? " wire-bad" : " wire-value";
      return {
        id, source: w.from.block, sourceHandle: w.from.port, target: w.to.block, targetHandle: w.to.port,
        className: `wire wire-${type ?? "any"}${liveClass}`, selected: selectedEdges.has(id),
        label: mode === "live" && live ? fmt(value, src?.type === "point_read" ? units[src.settings?.point] : null) : undefined,
        labelBgPadding: [4, 2], labelBgBorderRadius: 4, data: { wire: w },
      };
    });
  }, [shown, typeInfo, live, mode, selectedEdges, units]);

  // ---------- editing ----------

  const feedsOutput = (ids: Set<string>): boolean => {
    if (!d) return false;
    const outs = new Set(d.blocks.filter((b) => b.type === "point_write").map((b) => b.id));
    const seen = new Set<string>();
    const stack = [...ids];
    while (stack.length) {
      const id = stack.pop()!;
      if (outs.has(id)) return true;
      for (const w of d.wires) if (w.from.block === id && !seen.has(w.to.block)) { seen.add(w.to.block); stack.push(w.to.block); }
    }
    return false;
  };

  const removeSelection = (ids = selected, wires = selectedEdges) => {
    if (!d || (!ids.size && !wires.size)) return;
    if (ids.size && feedsOutput(ids) &&
        !confirm("This removes an output block or a block that feeds one. Once published, that output falls to its fail-safe. Remove it?")) return;
    change({ ...d, blocks: d.blocks.filter((b) => !ids.has(b.id)),
             wires: d.wires.filter((w) => !ids.has(w.from.block) && !ids.has(w.to.block) && !wires.has(wireId(w))) });
    setSelected(new Set());
    setSelectedEdges(new Set());
  };

  const copySelection = async (): Promise<string | null> => {
    if (!d || !selected.size) return null;
    const text = JSON.stringify(fragmentOf(d, selected), null, 2);
    clipboard.current = text;
    try { await navigator.clipboard.writeText(text); } catch { /* not allowed here: the editor keeps its own copy */ }
    return text;
  };

  const paste = async (text?: string | null) => {
    if (!d) return;
    let source = text ?? null;
    if (source === null) {
      try { source = await navigator.clipboard.readText(); } catch { source = null; }
      if (!source || !source.trim().startsWith("{")) source = clipboard.current;
    }
    if (!source) return;
    try {
      const { diagram, added } = mergeFragment(d, JSON.parse(source), flowCentre());
      change(diagram);
      setSelected(new Set(added));
    } catch {
      setDialog("import"); // not plain blocks: the import dialog explains what is wrong
    }
  };

  const flowCentre = () => {
    const el = document.querySelector(".logic-canvas");
    const box = el?.getBoundingClientRect();
    return box ? flow.screenToFlowPosition({ x: box.x + box.width / 2, y: box.y + box.height / 2 }) : { x: 0, y: 0 };
  };

  const addBlock = (type: string, at?: { x: number; y: number }) => {
    if (!d) return;
    const used = new Set(d.blocks.map((b) => b.id));
    let n = 1;
    while (used.has(`${type}_${n}`)) n++;
    const p = at ?? flowCentre();
    const pos = { x: Math.round(p.x / 20) * 20, y: Math.round(p.y / 20) * 20 };
    // Clicked from the palette: step down until the place is free, so it does not cover a block.
    while (!at && d.blocks.some((b) => b.position && Math.abs(b.position.x - pos.x) < 200 && Math.abs(b.position.y - pos.y) < 100)) {
      pos.y += 120;
    }
    const block: Block = { id: `${type}_${n}`, type, settings: structuredClone(BLOCKS[type].defaults), position: pos };
    change({ ...d, blocks: [...d.blocks, block] });
    setSelected(new Set([block.id]));
  };

  useEffect(() => {
    if (mode !== "edit" || !admin) return;
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable]") || dialog) return;
      const ctrl = e.ctrlKey || e.metaKey;
      const k = e.key.toLowerCase();
      if (ctrl && k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (ctrl && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); redo(); }
      else if (ctrl && k === "c") { e.preventDefault(); void copySelection(); }
      else if (ctrl && k === "x") { e.preventDefault(); void copySelection().then((x) => x && removeSelection()); }
      else if (ctrl && k === "v") { e.preventDefault(); void paste(); }
      else if (ctrl && k === "d") { e.preventDefault(); void copySelection().then((x) => { if (x) void paste(x); }); }
      else if (ctrl && k === "a") { e.preventDefault(); setSelected(new Set(d?.blocks.map((b) => b.id))); }
      else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); removeSelection(); }
      else if (e.key === "Escape") { setSelected(new Set()); setSelectedEdges(new Set()); setMenu(null); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  const onNodesChange = (changes: NodeChange[]) => {
    if (!d || mode !== "edit") return;
    let next = d;
    let sel: Set<string> | null = null;
    for (const c of changes) {
      if (c.type === "position" && c.position) {
        if (c.dragging && !dragStart.current) dragStart.current = d;
        const pos = { x: Math.round(c.position.x), y: Math.round(c.position.y) };
        next = { ...next, blocks: next.blocks.map((b) => (b.id === c.id ? { ...b, position: pos } : b)) };
      } else if (c.type === "select") {
        sel = sel ?? new Set(selected);
        if (c.selected) sel.add(c.id); else sel.delete(c.id);
      }
    }
    if (sel) setSelected(sel);
    if (next !== d) {
      const ended = changes.some((c) => c.type === "position" && !c.dragging);
      if (ended && dragStart.current) {
        change(next, dragStart.current);
        dragStart.current = null;
      } else {
        setD(next);
      }
    }
  };
  const onEdgesChange = (changes: EdgeChange[]) => {
    let sel: Set<string> | null = null;
    for (const c of changes) {
      if (c.type === "select") {
        sel = sel ?? new Set(selectedEdges);
        if (c.selected) sel.add(c.id); else sel.delete(c.id);
      }
    }
    if (sel) setSelectedEdges(sel);
  };

  const portTypeOf = (blockId: string | null, port: string | null, side: "in" | "out") => {
    const b = d?.blocks.find((x) => x.id === blockId);
    if (!b || !port) return undefined;
    const p = portsOf(b, typeInfo)[side === "in" ? "inputs" : "outputs"].find((x) => x.name === port);
    return p ? p.type : undefined;
  };
  const isValidConnection = (c: Connection | Edge) => {
    const from = portTypeOf(c.source, c.sourceHandle ?? null, "out");
    const to = portTypeOf(c.target, c.targetHandle ?? null, "in");
    if (from === undefined || to === undefined) return false;
    if (c.source === c.target && !TIMING.has(d!.blocks.find((b) => b.id === c.source)!.type)) return false;
    return from === null || to === null || from === to;
  };
  const onConnect = (c: Connection) => {
    if (!d || !c.sourceHandle || !c.targetHandle) return;
    const w: Wire = { from: { block: c.source, port: c.sourceHandle }, to: { block: c.target, port: c.targetHandle } };
    // A new wire into a used input replaces the old one.
    change({ ...d, wires: [...d.wires.filter((x) => !(x.to.block === c.target && x.to.port === c.targetHandle)), w] });
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const type = e.dataTransfer.getData("application/x-bms-block");
    if (type && BLOCKS[type]) addBlock(type, flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
  };

  if (!d || !detail) {
    return <div className="logic-editor"><p className="muted">{message?.text ?? "Loading…"}</p>
      <button type="button" className="btn" onClick={onClose}>← Logic</button></div>;
  }
  const one = selected.size === 1 ? d.blocks.find((b) => selected.has(b.id)) ?? null : null;
  const status = detail.publishedVersion ? `Published version ${detail.publishedVersion}` : "Draft (not published)";
  const same = (a: Diagram, b: Diagram) => (["dis", "scope", "equip", "variables", "blocks", "wires"] as const)
    .every((k) => JSON.stringify(a[k] ?? null) === JSON.stringify(b[k] ?? null));
  const changed = detail.published !== null && !same(detail.published, d);
  const stateText = { running: "Running", disabled: "Disabled", error: "Not running (error)", draft: "Not published", unknown: "" }[detail.state];
  const errorCount = issues.filter((i) => i.level === "error").length;

  return (
    <div className="logic-editor">
      <header className="logic-bar">
        <button type="button" className="btn" onClick={onClose}>← Logic</button>
        <div className="logic-title">
          <h2>{d.dis || name}</h2>
          <span className="muted small"><code>{name}</code> · {status}{stateText ? ` · ${stateText}` : ""}{changed ? " · draft has unpublished changes" : ""}</span>
        </div>
        {admin && detail.publishedVersion !== null && (
          <label className="logic-switch">
            <input type="checkbox" checked={detail.enabled} onChange={async (e) => {
              const on = e.target.checked;
              setDetail({ ...detail, enabled: on }); // shown at once; reloaded below
              try {
                await api.setEnabled(name, on);
                setMessage({ kind: "ok", text: on ? "Enabled." : "Disabled: its outputs went to their fail-safe." });
                await reload();
              } catch (err) { setMessage({ kind: "error", text: String(err) }); }
            }} /> Enabled
          </label>
        )}
        <span className="logic-spacer" />
        <div className="seg" role="group" aria-label="Mode">
          {admin && <button type="button" className={mode === "edit" ? "active" : ""} aria-pressed={mode === "edit"} onClick={() => setMode("edit")}>Edit</button>}
          <button type="button" className={mode === "live" ? "active" : ""} aria-pressed={mode === "live"} onClick={() => setMode("live")}>Live</button>
        </div>
        {admin && mode === "edit" && (
          <>
            <span className="muted small" aria-live="polite">{saving === "saving" ? "Saving…" : saving === "saved" ? "Draft saved" : saving === "error" ? "Not saved!" : ""}</span>
            <button type="button" className="btn" disabled={!past.current.length} onClick={undo} title="Ctrl+Z">Undo</button>
            <button type="button" className="btn" disabled={!future.current.length} onClick={redo} title="Ctrl+Y">Redo</button>
            <button type="button" className="btn" onClick={() => setDialog("import")}>Import</button>
            <button type="button" className="btn" onClick={() => setDialog("export")}>Export</button>
            <button type="button" className="btn" onClick={() => setDialog("versions")}>Versions</button>
            <button type="button" className="btn primary" onClick={() => setDialog("publish")}>Publish…</button>
          </>
        )}
      </header>
      {message && <div className={`outcome ${message.kind}`} role={message.kind === "error" ? "alert" : "status"}>
        <p>{message.text}</p></div>}

      <div className={`logic-body${mode === "live" ? " live" : ""}`}>
        {mode === "edit" && <Palette onAdd={(t) => addBlock(t)} />}
        <div className="logic-main">
          <div className="logic-canvas" onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
            {mode === "live" && !detail.published ? (
              <p className="muted logic-empty">Not published yet: Live mode shows the published version.</p>
            ) : (
              <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                         onConnect={onConnect} isValidConnection={isValidConnection} snapToGrid snapGrid={GRID}
                         nodesDraggable={mode === "edit"} nodesConnectable={mode === "edit"} elementsSelectable
                         deleteKeyCode={null} multiSelectionKeyCode="Shift" selectionKeyCode="Shift" fitView
                         fitViewOptions={{ padding: 0.2, maxZoom: 1 }} minZoom={0.2}
                         onNodeContextMenu={(e, n) => { if (mode !== "edit") return; e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, id: n.id }); }}
                         onPaneClick={() => setMenu(null)} proOptions={{ hideAttribution: false }}>
                <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
                <MiniMap pannable zoomable nodeClassName="mm-node" nodeStrokeWidth={2} />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
            {mode === "edit" && (selected.size > 0 || selectedEdges.size > 0) && (
              <div className="canvas-tools" role="toolbar" aria-label="Selection">
                <button type="button" className="btn danger" onClick={() => removeSelection()}>Delete</button>
                {selected.size > 0 && <button type="button" className="btn" onClick={() => void copySelection().then((x) => { if (x) void paste(x); })}>Duplicate</button>}
              </div>
            )}
            {menu && (
              <div className="context-menu" style={{ left: menu.x, top: menu.y }} role="menu">
                <button type="button" role="menuitem" onClick={() => { removeSelection(new Set([menu.id]), new Set()); setMenu(null); }}>Delete</button>
                <button type="button" role="menuitem" onClick={() => {
                  const ids = new Set([menu.id]);
                  setMenu(null);
                  const text = JSON.stringify(fragmentOf(d, ids));
                  void paste(text);
                }}>Duplicate</button>
              </div>
            )}
          </div>
          {mode === "edit" && admin && <TestStrip name={name} diagram={d} points={points} />}
        </div>
        <Properties diagram={d} block={one} mode={mode} admin={admin} points={points} variables={variables} schedules={schedules}
                    equips={equips} issues={issues} live={live} typeInfo={typeInfo}
                    onDiagram={(next) => change(next)} onSelect={(id) => setSelected(new Set([id]))}
                    onVariables={() => api.listVariables().then(setVariables)} />
      </div>

      {dialog === "publish" && <PublishDialog name={name} diagram={d} errors={errorCount} onClose={() => setDialog(null)}
                                              onDone={async (text) => { setDialog(null); setMessage({ kind: "ok", text }); await reload(); }} />}
      {dialog === "versions" && <VersionsDialog detail={detail} onClose={() => setDialog(null)}
                                                onRestore={async (v) => {
                                                  await api.restore(name, v);
                                                  setDialog(null);
                                                  setMessage({ kind: "ok", text: `Version ${v} is in the draft now; publish it to run it.` });
                                                  await reload();
                                                }} />}
      {dialog === "import" && <ImportDialog current={d} points={points} variables={variables} schedules={schedules}
                                            onClose={() => setDialog(null)}
                                            onPaste={(diagram, added) => { change(diagram); setSelected(new Set(added)); setDialog(null); }} />}
      {dialog === "export" && <ExportDialog diagram={d} selection={selected} onClose={() => setDialog(null)} />}
    </div>
  );
}

function PublishDialog({ name, diagram, errors, onClose, onDone }: {
  name: string; diagram: Diagram; errors: number; onClose: () => void; onDone: (text: string) => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [problems, setProblems] = useState<Issue[] | null>(null);
  const [busy, setBusy] = useState(false);
  const outputs = diagram.blocks.filter((b) => b.type === "point_write");
  const failsafe = (b: Block) => {
    const f = b.settings?.failsafe ?? { mode: "release" };
    return f.mode === "release" ? "release to the device" : f.mode === "hold" ? "hold the last value" : `go to ${fmt(f.value)}`;
  };
  return (
    <div className="dialog-backdrop" role="dialog" aria-label="Publish">
      <div className="dialog">
        <h3>Publish {diagram.dis || name}</h3>
        <p className="muted">A new version runs at once. If an input goes bad, logic is disabled or the server stops, each output goes to its fail-safe:</p>
        {outputs.length ? (
          <ul className="failsafe-list">
            {outputs.map((b) => <li key={b.id}><strong>{b.settings?.point || b.id}</strong>: {failsafe(b)}</li>)}
          </ul>
        ) : <p className="muted">This diagram writes no points.</p>}
        {errors > 0 && <p className="error">The draft has {errors} error{errors > 1 ? "s" : ""}; see Checks on the right.</p>}
        <label className="prop-inline"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm these fail-safes</label>
        <div className="prop">
          <label htmlFor="publish-note">Note (what changed)</label>
          <input id="publish-note" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        {problems && <ul className="issue-list">{problems.filter((i) => i.level === "error").map((i, k) => <li key={k}>{i.block ? `${i.block}: ` : ""}{i.message}</li>)}</ul>}
        <div className="actions">
          <button type="button" className="btn primary" disabled={(!confirmed && outputs.length > 0) || busy} onClick={async () => {
            setBusy(true);
            try {
              await api.saveDraft(name, diagram);
              const r = await api.publish(name, note);
              onDone(`Published version ${r.version}; it is running.`);
            } catch (e) {
              setProblems(e instanceof SetupError ? e.issues as unknown as Issue[] : []);
              if (!(e instanceof SetupError) || !e.issues.length) setProblems([{ level: "error", message: String(e instanceof Error ? e.message : e) }]);
            } finally { setBusy(false); }
          }}>Publish</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function VersionsDialog({ detail, onClose, onRestore }: { detail: Detail; onClose: () => void; onRestore: (v: number) => void }) {
  return (
    <div className="dialog-backdrop" role="dialog" aria-label="Versions">
      <div className="dialog">
        <h3>Versions</h3>
        {detail.versions.length === 0 && <p className="muted">Nothing published yet.</p>}
        <table className="grid">
          <thead><tr><th>Version</th><th>Published</th><th>By</th><th>Note</th><th /></tr></thead>
          <tbody>
            {[...detail.versions].reverse().map((v) => (
              <tr key={v.version}>
                <td>{v.version}{v.version === detail.publishedVersion ? " (running)" : ""}</td>
                <td>{new Date(v.at).toLocaleString()}</td>
                <td>{v.by}</td>
                <td>{v.note}</td>
                <td><button type="button" className="link" onClick={() => onRestore(v.version)}>Restore into draft</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="actions"><button type="button" className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}

function ExportDialog({ diagram, selection, onClose }: { diagram: Diagram; selection: Set<string>; onClose: () => void }) {
  const [what, setWhat] = useState<"all" | "selection">(selection.size ? "selection" : "all");
  const data = what === "all" ? { ...diagram, schema: api.SCHEMA } : fragmentOf(diagram, selection);
  const text = JSON.stringify(data, null, 2);
  const [copied, setCopied] = useState(false);
  return (
    <div className="dialog-backdrop" role="dialog" aria-label="Export">
      <div className="dialog wide">
        <h3>Export</h3>
        <div className="seg" role="group" aria-label="What to export">
          <button type="button" className={what === "all" ? "active" : ""} onClick={() => setWhat("all")}>Whole diagram</button>
          <button type="button" className={what === "selection" ? "active" : ""} disabled={!selection.size} onClick={() => setWhat("selection")}>
            Selected blocks ({selection.size})</button>
        </div>
        <textarea className="json" readOnly value={text} aria-label="Exported JSON" />
        <div className="actions">
          <button type="button" className="btn" onClick={async () => {
            try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setCopied(false); }
          }}>{copied ? "Copied" : "Copy"}</button>
          <a className="btn" download={`${diagram.name}.bmslogic.json`} href={`data:application/json;charset=utf-8,${encodeURIComponent(text)}`}>
            Save as file</a>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
