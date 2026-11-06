// One editable table of points: equipment, room, name, display name, tags, unit and scaling
// (main spec 5.1.1 step 2 and the room mapping table). Edits change a working copy of the
// setup; the page that owns it saves it.
import { useMemo, useState } from "react";
import type { Obj, SiteConfig } from "./api";
import type { Flag } from "./checks";
import { pointFlags, roomOf } from "./checks";
import { NAME_PATTERN, slug, tagsToText, textToTags } from "./text";
import { Badge } from "./ui";

const FILTERS: { key: Flag | "all"; label: string; tone: "info" | "warn" | "bad" | "muted" }[] = [
  { key: "all", label: "All", tone: "info" },
  { key: "noRoom", label: "No room", tone: "warn" },
  { key: "untagged", label: "Untagged", tone: "warn" },
  { key: "duplicate", label: "Duplicate names", tone: "bad" },
  { key: "notCommunicating", label: "Not communicating", tone: "muted" },
  { key: "noLimits", label: "No write limits", tone: "bad" },
];

/** One side of a point's occupancy map (milestone 7B); clearing either side removes the map. */
function OccupancyInput({ point, which, onChange }: { point: Obj; which: "occupied" | "unoccupied"; onChange: (occ: Obj | undefined) => void }) {
  const occ = point.occupancy ?? {};
  const other = which === "occupied" ? "unoccupied" : "occupied";
  const label = `${which === "occupied" ? "Occupied" : "Unoccupied"} value of ${point.name}`;
  const update = (v: unknown) => {
    if (v === "" || v === undefined) return onChange(undefined);
    const next = { ...occ, [which]: v };
    if (next[other] === undefined) next[other] = point.kind === "Bool" ? !v : v;
    onChange(next);
  };
  const value = occ[which];
  if (point.kind === "Bool" || point.kind === "Str") {
    const options = point.kind === "Bool" ? [["true", "On"], ["false", "Off"]] : (point.enum ?? []).map((e: string) => [e, e]);
    return (
      <select aria-label={label} value={value === undefined ? "" : String(value)}
        onChange={(e) => update(e.target.value === "" ? "" : point.kind === "Bool" ? e.target.value === "true" : e.target.value)}>
        <option value="">–</option>
        {options.map(([v, t]: string[]) => <option key={v} value={v}>{t}</option>)}
      </select>
    );
  }
  return <input className="narrow" type="number" step="any" aria-label={label} value={value ?? ""} onChange={(e) => update(e.target.value === "" ? "" : Number(e.target.value))} />;
}

function limitsFrom(min: unknown, max: unknown): Obj | undefined {
  const lo = min === "" || min == null ? null : Number(min);
  const hi = max === "" || max == null ? null : Number(max);
  if (lo === null && hi === null) return undefined;
  return { min: lo ?? 0, max: hi ?? 0 };
}

export function PointGrid({
  config,
  onChange,
  scope,
  statuses,
  canRename,
  showDevice = true,
}: {
  config: SiteConfig;
  onChange: (config: SiteConfig) => void;
  scope?: string[]; // only these points (the wizard's new points)
  statuses: Record<string, string>;
  canRename: (name: string) => boolean;
  showDevice?: boolean;
}) {
  const [filter, setFilter] = useState<Flag | "all">("all");
  const [search, setSearch] = useState("");
  const [deviceFilter, setDeviceFilter] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [bulkRoom, setBulkRoom] = useState("");
  const [bulkEquip, setBulkEquip] = useState("");
  const [bulkTags, setBulkTags] = useState("");
  const [newRoom, setNewRoom] = useState("");

  const flags = useMemo(() => pointFlags(config, statuses), [config, statuses]);
  const inScope = useMemo(() => (scope ? new Set(scope) : null), [scope]);
  const indexes = useMemo(
    () =>
      config.points
        .map((p, i) => ({ p, i }))
        .filter(({ p }) => !inScope || inScope.has(p.name))
        .filter(({ p }) => !deviceFilter || p.device === deviceFilter)
        .filter(({ p }) => filter === "all" || flags[p.name]?.has(filter))
        .filter(({ p }) => {
          if (!search) return true;
          const text = `${p.name} ${p.dis} ${p.equip ?? ""} ${roomOf(config, p) ?? ""} ${tagsToText(p.tags)}`.toLowerCase();
          return text.includes(search.toLowerCase());
        })
        .map(({ i }) => i),
    [config, inScope, deviceFilter, filter, flags, search],
  );
  const counts = useMemo(() => {
    const all = config.points.filter((p) => !inScope || inScope.has(p.name));
    const c: Record<string, number> = { all: all.length };
    for (const f of FILTERS) if (f.key !== "all") c[f.key] = all.filter((p) => flags[p.name]?.has(f.key as Flag)).length;
    return c;
  }, [config.points, inScope, flags]);

  const setPoint = (i: number, change: Obj) => {
    const points = config.points.slice();
    points[i] = { ...points[i], ...change };
    for (const k of Object.keys(change)) if (change[k] === undefined) delete points[i][k];
    onChange({ ...config, points });
  };

  const bulk = (fn: (p: Obj) => Obj) => {
    const points = config.points.map((p, i) => (picked.has(i) ? fn(p) : p));
    onChange({ ...config, points });
  };

  const spaces = config.spaces;
  const devices = [...new Set(config.points.filter((p) => !inScope || inScope.has(p.name)).map((p) => p.device))].sort();
  const allPicked = indexes.length > 0 && indexes.every((i) => picked.has(i));

  return (
    <div className="point-grid">
      <div className="chips" role="group" aria-label="Show">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`chip ${f.tone} ${filter === f.key ? "on" : ""}`}
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label} {counts[f.key] ?? 0}
          </button>
        ))}
        <input aria-label="Search points" placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} />
        {showDevice && devices.length > 1 && (
          <select aria-label="Device" value={deviceFilter} onChange={(e) => setDeviceFilter(e.target.value)}>
            <option value="">All devices</option>
            {devices.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        )}
      </div>
      <div className="table-scroll">
        <table className="grid editable" aria-label="Points">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="Select all shown"
                  checked={allPicked}
                  onChange={() => setPicked(allPicked ? new Set() : new Set(indexes))}
                />
              </th>
              <th>Point</th>
              <th>Display name</th>
              <th>Equipment</th>
              <th>Room</th>
              <th>Tags</th>
              <th>Unit</th>
              <th>Scale</th>
              <th>Write limits</th>
              <th>Schedule: occupied / unoccupied</th>
              {showDevice && <th>Device</th>}
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {indexes.slice(0, 1000).map((i) => {
              const p = config.points[i];
              const f = flags[p.name] ?? new Set();
              const equip = config.equips.find((e) => e.name === p.equip);
              const st = statuses[p.name];
              const tone = f.has("duplicate") || f.has("noLimits") ? "row-bad" : f.size && !(f.size === 1 && f.has("notCommunicating")) ? "row-warn" : "";
              return (
                <tr key={i} className={tone} data-point={p.name}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${p.name}`}
                      checked={picked.has(i)}
                      onChange={() => {
                        const next = new Set(picked);
                        if (next.has(i)) next.delete(i);
                        else next.add(i);
                        setPicked(next);
                      }}
                    />
                  </td>
                  <td>
                    {canRename(p.name) ? (
                      <input
                        className={`mono ${NAME_PATTERN.test(p.name) ? "" : "invalid"}`}
                        aria-label={`Name of ${p.name}`}
                        value={p.name}
                        onChange={(e) => setPoint(i, { name: e.target.value })}
                      />
                    ) : (
                      <span className="mono" title="Rename a saved point from its own page, so its history and old name are kept">
                        {p.name}
                      </span>
                    )}
                  </td>
                  <td>
                    <input aria-label={`Display name of ${p.name}`} value={p.dis ?? ""} onChange={(e) => setPoint(i, { dis: e.target.value })} />
                  </td>
                  <td>
                    <select
                      aria-label={`Equipment of ${p.name}`}
                      value={p.equip ?? ""}
                      onChange={(e) => setPoint(i, { equip: e.target.value || undefined })}
                    >
                      <option value="">(none)</option>
                      {config.equips.map((e) => (
                        <option key={e.name} value={e.name}>
                          {e.dis} ({e.name})
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      aria-label={`Room of ${p.name}`}
                      className={f.has("noRoom") ? "warn-text" : ""}
                      value={p.space ?? ""}
                      onChange={(e) => setPoint(i, { space: e.target.value || undefined })}
                    >
                      <option value="">
                        {equip?.space ? `From equipment: ${spaces.find((s) => s.name === equip.space)?.dis ?? equip.space}` : "No room"}
                      </option>
                      {spaces.map((s) => (
                        <option key={s.name} value={s.name}>
                          {s.dis}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      key={`${i}-${tagsToText(p.tags)}`}
                      aria-label={`Tags of ${p.name}`}
                      defaultValue={tagsToText(p.tags)}
                      onBlur={(e) => setPoint(i, { tags: textToTags(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input className="narrow" aria-label={`Unit of ${p.name}`} value={p.unit ?? ""} onChange={(e) => setPoint(i, { unit: e.target.value || undefined })} />
                  </td>
                  <td>
                    {p.address?.dataType ? (
                      <input
                        className="narrow"
                        type="number"
                        step="any"
                        aria-label={`Scale of ${p.name}`}
                        value={p.address.scale ?? 1}
                        onChange={(e) => setPoint(i, { address: { ...p.address, scale: Number(e.target.value) } })}
                      />
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </td>
                  <td className="nowrap">
                    {(p.type === "sp" || p.type === "cmd") && p.kind === "Number" ? (
                      <span className={`limits ${f.has("noLimits") ? "missing" : ""}`}>
                        <input className="narrow" type="number" step="any" aria-label={`Lowest write of ${p.name}`} placeholder="min"
                          value={p.writeLimits?.min ?? ""} onChange={(e) => setPoint(i, { writeLimits: limitsFrom(e.target.value, p.writeLimits?.max) })} />
                        <input className="narrow" type="number" step="any" aria-label={`Highest write of ${p.name}`} placeholder="max"
                          value={p.writeLimits?.max ?? ""} onChange={(e) => setPoint(i, { writeLimits: limitsFrom(p.writeLimits?.min, e.target.value) })} />
                      </span>
                    ) : <span className="muted">–</span>}
                  </td>
                  <td className="nowrap">
                    {(p.type === "sp" || p.type === "cmd") ? (
                      <span className="limits">
                        <OccupancyInput point={p} which="occupied" onChange={(occ) => setPoint(i, { occupancy: occ })} />
                        <OccupancyInput point={p} which="unoccupied" onChange={(occ) => setPoint(i, { occupancy: occ })} />
                      </span>
                    ) : <span className="muted">–</span>}
                  </td>
                  {showDevice && <td className="mono small">{p.device}</td>}
                  <td>
                    {f.has("duplicate") ? (
                      <Badge tone="bad">Duplicate name</Badge>
                    ) : f.has("noLimits") ? (
                      <Badge tone="bad">No write limits</Badge>
                    ) : f.has("notCommunicating") ? (
                      <Badge tone="bad">{st}</Badge>
                    ) : f.has("noRoom") || f.has("untagged") ? (
                      <Badge tone="warn">Warning</Badge>
                    ) : st ? (
                      <Badge tone="ok">OK</Badge>
                    ) : (
                      <Badge tone="muted">Not read</Badge>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {indexes.length > 1000 && <p className="muted small">Showing the first 1000 of {indexes.length}; use the filters.</p>}
      </div>
      <div className="bulk" aria-label="Change selected">
        <span className="muted small">{picked.size} selected</span>
        <select aria-label="Room for selected" value={bulkRoom} onChange={(e) => setBulkRoom(e.target.value)}>
          <option value="">Room…</option>
          <option value="-">Clear (use the equipment's room)</option>
          {spaces.map((s) => (
            <option key={s.name} value={s.name}>
              {s.dis}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn"
          disabled={!picked.size || !bulkRoom}
          onClick={() => bulk((p) => {
            const q = { ...p };
            if (bulkRoom === "-") delete q.space;
            else q.space = bulkRoom;
            return q;
          })}
        >
          Set room for selected
        </button>
        <select aria-label="Equipment for selected" value={bulkEquip} onChange={(e) => setBulkEquip(e.target.value)}>
          <option value="">Equipment…</option>
          {config.equips.map((e) => (
            <option key={e.name} value={e.name}>
              {e.dis}
            </option>
          ))}
        </select>
        <button type="button" className="btn" disabled={!picked.size || !bulkEquip} onClick={() => bulk((p) => ({ ...p, equip: bulkEquip }))}>
          Move to equipment
        </button>
        <input aria-label="Tags to add" placeholder="tags to add" value={bulkTags} onChange={(e) => setBulkTags(e.target.value)} />
        <button
          type="button"
          className="btn"
          disabled={!picked.size || !bulkTags.trim()}
          onClick={() => bulk((p) => ({ ...p, tags: { ...(p.tags ?? {}), ...textToTags(bulkTags) } }))}
        >
          Add tags to selected
        </button>
        <span className="spacer" />
        <input aria-label="New room name" placeholder="New room, e.g. Meeting room" value={newRoom} onChange={(e) => setNewRoom(e.target.value)} />
        <button
          type="button"
          className="btn"
          disabled={!newRoom.trim() || spaces.some((s) => s.name === slug(newRoom))}
          onClick={() => {
            onChange({ ...config, spaces: [...config.spaces, { name: slug(newRoom), dis: newRoom.trim(), tags: {} }] });
            setNewRoom("");
          }}
        >
          Add room
        </button>
      </div>
    </div>
  );
}
