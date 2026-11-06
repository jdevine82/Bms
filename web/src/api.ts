// Types and calls for the backend API (milestone 2 spec section 7).

export type Status = "ok" | "stale" | "fault" | "down" | "disabled" | "unknown";

export interface LiveValue {
  id: string;
  value: number | boolean | string | null; // null unless status is "ok"
  lastValue: number | boolean | string | null;
  status: Status;
  reason: string | null;
  ts: string;
  lastGoodTs: string | null;
}

export interface Ref {
  id: string;
  name: string;
  dis: string;
}

export interface Point {
  id: string;
  name: string;
  dis: string;
  equip: Ref | null;
  space: Ref | null;
  type: "sensor" | "sp" | "cmd";
  kind: "Number" | "Bool" | "Str";
  unit: string | null;
  enum: string[] | null;
  tags: Record<string, boolean | string | number>;
  his: boolean;
  pollClass: string;
  cur: LiveValue;
  writable?: boolean; // milestone 7A: can be commanded
  writeLimits?: { min: number; max: number } | null;
  override?: Override | null;
}

export interface Override {
  value: number | boolean | string;
  setBy: string;
  setAt: string;
  endsAt: string;
}

export interface Site {
  id: string;
  name: string;
  dis: string;
  tz: string;
  ianaTz: string;
  equips?: { id: string; name: string; dis: string; tags: Record<string, unknown> }[];
}

export interface HisRow {
  ts: string;
  value: number | string | null;
  status: Status;
  reason: string | null;
}

export interface History {
  id: string;
  name: string;
  unit: string | null;
  thinned: boolean;
  rowCount: number;
  rows: HisRow[];
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} from ${url}`);
  }
  return (await response.json()) as T;
}

export const getSite = () => getJson<Site>("/api/site");
export const getPoints = () => getJson<Point[]>("/api/points");

export function historyQuery(start: Date, end: Date): string {
  return new URLSearchParams({ start: start.toISOString(), end: end.toISOString() }).toString();
}

export const getHistory = (id: string, start: Date, end: Date) =>
  getJson<History>(`/api/points/${id}/history?${historyQuery(start, end)}`);

export const historyCsvUrl = (id: string, start: Date, end: Date) =>
  `/api/points/${id}/history.csv?${historyQuery(start, end)}`;
