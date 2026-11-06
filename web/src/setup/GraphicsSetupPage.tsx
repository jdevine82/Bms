// Setup > Graphics (milestone 9A): the saved graphics, starting graphics made again, and a JSON
// editor for one graphic until the graphics editor arrives (9B). Every save is a checked change.
import { useState } from "react";
import type { Graphic } from "../graphics/graphicsApi";
import { getGraphics } from "../graphics/graphicsApi";
import { del, post, put } from "./api";
import type { Outcome } from "./ui";
import { ActionButton, OutcomeBox, outcomeFrom, useLoad } from "./ui";

const NEW: Graphic = { name: "new_graphic", dis: "New graphic", kind: "system", width: 1000, height: 600,
  tab: { order: 900 }, items: [{ type: "text", id: "title", x: 20, y: 30, text: "New graphic", size: 22 }] };

export function GraphicsSetupPage() {
  const list = useLoad(() => getGraphics(), []);
  const [editing, setEditing] = useState<{ name: string | null; text: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const graphics = list.data?.graphics ?? [];

  const parsed = (): Graphic | null => {
    try {
      return JSON.parse(editing!.text) as Graphic;
    } catch (e) {
      setOutcome({ kind: "error", message: `This is not valid JSON: ${String(e)}` });
      return null;
    }
  };
  const save = async (check: boolean) => {
    const g = parsed();
    if (!g) return;
    try {
      const r = await put(`/api/setup/graphics/${encodeURIComponent(g.name)}${check ? "?check=true" : ""}`, g);
      setOutcome({ kind: "ok", message: check ? `${g.dis} passes the checks.` : `Saved ${g.dis}; it is live.`,
                   issues: r.warnings });
      if (!check) {
        setEditing(null);
        await list.reload();
      }
    } catch (e) {
      setOutcome(outcomeFrom(e));
    }
  };

  return (
    <section className="main-pane" aria-labelledby="graphics-h">
      <h2 id="graphics-h">Graphics</h2>
      <p className="muted">
        The Graphics page and the HMI's Graphics tab show these, Site first and then in tab order.
        {list.data?.source === "generated" &&
          " None are saved yet, so the starting graphics made from the rooms and equipment are shown. Saving or changing one saves them all."}
      </p>
      {list.error && <p className="error">{list.error}</p>}
      <table className="grid">
        <thead><tr><th>Name</th><th>Shown as</th><th>Kind</th><th>Tab</th><th>Items</th><th /></tr></thead>
        <tbody>
          {graphics.map((g) => (
            <tr key={g.name}>
              <td><code>{g.name}</code></td>
              <td>{g.dis}</td>
              <td>{g.kind}</td>
              <td>{g.tab ? g.tab.order : "not a tab"}</td>
              <td>{g.items.length}</td>
              <td className="row-actions">
                <button type="button" className="link" onClick={() => {
                  setOutcome(null);
                  setEditing({ name: g.name, text: JSON.stringify(g, null, 2) });
                }}>Edit as JSON</button>
                {list.data?.source === "setup" && (
                  <ActionButton danger onClick={async () => {
                    if (!confirm(`Remove the graphic ${g.dis}?`)) return;
                    try {
                      await del(`/api/setup/graphics/${encodeURIComponent(g.name)}`);
                      setOutcome({ kind: "ok", message: `Removed ${g.dis}.` });
                      await list.reload();
                    } catch (e) { setOutcome(outcomeFrom(e)); }
                  }}>Remove</ActionButton>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="actions">
        <ActionButton onClick={() => { setOutcome(null); setEditing({ name: null, text: JSON.stringify(NEW, null, 2) }); }}>
          Add a graphic
        </ActionButton>
        <ActionButton onClick={async () => {
          if (list.data?.source === "setup" &&
              !confirm("Make the starting graphics again? This replaces every saved graphic, including your changes.")) return;
          try {
            const r = await post("/api/setup/graphics/generate", {});
            setOutcome({ kind: "ok", message: "Made the starting graphics from the rooms and equipment; they are live.",
                         issues: r.warnings });
            await list.reload();
          } catch (e) { setOutcome(outcomeFrom(e)); }
        }}>Make starting graphics again</ActionButton>
      </div>
      <OutcomeBox outcome={outcome} />
      {editing && (
        <div className="form">
          <div className="form-row">
            <label htmlFor="graphic-json">{editing.name ? `Graphic ${editing.name} (JSON)` : "New graphic (JSON)"}</label>
            <textarea id="graphic-json" className="json" spellCheck={false} value={editing.text}
                      onChange={(e) => setEditing({ ...editing, text: e.target.value })} />
          </div>
          <p className="muted small">
            Items: symbol, value, pipe, duct, room and text. Points are bound by name; the setup checks run on Save.
          </p>
          <div className="actions">
            <ActionButton onClick={() => save(true)}>Check</ActionButton>
            <ActionButton primary onClick={() => save(false)}>Save</ActionButton>
            <button type="button" className="btn" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}
