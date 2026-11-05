// Tags, CSV and naming helpers shared by the setup screens.

export type Tags = Record<string, boolean | string | number>;

/** "temp, room, sensor, zone=north" — markers by name, other values as name=value. */
export function tagsToText(tags: Tags | undefined | null): string {
  return Object.entries(tags ?? {})
    .map(([k, v]) => (v === true ? k : `${k}=${v}`))
    .join(", ");
}

export function textToTags(text: string): Tags {
  const out: Tags = {};
  for (const part of text.split(/[,;]/)) {
    const item = part.trim();
    if (!item) continue;
    const eq = item.indexOf("=");
    if (eq < 0) {
      out[item] = true;
    } else {
      const key = item.slice(0, eq).trim();
      const raw = item.slice(eq + 1).trim();
      out[key] = raw !== "" && !Number.isNaN(Number(raw)) ? Number(raw) : raw;
    }
  }
  return out;
}

/** Parse CSV (RFC 4180: quotes, doubled quotes, commas and newlines inside quotes). */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const src = text.replace(/^﻿/, "");
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"' && src[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && src[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

export function toCsv(rows: (string | number | null | undefined)[][]): string {
  const cell = (v: string | number | null | undefined) => {
    const s = v == null ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return rows.map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
}

/** A config name from a display name: "Meeting room" -> "meeting_room". */
export function slug(text: string): string {
  const s = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return /^[a-z]/.test(s) ? s : `r_${s}`;
}

export const NAME_PATTERN = /^[a-z][a-z0-9_]*$/;

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
