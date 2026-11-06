// Schedules and holiday calendars (milestone 7B, main spec 5.4).
import { del, get, post, put } from "../setup/api";

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export const DAY_TEXT: Record<string, string> = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };

export interface Period { start: string; end: string }
export interface OneOff { start: string; end: string; occupied: boolean; dis?: string }
export interface Controller { device: string; instance: number; valueType: "enumerated" | "unsigned" | "real" | "boolean"; occupied: number | boolean; unoccupied: number | boolean }
export interface ControllerState { pushedAt?: string; pushedBy?: string; checkedAt?: string; inSync?: boolean; differences?: string[] }

export interface Schedule {
  name: string;
  dis: string;
  enabled: boolean;
  weekly: Partial<Record<(typeof DAYS)[number], Period[]>>;
  calendars: string[];
  exceptions: OneOff[];
  equips: string[];
  tags: string[];
  controller: Controller | null;
  now?: { occupied: boolean; why: string };
  next?: { at: string; occupied: boolean } | null;
  equipsResolved?: string[];
  points?: { name: string; dis: string; occupancy: { occupied: unknown; unoccupied: unknown } }[];
  controllerState?: ControllerState | null;
}

export interface Calendar { name: string; dis: string; dates: { start: string; end?: string | null; dis?: string }[] }

export const listSchedules = () => get<Schedule[]>("/api/schedules");
export const addSchedule = (s: Schedule) => post<Schedule>("/api/schedules", s);
export const saveSchedule = (s: Schedule) => put<Schedule>(`/api/schedules/${s.name}`, s);
export const removeSchedule = (name: string) => del<{ ok: boolean }>(`/api/schedules/${name}`);
export const pushController = (name: string) => post<ControllerState>(`/api/schedules/${name}/controller/push`, {});
export const checkController = (name: string) => post<ControllerState>(`/api/schedules/${name}/controller/check`, {});
export const acceptController = (name: string) => post<Schedule>(`/api/schedules/${name}/controller/accept`, {});
export const listCalendars = () => get<Calendar[]>("/api/holiday-calendars");
export const saveCalendar = (c: Calendar) => put<Calendar>(`/api/holiday-calendars/${c.name}`, c);
export const removeCalendar = (name: string) => del<{ ok: boolean }>(`/api/holiday-calendars/${name}`);

/** "Occupied until 18:00" or "Unoccupied until Mon 07:00". */
export function stateText(s: Schedule): string {
  if (!s.now) return "";
  const state = s.now.occupied ? "Occupied" : "Unoccupied";
  if (!s.next) return state;
  const at = new Date(s.next.at); // a local wall time: shown as written
  const sameDay = s.next.at.slice(0, 10) === new Date().toISOString().slice(0, 10);
  const day = at.toLocaleDateString([], { weekday: "short" });
  return `${state} until ${sameDay ? "" : `${day} `}${s.next.at.slice(11, 16)}`;
}
