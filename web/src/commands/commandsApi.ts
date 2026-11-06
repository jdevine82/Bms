// Commands, overrides and the HMI PIN (milestone 7A, main spec 5.6 and 7).
import type { Override } from "../api";
import { get, post, put } from "../setup/api";

export interface CommandResult {
  ok: boolean;
  point: string;
  value: number | boolean | string;
  mode: "set" | "hand";
  override: Override | null;
  readBack: { status: string; value: unknown; error: string | null } | null;
}

export const sendCommand = (pointId: string, body: { value: unknown; mode: "set" | "hand"; hours?: number; confirm?: boolean }) =>
  post<CommandResult>(`/api/points/${pointId}/command`, body);
export const releaseToAuto = (pointId: string) => post<{ ok: boolean }>(`/api/points/${pointId}/release`, {});
export const listOverrides = () => get<(Override & { pointId: string; point: string; dis: string })[]>("/api/overrides");
export const getCommandSettings = () => get<{ choicesH: number[]; defaultH: number }>("/api/command-settings");
export const putCommandSettings = (s: { choicesH: number[]; defaultH: number }) => put<{ choicesH: number[]; defaultH: number }>("/api/command-settings", s);

export const pinUsers = () => get<{ username: string; dis: string }[]>("/api/auth/pin-users");
export const pinLogin = (username: string, pin: string) => post<{ ok: boolean }>("/api/auth/pin", { username, pin });
export const setOwnPin = (pin: string, password: string) => post<{ ok: boolean }>("/api/auth/me/pin", { pin, password });
export const removeOwnPin = () => post<{ ok: boolean }>("/api/auth/me/pin/remove", {});
export const removeUserPin = (username: string) => post<{ ok: boolean }>(`/api/users/${encodeURIComponent(username)}/pin/remove`, {});

export function hoursText(h: number): string {
  if (h < 1) return `${Math.round(h * 60)} min`;
  return `${h} h`;
}
