// Calls for the setup API (milestone 5A backend, used by the 5B screens).
// Reading is open; changes are accepted only from the server itself until logins exist (milestone 6).

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Obj = Record<string, any>; // a config item, as in the config file (devine-bms-config/0)

export interface Issue {
  severity: "error" | "warning";
  where: string;
  message: string;
}

export interface ChangeResult {
  ok: boolean;
  applied: boolean;
  warnings: Issue[];
  report?: { created: Record<string, number>; updated: Record<string, number>; disabled: Record<string, number> };
  config?: SiteConfig; // a change made on a draft returns the changed draft
  [key: string]: any;
}

export interface SiteConfig {
  schema: string;
  site: Obj;
  bacnet?: Obj;
  equipTemplates?: Obj[];
  spaces: Obj[];
  equips: Obj[];
  feeds: Obj[];
  buses?: Obj[];
  devices: Obj[];
  points: Obj[];
  [key: string]: any;
}

/** A refused change: the message and the failed checks. */
export class SetupError extends Error {
  status: number;
  issues: Issue[];
  code: string | null = null;
  constructor(message: string, status: number, issues: Issue[] = []) {
    super(message);
    this.status = status;
    this.issues = issues;
  }
}

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = null;
  try {
    data = await response.json();
  } catch {
    /* not JSON */
  }
  if (!response.ok || (data && data.ok === false)) {
    const message =
      (data && (data.error || (typeof data.detail === "string" ? data.detail : null))) ||
      (response.status === 403 ? "Your role does not allow this." : `${response.status} ${response.statusText}`);
    const error = new SetupError(message, response.status, (data && data.issues) || []);
    error.code = data?.code ?? null;
    throw error;
  }
  return data as T;
}

export const get = <T>(url: string) => send<T>("GET", url);
export const post = <T = ChangeResult>(url: string, body: unknown) => send<T>("POST", url, body);
export const put = <T = ChangeResult>(url: string, body: unknown) => send<T>("PUT", url, body);
export const del = <T = ChangeResult>(url: string) => send<T>("DELETE", url);

const q = (check?: boolean) => (check ? "?check=true" : "");
const enc = encodeURIComponent;

/** The whole setup and its revision (sent back when saving a whole edited setup). */
export async function getConfig(): Promise<{ config: SiteConfig; revision: string }> {
  const response = await fetch("/api/setup/config");
  if (!response.ok) throw new SetupError(`${response.status} ${response.statusText}`, response.status);
  return { config: (await response.json()) as SiteConfig, revision: response.headers.get("X-Setup-Revision") ?? "" };
}

export const commit = (config: SiteConfig, revision: string, summary: string, check = false) =>
  post(`/api/setup/commit${q(check)}`, { config, revision, summary });

export type Kind = "spaces" | "equips" | "buses" | "devices" | "points";
export const listItems = (kind: Kind) => get<Obj[]>(`/api/setup/${kind}`);
export const putItem = (kind: Kind, name: string, item: Obj, check = false) =>
  put(`/api/setup/${kind}/${enc(name)}${q(check)}`, item);
export const deleteItem = (kind: Kind, name: string) => del(`/api/setup/${kind}/${enc(name)}`);
export const renameItem = (kind: Kind, name: string, newName: string) =>
  post(`/api/setup/${kind}/${enc(name)}/rename`, { newName });

export interface ChangeEntry {
  ts: string;
  client: string;
  action: string;
  kind: string | null;
  name: string | null;
  summary: string;
  before: any;
  after: any;
}
export const getChanges = (limit = 200) => get<ChangeEntry[]>(`/api/setup/changes?limit=${limit}`);

// ---------- device templates ----------

export interface TemplateSummary {
  id: string;
  version: string;
  vendor: string;
  model: string;
  dis: string;
  protocols: string[];
  status?: string;
  source: "builtin" | "site";
  points: number;
  [key: string]: any;
}
export const listTemplates = () => get<TemplateSummary[]>("/api/setup/templates");
export const getTemplate = (id: string, version?: string) =>
  get<{ source: string; versions: string[]; template: Obj }>(
    `/api/setup/templates/${enc(id)}${version ? `?version=${enc(version)}` : ""}`,
  );
export const saveTemplate = (template: Obj) => post("/api/setup/templates", template);
export const deleteTemplate = (id: string, version?: string) =>
  del(`/api/setup/templates/${enc(id)}${version ? `?version=${enc(version)}` : ""}`);
export const applyTemplate = (id: string, body: Obj, check = false) =>
  post(`/api/setup/templates/${enc(id)}/apply${q(check)}`, body);

// ---------- equipment templates ----------

export interface EquipTemplateSummary {
  id: string;
  version: string;
  dis: string;
  appliesTo: "equip" | "space";
  source: "builtin" | "site";
  versions: string[];
  instances: number;
  [key: string]: any;
}
export const listEquipTemplates = () => get<EquipTemplateSummary[]>("/api/setup/equip-templates");
export const getEquipTemplate = (id: string, version?: string) =>
  get<Obj>(`/api/setup/equip-templates/${enc(id)}${version ? `?version=${enc(version)}` : ""}`);
export const diffEquipTemplate = (id: string, from: string, to: string) =>
  get<Obj>(`/api/setup/equip-templates/${enc(id)}/diff?from=${enc(from)}&to=${enc(to)}`);
export const saveEquipTemplate = (template: Obj, check = false) => post(`/api/setup/equip-templates${q(check)}`, template);
export const deleteEquipTemplate = (id: string, version?: string) =>
  del(`/api/setup/equip-templates/${enc(id)}${version ? `?version=${enc(version)}` : ""}`);
export const createFromTemplate = (id: string, body: Obj, check = false) =>
  post(`/api/setup/equip-templates/${enc(id)}/create${q(check)}`, body);
export const rollout = (id: string, body: Obj, check = false) =>
  post(`/api/setup/equip-templates/${enc(id)}/rollout${q(check)}`, body);
export const getInstance = (kind: "equips" | "spaces", name: string) =>
  get<Obj>(`/api/setup/instances/${kind}/${enc(name)}`);
export const resetOverride = (kind: "equips" | "spaces", name: string, role: string, field: string) =>
  post(`/api/setup/instances/${kind}/${enc(name)}/reset`, { role, field });
export const unlinkInstance = (kind: "equips" | "spaces", name: string) =>
  post(`/api/setup/instances/${kind}/${enc(name)}/unlink`, {});

// ---------- test reads ----------

export interface TestReadResult {
  ok: boolean;
  results: { point: string; status: string; value: any; unit: string | null; error: string | null }[];
  requests: { request: string; ms: number | null; error: string | null; frames: { direction: string; hex?: string; text?: string }[] }[];
}
export const testRead = (body: Obj) => post<TestReadResult>("/api/setup/test-read", body);

// ---------- Discover ----------

export interface Scan {
  id: string;
  kind: string;
  params: Obj;
  status: "running" | "done" | "stopped" | "failed";
  progress: { done: number; total: number };
  error: string | null;
  results: Obj[];
  started: number;
  found?: number;
}
export const startScan = (body: Obj) => post<Scan>("/api/setup/discover", body);
export const listScans = () => get<Scan[]>("/api/setup/discover");
export const getScan = (id: string) => get<Scan>(`/api/setup/discover/${enc(id)}`);
export const stopScan = (id: string) => del<Scan>(`/api/setup/discover/${enc(id)}`);
export const addFound = (id: string, picks: { row: number; name: string; dis?: string }[]) =>
  post<{ ok: boolean; added: Obj[]; failed: Obj[] }>(`/api/setup/discover/${enc(id)}/add`, { picks });

// ---------- live status (milestone 2 API) ----------

export interface Health {
  devices: { name: string; online: boolean; polls: number; failures: number }[];
  connections: { name: string; where: string; shared: boolean; online: boolean; trunkStats: Obj | null; devices: string[] }[];
  [key: string]: any;
}
export const getHealth = () => get<Health>("/api/health");

/** Save a file in the browser (exports). */
export function download(filename: string, text: string, type = "application/json") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Read a file the user picked (imports). */
export function readFile(file: File): Promise<string> {
  return file.text();
}
