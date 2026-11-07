// Diagram helpers shared by the editor and the import dialog: wire IDs, automatic layout for
// blocks without positions, selections as fragments (the import and export format), and
// merging a fragment into a diagram with clashing IDs renumbered (main spec 5.3.2).
import type { Block, Diagram, VariableDef, Wire } from "./logicApi";
import { SCHEMA } from "./logicApi";

export const wireId = (w: Wire) => `${w.from.block}.${w.from.port}->${w.to.block}.${w.to.port}`;

export interface Fragment {
  schema?: string;
  blocks: Block[];
  wires?: Wire[];
  variables?: VariableDef[];
  [key: string]: unknown;
}

/** Lay out blocks that have no position: columns by depth from the inputs, rows in order. */
export function autoLayout<T extends { blocks: Block[]; wires?: Wire[] }>(d: T, origin = { x: 0, y: 0 }): T {
  if (d.blocks.every((b) => b.position)) return d;
  const wires = d.wires ?? [];
  const depth = new Map<string, number>();
  const ids = new Set(d.blocks.map((b) => b.id));
  const visit = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0; // a loop
    seen.add(id);
    const feeders = wires.filter((w) => w.to.block === id && ids.has(w.from.block)).map((w) => w.from.block);
    const value = feeders.length ? Math.max(...feeders.map((f) => visit(f, seen) + 1)) : 0;
    depth.set(id, value);
    return value;
  };
  const rows = new Map<number, number>();
  const placedMaxY = Math.max(origin.y, ...d.blocks.filter((b) => b.position).map((b) => b.position!.y + 160));
  const blocks = d.blocks.map((b) => {
    if (b.position) return b;
    const col = visit(b.id, new Set());
    const row = rows.get(col) ?? 0;
    rows.set(col, row + 1);
    const base = d.blocks.some((x) => x.position) ? placedMaxY : origin.y;
    return { ...b, position: { x: origin.x + col * 280, y: base + row * 160 } };
  });
  return { ...d, blocks };
}

/** The selected blocks with the wires between them, in the import format. */
export function fragmentOf(d: Diagram, ids: Set<string>): Fragment {
  const blocks = d.blocks.filter((b) => ids.has(b.id));
  const wires = d.wires.filter((w) => ids.has(w.from.block) && ids.has(w.to.block));
  const used = new Set(blocks.map((b) => b.settings?.variable).filter(Boolean));
  const variables = d.variables.filter((v) => used.has(v.name));
  return { schema: SCHEMA, blocks, wires, ...(variables.length ? { variables } : {}) };
}

/** Add a fragment (or a whole diagram's blocks) to a diagram near `at`: block IDs that clash are
 * renumbered and the wires follow; variables it brings that the diagram lacks are added. */
export function mergeFragment(d: Diagram, raw: unknown, at: { x: number; y: number }): { diagram: Diagram; added: string[] } {
  if (!raw || typeof raw !== "object" || !Array.isArray((raw as Fragment).blocks)) {
    throw new Error("this is not a set of logic blocks");
  }
  const frag = autoLayout(raw as Fragment, at);
  if (frag.schema && frag.schema !== SCHEMA) throw new Error(`this is ${frag.schema}, not ${SCHEMA}`);
  const used = new Set(d.blocks.map((b) => b.id));
  const rename = new Map<string, string>();
  for (const b of frag.blocks) {
    let id = b.id;
    if (used.has(id)) {
      const base = id.replace(/_\d+$/, "");
      let n = 2;
      while (used.has(`${base}_${n}`)) n++;
      id = `${base}_${n}`;
    }
    used.add(id);
    rename.set(b.id, id);
  }
  const xs = frag.blocks.map((b) => b.position!.x);
  const ys = frag.blocks.map((b) => b.position!.y);
  const dx = at.x - Math.min(...xs) + 40;
  const dy = at.y - Math.min(...ys) + 40;
  const blocks = frag.blocks.map((b) => ({ ...b, id: rename.get(b.id)!,
                                            position: { x: Math.round((b.position!.x + dx) / 20) * 20, y: Math.round((b.position!.y + dy) / 20) * 20 } }));
  const wires = (frag.wires ?? []).filter((w) => rename.has(w.from.block) && rename.has(w.to.block)).map((w) => ({
    from: { block: rename.get(w.from.block)!, port: w.from.port }, to: { block: rename.get(w.to.block)!, port: w.to.port } }));
  const have = new Set(d.variables.map((v) => v.name));
  const variables = [...d.variables, ...(frag.variables ?? []).filter((v) => !have.has(v.name))];
  return { diagram: { ...d, blocks: [...d.blocks, ...blocks], wires: [...d.wires, ...wires], variables }, added: blocks.map((b) => b.id) };
}
