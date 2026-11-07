// The block palette (main spec 5.3.1 Layout): searchable groups; drag a block onto the canvas or
// click it to add it in the middle.
import { useState } from "react";
import { BLOCKS, GROUPS } from "./blocks";
import type { Snippet } from "./logicApi";

export function Palette({ onAdd, snippets = [], onSnippet, onRemoveSnippet }: {
  onAdd: (type: string) => void;
  snippets?: Snippet[];
  onSnippet?: (s: Snippet) => void;
  onRemoveSnippet?: (s: Snippet) => void;
}) {
  const [q, setQ] = useState("");
  const match = (type: string) => {
    const s = q.trim().toLowerCase();
    return !s || BLOCKS[type].dis.toLowerCase().includes(s) || type.includes(s) || BLOCKS[type].help.toLowerCase().includes(s);
  };
  return (
    <aside className="logic-palette" aria-label="Blocks">
      <input type="search" placeholder="Search blocks" aria-label="Search blocks" value={q} onChange={(e) => setQ(e.target.value)} />
      {GROUPS.map((g) => {
        const types = Object.keys(BLOCKS).filter((t) => BLOCKS[t].group === g && match(t));
        if (!types.length) return null;
        return (
          <section key={g} aria-label={g}>
            <h3>{g}</h3>
            {types.map((t) => (
              <button key={t} type="button" draggable title={BLOCKS[t].help} aria-label={`Add ${BLOCKS[t].dis}`}
                      onDragStart={(e) => { e.dataTransfer.setData("application/x-bms-block", t); e.dataTransfer.effectAllowed = "copy"; }}
                      onClick={() => onAdd(t)}>
                {BLOCKS[t].dis}
              </button>
            ))}
          </section>
        );
      })}
      {snippets.filter((sn) => !q.trim() || sn.dis.toLowerCase().includes(q.trim().toLowerCase())).length > 0 && (
        <section aria-label="Snippets">
          <h3>Snippets</h3>
          {snippets.filter((sn) => !q.trim() || sn.dis.toLowerCase().includes(q.trim().toLowerCase())).map((sn) => (
            <div key={sn.name} className="snippet-row">
              <button type="button" aria-label={`Insert snippet ${sn.dis}`} title={`${sn.fragment.blocks.length} blocks`}
                      onClick={() => onSnippet?.(sn)}>{sn.dis}</button>
              {onRemoveSnippet && <button type="button" className="snippet-remove" aria-label={`Remove snippet ${sn.dis}`}
                                          onClick={() => onRemoveSnippet(sn)}>×</button>}
            </div>
          ))}
        </section>
      )}
    </aside>
  );
}
