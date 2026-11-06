// Setup > Graphics > Site symbols (milestone 9B): the site's own symbols, drawn here or imported
// as SVG (cleaned on the server, with an Off group and an On group).
import { useState } from "react";
import { del, post, put } from "../../setup/api";
import type { Outcome } from "../../setup/ui";
import { ActionButton, OutcomeBox, outcomeFrom, useLoad } from "../../setup/ui";
import type { LibrarySymbol, SymbolShape } from "../graphicsApi";
import { getLibrary } from "../graphicsApi";
import { SymbolIcon } from "./Palette";
import { SymbolBoard, symbolFromShapes } from "./SymbolBoard";

interface Draft {
  existing: boolean;
  id: string;
  dis: string;
  category: string;
  kind: LibrarySymbol["kind"];
  animation: "none" | "spin" | "flash";
  mode: "draw" | "import";
  shapes: SymbolShape[];
  svg: string; // imported (cleaned) SVG
  removed: string[] | null;
}

const blank = (mode: Draft["mode"]): Draft => ({ existing: false, id: "", dis: "", category: "Site symbols", kind: "state",
  animation: "none", mode, shapes: [], svg: "", removed: null });

export function SymbolsPanel() {
  const lib = useLoad(() => getLibrary(), []);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const site = (lib.data ?? []).filter((s) => s.source === "site");
  const svg = draft ? (draft.mode === "draw" ? symbolFromShapes(draft.shapes) : draft.svg) : "";
  const preview: LibrarySymbol | null = draft && svg
    ? { id: draft.id || "preview", dis: draft.dis, category: draft.category, source: "site", kind: draft.kind, svg }
    : null;

  const importFile = async (file: File) => {
    try {
      const r = await post<{ svg: string; viewBox: string; removed: string[] }>("/api/setup/graphics/sanitize",
        { svg: await file.text(), purpose: "symbol", prefix: draft?.id || "import" });
      setDraft((d) => ({ ...(d ?? blank("import")), mode: "import", svg: r.svg, removed: r.removed,
                         dis: d?.dis || file.name.replace(/\.svg$/i, "") }));
      setOutcome(null);
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  const save = async () => {
    if (!draft) return;
    if (!/^[a-z0-9][a-z0-9-]*$/.test(draft.id)) {
      setOutcome({ kind: "error", message: "Give the symbol an ID: lowercase letters, digits and -." });
      return;
    }
    try {
      await put(`/api/setup/graphics/symbols/${encodeURIComponent(draft.id)}`, {
        dis: draft.dis || draft.id, category: draft.category, kind: draft.kind, animation: draft.animation, svg,
        ...(draft.mode === "draw" ? { shapes: draft.shapes } : {}),
      });
      setOutcome({ kind: "ok", message: `Saved the symbol ${draft.dis || draft.id}; it is in the editor's palette.` });
      setDraft(null);
      await lib.reload();
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  return (
    <section aria-labelledby="symbols-h">
      <h3 id="symbols-h">Site symbols</h3>
      <p className="muted">
        The site's own symbols, next to the library in the editor's palette. Each has an Off group and an On group: Off shows
        while it is not running, On while it runs.
      </p>
      {site.length === 0 && <p className="muted">No site symbols yet.</p>}
      <div className="symbol-list">
        {site.map((s) => (
          <div key={s.id} className="symbol-card">
            <div className="pair" aria-label={`${s.dis}: Off and On`}>
              <SymbolIcon sym={s} size={48} /><SymbolIcon sym={s} on size={48} />
            </div>
            <strong>{s.dis}</strong>
            <code className="small">{s.id}</code>
            <div className="actions">
              <button type="button" className="link" onClick={() => setDraft({
                existing: true, id: s.id, dis: s.dis, category: s.category, kind: s.kind, animation: s.animation ?? "none",
                mode: s.shapes ? "draw" : "import", shapes: s.shapes ?? [], svg: s.svg ?? "", removed: null })}>
                {s.shapes ? "Edit" : "Replace"}
              </button>
              <ActionButton danger onClick={async () => {
                if (!confirm(`Remove the symbol ${s.dis}?`)) return;
                try {
                  await del(`/api/setup/graphics/symbols/${encodeURIComponent(s.id)}`);
                  setOutcome({ kind: "ok", message: `Removed ${s.dis}.` });
                  await lib.reload();
                } catch (e) { setOutcome(outcomeFrom(e)); }
              }}>Remove</ActionButton>
            </div>
          </div>
        ))}
      </div>
      {!draft && (
        <div className="actions">
          <button type="button" className="btn" onClick={() => { setOutcome(null); setDraft(blank("draw")); }}>Draw a symbol</button>
          <button type="button" className="btn" onClick={() => { setOutcome(null); setDraft(blank("import")); }}>Import an SVG</button>
        </div>
      )}
      <OutcomeBox outcome={outcome} />
      {draft && (
        <div className="form symbol-form">
          <div className="prop-row">
            <div className="prop">
              <label htmlFor="sym-id">ID</label>
              <input id="sym-id" value={draft.id} readOnly={draft.existing} placeholder="for example pump-twin"
                     onChange={(e) => setDraft({ ...draft, id: e.target.value })} />
            </div>
            <div className="prop">
              <label htmlFor="sym-dis">Name</label>
              <input id="sym-dis" value={draft.dis} onChange={(e) => setDraft({ ...draft, dis: e.target.value })} />
            </div>
            <div className="prop">
              <label htmlFor="sym-cat">Category</label>
              <input id="sym-cat" value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} />
            </div>
            <div className="prop">
              <label htmlFor="sym-kind">Kind</label>
              <select id="sym-kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Draft["kind"] })}>
                <option value="state">State (On or Off)</option><option value="position">Position</option>
                <option value="level">Level</option><option value="value">Value</option>
              </select>
            </div>
            <div className="prop">
              <label htmlFor="sym-anim">When On</label>
              <select id="sym-anim" value={draft.animation} onChange={(e) => setDraft({ ...draft, animation: e.target.value as Draft["animation"] })}>
                <option value="none">No animation</option><option value="spin">Spin</option><option value="flash">Flash the On group</option>
              </select>
            </div>
          </div>
          {draft.mode === "draw" ? (
            <SymbolBoard shapes={draft.shapes} onChange={(shapes) => setDraft({ ...draft, shapes })} />
          ) : (
            <div className="prop">
              <label htmlFor="sym-file">SVG file</label>
              <input id="sym-file" type="file" accept=".svg,image/svg+xml"
                     onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
              <span className="muted small">
                Groups with the IDs off and on (or Inkscape layers named Off and On). Scripts, images, links and style sheets are removed; 64 KB at most.
              </span>
            </div>
          )}
          {draft.removed && (
            <p className="muted small" role="status">
              {draft.removed.length ? `Removed from the file: ${draft.removed.join(", ")}.` : "Nothing needed removing."}
            </p>
          )}
          {preview && (
            <div className="symbol-previews" aria-label="Preview">
              <figure><SymbolIcon sym={preview} size={64} /><figcaption>Off</figcaption></figure>
              <figure><SymbolIcon sym={preview} on size={64} /><figcaption>On</figcaption></figure>
            </div>
          )}
          <div className="actions">
            <ActionButton primary disabled={!svg || (draft.mode === "draw" && draft.shapes.length === 0)} onClick={save}>
              Save symbol
            </ActionButton>
            <button type="button" className="btn" onClick={() => setDraft(null)}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}
