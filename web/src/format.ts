import type { LiveValue, Point } from "./api";

export const STATUS_TEXT: Record<string, string> = {
  ok: "Good",
  stale: "Stale",
  fault: "Bad (fault)",
  down: "Bad (offline)",
  disabled: "Disabled",
  unknown: "Waiting",
};

/** A value for display. Bad and Stale values are never shown as a live number (main spec 5.2.1). */
export function formatValue(point: Point, live: LiveValue): string {
  if (live.status !== "ok" || live.value === null) return "--";
  const unit = point.unit ? ` ${point.unit}` : "";
  if (typeof live.value === "number") {
    const digits = Math.abs(live.value) >= 1000 ? 1 : Math.abs(live.value) >= 10 ? 2 : 3;
    return `${live.value.toLocaleString(undefined, { maximumFractionDigits: digits })}${unit}`;
  }
  if (typeof live.value === "boolean") return live.value ? "On" : "Off";
  return `${live.value}${unit}`;
}

export function formatTime(iso: string | null, timeZone: string): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(iso));
}
