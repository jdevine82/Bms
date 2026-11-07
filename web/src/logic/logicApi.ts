// Control logic (main spec 5.3.1 and 5.3.2; milestone 10): the devine-bms-logic/1 format and the API.
import { del, get, post, put } from "../setup/api";

export const SCHEMA = "devine-bms-logic/1";
export type VType = "bool" | "number" | "text";
export type Value = boolean | number | string;

export interface Block {
  id: string;
  type: string;
  settings: Record<string, any>;
  position?: { x: number; y: number };
  label?: string;
}
export interface End { block: string; port: string }
export interface Wire { from: End; to: End }
export interface VariableDef {
  name: string;
  dis?: string;
  type: VType;
  unit?: string;
  kind: "constant" | "memory";
  value?: Value | null;
  min?: number | null;
  max?: number | null;
}
export interface Diagram {
  schema?: string;
  name: string;
  dis: string;
  scope: "site" | "equipment";
  equip?: string | null;
  description?: string;
  variables: VariableDef[];
  blocks: Block[];
  wires: Wire[];
}
export interface Issue { level: "error" | "warning"; message: string; block?: string }
export interface Summary {
  name: string;
  dis: string;
  scope: string;
  equip: string | null;
  enabled: boolean;
  publishedVersion: number | null;
  state: "draft" | "running" | "disabled" | "error" | "unknown";
  issues: Issue[];
  draftAt: string;
  draftBy: string | null;
  writes: string[];
}
export interface Detail extends Summary {
  draft: Diagram;
  published: Diagram | null;
  draftChanged: boolean;
  versions: { version: number; note: string | null; by: string | null; at: string }[];
}
export interface LiveBlock {
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  error?: string;
  bad?: boolean;
  remaining?: number;
  clamping?: string | null;
  mode?: string;
  next?: string | null;
}
export interface Live {
  name: string;
  state: string;
  issues: Issue[];
  version: number | null;
  at: string | null;
  blocks: Record<string, LiveBlock>;
  outputs: Record<string, { wanted: unknown; state: string; written: unknown; error: string | null; failsafe: any }>;
  variables: SiteVariable[];
}
export interface SiteVariable {
  scope: "site" | "diagram";
  owner: string | null;
  name: string;
  dis: string | null;
  type: VType;
  unit: string | null;
  kind: "constant" | "memory";
  value: Value | null;
  min: number | null;
  max: number | null;
}
export interface TestStep { t: number; outputs: Record<string, unknown>; values: Record<string, unknown>; errors: Record<string, string> }

const enc = encodeURIComponent;
export const listLogic = () => get<Summary[]>("/api/logic");
export const getLogic = (name: string) => get<Detail>(`/api/logic/${enc(name)}`);
export const getLive = (name: string) => get<Live>(`/api/logic/${enc(name)}/live`);
export const saveDraft = (name: string, d: Diagram) =>
  put<{ ok: boolean; created: boolean; valid: boolean; issues: Issue[] }>(`/api/logic/${enc(name)}/draft`, d);
export const checkDraft = (name: string, d: Diagram) => post<{ ok: boolean; valid: boolean; issues: Issue[] }>(`/api/logic/${enc(name)}/check`, d);
export const publish = (name: string, note: string) =>
  post<{ ok: boolean; version: number; issues: Issue[] }>(`/api/logic/${enc(name)}/publish`, { confirmFailsafe: true, note });
export const restore = (name: string, version: number) => post(`/api/logic/${enc(name)}/restore`, { version });
export const setEnabled = (name: string, enabled: boolean) => post(`/api/logic/${enc(name)}/enable`, { enabled });
export const deleteLogic = (name: string) => del(`/api/logic/${enc(name)}`);
export const testRun = (name: string, diagram: Diagram, inputs: Record<string, unknown>, schedules: Record<string, boolean>,
                        steps: number, dt: number) =>
  post<{ ok: boolean; valid: boolean; issues: Issue[]; steps?: TestStep[] }>(`/api/logic/${enc(name)}/test`, { diagram, inputs, schedules, steps, dt });
export const importLogic = (diagram: Diagram, name?: string, replace = false) =>
  post<{ ok: boolean; name: string; issues: Issue[] }>("/api/logic/import", { diagram, name, replace });
export const listVariables = () => get<SiteVariable[]>("/api/logic-variables");
export const putSiteVariable = (name: string, body: Partial<SiteVariable>) => put(`/api/logic-variables/${enc(name)}`, body);
export const setDiagramVariable = (diagram: string, name: string, value: Value) =>
  put(`/api/logic/${enc(diagram)}/variables/${enc(name)}`, { value });
