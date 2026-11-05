// The room mapping table as a spreadsheet (CSV), so a site can be set up in bulk offline
// (main spec 5.1.1, other commissioning tools).
import type { Obj, SiteConfig } from "./api";
import { parseCsv, slug, tagsToText, textToTags, toCsv } from "./text";

export const CSV_COLUMNS = ["name", "dis", "equip", "room", "tags", "unit", "scale", "device"];

export function pointsToCsv(config: SiteConfig, names?: Set<string>): string {
  const rows = config.points
    .filter((p) => !names || names.has(p.name))
    .map((p) => [p.name, p.dis, p.equip ?? "", p.space ?? "", tagsToText(p.tags), p.unit ?? "", p.address?.dataType ? (p.address.scale ?? 1) : "", p.device]);
  return toCsv([CSV_COLUMNS, ...rows]);
}

export interface CsvImport {
  config: SiteConfig;
  updated: number;
  unknown: string[]; // names in the file with no such point
  roomsAdded: string[];
  problems: string[];
}

/** Apply a CSV to the points it names. Empty cells leave a field as it is; "-" clears room or unit. */
export function applyCsv(config: SiteConfig, text: string): CsvImport {
  const rows = parseCsv(text);
  const problems: string[] = [];
  if (!rows.length) return { config, updated: 0, unknown: [], roomsAdded: [], problems: ["The file is empty."] };
  const head = rows[0].map((h) => h.trim().toLowerCase());
  const col = (name: string, ...aliases: string[]) => [name, ...aliases].map((n) => head.indexOf(n)).find((i) => i >= 0) ?? -1;
  const at = {
    name: col("name", "point"),
    dis: col("dis", "display name"),
    equip: col("equip", "equipment"),
    room: col("room", "space"),
    tags: col("tags"),
    unit: col("unit", "units"),
    scale: col("scale"),
  };
  if (at.name < 0) return { config, updated: 0, unknown: [], roomsAdded: [], problems: ["The file needs a 'name' column."] };
  const spaces = config.spaces.slice();
  const roomsAdded: string[] = [];
  const findRoom = (value: string): string => {
    const v = value.trim();
    const hit = spaces.find((s) => s.name === v || s.dis.toLowerCase() === v.toLowerCase());
    if (hit) return hit.name;
    const name = slug(v);
    spaces.push({ name, dis: v, tags: {} });
    roomsAdded.push(v);
    return name;
  };
  const byName = new Map(config.points.map((p, i) => [p.name, i]));
  const points = config.points.slice();
  const equips = new Set(config.equips.map((e) => e.name));
  const unknown: string[] = [];
  let updated = 0;
  rows.slice(1).forEach((r, n) => {
    const cell = (i: number) => (i >= 0 ? (r[i] ?? "").trim() : "");
    const name = cell(at.name);
    const i = byName.get(name);
    if (i === undefined) {
      unknown.push(name);
      return;
    }
    const p: Obj = { ...points[i] };
    const before = JSON.stringify(p);
    if (cell(at.dis)) p.dis = cell(at.dis);
    if (cell(at.equip)) {
      if (equips.has(cell(at.equip))) p.equip = cell(at.equip);
      else problems.push(`row ${n + 2}: no equipment '${cell(at.equip)}'`);
    }
    if (cell(at.room) === "-") delete p.space;
    else if (cell(at.room)) p.space = findRoom(cell(at.room));
    if (at.tags >= 0 && cell(at.tags)) p.tags = textToTags(cell(at.tags));
    if (cell(at.unit) === "-") delete p.unit;
    else if (cell(at.unit)) p.unit = cell(at.unit);
    if (cell(at.scale) && p.address?.dataType) {
      const s = Number(cell(at.scale));
      if (Number.isFinite(s)) p.address = { ...p.address, scale: s };
      else problems.push(`row ${n + 2}: scale '${cell(at.scale)}' is not a number`);
    }
    if (JSON.stringify(p) !== before) {
      points[i] = p;
      updated++;
    }
  });
  return { config: { ...config, spaces, points }, updated, unknown, roomsAdded, problems };
}
