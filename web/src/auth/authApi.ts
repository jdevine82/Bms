// Login, accounts and audit log calls (milestone 6A).
import { get, post, put, del } from "../setup/api";

export type Role = "viewer" | "operator" | "admin";

export interface Me {
  username: string | null;
  dis: string;
  role: Role;
  platform?: boolean;
  totp: boolean;
  kiosk?: boolean;
  totpSetupNeeded?: boolean;
  pin?: boolean;
  pinSession?: boolean;
}

export interface AuthState {
  firstRun: boolean;
  firstRunHere: boolean;
  setupCode?: boolean; // standalone first boot: the code on the Pi's screen is needed here (11a)
  user: Me | null;
}

export interface UserRow {
  username: string;
  dis: string;
  role: Role;
  platform: boolean;
  enabled: boolean;
  totp: boolean;
  pin?: boolean;
  locked: boolean;
  lockedUntil: string | null;
  lastLogin: string | null;
}

export interface Security {
  minPasswordLength: number;
  idleTimeoutMin: number;
  lockoutAttempts: number;
  lockoutMinutes: number;
  requireTotpForAdmins: boolean;
  kioskAddresses: string[];
  allowedNetworks: string[];
}

export interface AuditRow {
  ts: string;
  user: string | null;
  client: string;
  action: string;
  kind: string | null;
  name: string | null;
  summary: string;
  before: unknown;
  after: unknown;
}

export const authState = (touch = true) => get<AuthState>(`/api/auth/state${touch ? "" : "?touch=0"}`);
export const login = (username: string, password: string, code?: string) =>
  post<{ ok: boolean; user: Me }>("/api/auth/login", { username, password, code: code || null });
export const firstAdmin = (username: string, dis: string, password: string, setupCode?: string) =>
  post<{ ok: boolean; user: Me }>("/api/auth/first-admin", { username, dis, password, ...(setupCode ? { setupCode } : {}) });
export const logout = (everywhere = false) => post<{ ok: boolean }>("/api/auth/logout", { everywhere });
export const changeOwnPassword = (current: string, password: string) =>
  post<{ ok: boolean }>("/api/auth/me/password", { current, password });
export const totpBegin = () => post<{ secret: string; uri: string }>("/api/auth/me/totp", {});
export const totpConfirm = (code: string) => post<{ ok: boolean }>("/api/auth/me/totp/confirm", { code });
export const totpRemove = (password: string) => post<{ ok: boolean }>("/api/auth/me/totp/remove", { password });

export const listUsers = () => get<UserRow[]>("/api/users");
export const addUser = (body: { username: string; dis: string; role: Role; password: string; platform?: boolean }) =>
  post<UserRow>("/api/users", body);
export const changeUser = (username: string, body: Partial<Pick<UserRow, "dis" | "role" | "enabled" | "platform">>) =>
  put<UserRow>(`/api/users/${encodeURIComponent(username)}`, body);
export const removeUser = (username: string) => del<{ ok: boolean }>(`/api/users/${encodeURIComponent(username)}`);
export const setUserPassword = (username: string, password: string) =>
  post<{ ok: boolean }>(`/api/users/${encodeURIComponent(username)}/password`, { password });
export const unlockUser = (username: string) => post<{ ok: boolean }>(`/api/users/${encodeURIComponent(username)}/unlock`, {});
export const resetTotp = (username: string) =>
  post<{ ok: boolean }>(`/api/users/${encodeURIComponent(username)}/totp/remove`, {});

export const getSecurity = () => get<Security>("/api/auth/settings");
export const putSecurity = (body: Partial<Security>) => put<Security>("/api/auth/settings", body);

export function auditQuery(filters: Record<string, string>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) q.set(k, v);
  return q.toString();
}
export const getAudit = (filters: Record<string, string>) => get<AuditRow[]>(`/api/audit?${auditQuery(filters)}`);
