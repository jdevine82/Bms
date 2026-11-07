// The block palette (main spec 5.3.1 Layout): searchable groups; drag a block onto the canvas or
// click it to add it in the middle.
import { useState } from "react";
import { BLOCKS, GROUPS } from "./blocks";

export function Palette({ onAdd }: { onAdd: (type: string) => void }) {
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
    </aside>
  );
}
