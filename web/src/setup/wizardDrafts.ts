// Commissioning wizard drafts (main spec 5.1.1: each step can be saved and resumed, and nothing
// goes live until step 3). A draft is kept in this browser; the setup it builds is a whole
// config document that the backend checks at each step but saves only at Go live.
import type { Obj, SiteConfig } from "./api";

export interface WizardDraft {
  id: string;
  title: string;
  step: 1 | 2 | 3 | 4;
  created: string;
  updated: string;
  device: Obj; // the device being added (or an existing one, when `existingDevice`)
  existingDevice: boolean;
  bus: Obj | null; // a new bus added with the device, if any
  deviceTemplate: string | null;
  equipTemplate: string | null; // equipment or room template; null = points from the device template only
  create: Obj; // the create form: name pattern, display pattern, repeat, room, keys
  base: SiteConfig | null; // the setup when step 2 ran, and its revision
  revision: string | null;
  config: SiteConfig | null; // the draft setup after step 2 (with in-table edits)
  newPoints: string[]; // the points this draft adds
  tested: Record<string, string>; // point -> status from the last test read
  placement: Record<string, "room" | "site" | "skip">; // step 4
  live: boolean; // went live
}

const KEY = "bms-commission-drafts";

function readAll(): WizardDraft[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as WizardDraft[]) : [];
  } catch {
    return [];
  }
}

function writeAll(drafts: WizardDraft[]): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify(drafts));
    return true;
  } catch {
    return false; // storage full or blocked: the draft lives only until the page closes
  }
}

export const listDrafts = () => readAll().sort((a, b) => b.updated.localeCompare(a.updated));
export const getDraft = (id: string) => readAll().find((d) => d.id === id) ?? null;

export function saveDraft(draft: WizardDraft): boolean {
  const all = readAll().filter((d) => d.id !== draft.id);
  return writeAll([...all, { ...draft, updated: new Date().toISOString() }]);
}

export function removeDraft(id: string) {
  writeAll(readAll().filter((d) => d.id !== id));
}

export function newDraft(device?: Obj, deviceTemplate: string | null = null, existing = false): WizardDraft {
  const now = new Date().toISOString();
  return {
    id: Math.random().toString(36).slice(2, 10),
    title: device?.dis || device?.name || "New device",
    step: 1,
    created: now,
    updated: now,
    device: device ?? { name: "", dis: "", protocol: "modbus_tcp", connection: { host: "", port: 502, unitId: 1 } },
    existingDevice: existing,
    bus: null,
    deviceTemplate,
    equipTemplate: null,
    create: { name: "", dis: "", space: "", repeat: false, from: 1, to: 1, step: 0, namePrefix: "" },
    base: null,
    revision: null,
    config: null,
    newPoints: [],
    tested: {},
    placement: {},
    live: false,
  };
}

/** Start a draft from a device found by Discover; the wizard opens it next. */
export function startDraftFromDevice(device: Obj, template: string | null) {
  const d = newDraft(device, template);
  saveDraft(d);
  try {
    sessionStorage.setItem("bms-commission-open", d.id);
  } catch {
    /* the wizard then shows the draft list */
  }
}
