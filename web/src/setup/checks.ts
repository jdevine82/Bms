// Commissioning checks (main spec 5.1.1 step 3 and the mapping table): points with no room,
// untagged points, duplicate names and points not communicating. Errors block go-live;
// warnings can be accepted.
import type { Issue, Obj, SiteConfig } from "./api";

export type Flag = "noRoom" | "untagged" | "duplicate" | "notCommunicating" | "noLimits";

/** A setpoint or command that can be written (milestone 7A), as the backend decides. */
export function writable(p: Obj): boolean {
  if (p.type !== "sp" && p.type !== "cmd") return false;
  const a = p.address ?? {};
  if (a.topic !== undefined) return !!a.commandTopic;
  if (a.instance !== undefined) return !String(a.objectType).endsWith("Input");
  return a.objectType === "coil" || a.objectType === "holdingRegister";
}

export function roomOf(config: SiteConfig, point: Obj): string | null {
  if (point.space) return point.space;
  const equip = config.equips.find((e) => e.name === point.equip);
  return equip?.space ?? null;
}

export function pointFlags(config: SiteConfig, statuses: Record<string, string>): Record<string, Set<Flag>> {
  const counts = new Map<string, number>();
  for (const p of config.points) counts.set(p.name, (counts.get(p.name) ?? 0) + 1);
  const out: Record<string, Set<Flag>> = {};
  config.points.forEach((p) => {
    const flags = out[p.name] ?? new Set<Flag>();
    if (!roomOf(config, p)) flags.add("noRoom");
    if (!Object.keys(p.tags ?? {}).length) flags.add("untagged");
    if ((counts.get(p.name) ?? 0) > 1) flags.add("duplicate");
    const s = statuses[p.name];
    if (s && s !== "ok" && s !== "stale") flags.add("notCommunicating");
    if (writable(p) && p.kind === "Number" && !p.writeLimits) flags.add("noLimits");
    out[p.name] = flags;
  });
  return out;
}

export interface Verdict {
  errors: string[];
  warnings: string[];
}

/** Checks on the points in `scope` (all points when not given), plus the backend's own checks. */
export function verdict(config: SiteConfig, statuses: Record<string, string>, server: Issue[], scope?: string[]): Verdict {
  const flags = pointFlags(config, statuses);
  const names = new Set(scope ?? config.points.map((p) => p.name));
  const count = (f: Flag) => [...names].filter((n) => flags[n]?.has(f)).length;
  const errors: string[] = [];
  const warnings: string[] = [];
  const dup = [...new Set(config.points.filter((p) => names.has(p.name) && flags[p.name]?.has("duplicate")).map((p) => p.name))];
  if (dup.length) errors.push(`${dup.length} point name${dup.length > 1 ? "s are" : " is"} used twice: ${dup.slice(0, 5).join(", ")}. Rename one.`);
  const noLimits = [...names].filter((n) => flags[n]?.has("noLimits"));
  if (noLimits.length) errors.push(`${noLimits.length} writable point${noLimits.length > 1 ? "s have" : " has"} no write limits: ${noLimits.slice(0, 5).join(", ")}.`);
  for (const i of server.filter((x) => x.severity === "error")) errors.push(`${i.where}: ${i.message}`);
  const noRoom = count("noRoom");
  if (noRoom) warnings.push(`${noRoom} point${noRoom > 1 ? "s have" : " has"} no room.`);
  const untagged = count("untagged");
  if (untagged) warnings.push(`${untagged} point${untagged > 1 ? "s have" : " has"} no tags.`);
  const down = count("notCommunicating");
  if (down) warnings.push(`${down} point${down > 1 ? "s are" : " is"} not communicating.`);
  for (const i of server.filter((x) => x.severity === "warning")) warnings.push(`${i.where}: ${i.message}`);
  return { errors, warnings };
}
