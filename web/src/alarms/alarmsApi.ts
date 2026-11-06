// Alarm calls (milestone 6B, main spec 5.5 and 5.5.1).
import { get, post, put } from "../setup/api";

export type Level = "info" | "warning" | "critical";

export interface Alarm {
  id: string;
  key: string;
  source: string;
  sourceDis: string;
  type: "high" | "low" | "state" | "stale" | "offline";
  level: Level;
  message: string;
  detail: { value?: number | string | boolean | null; unit?: string | null; limit?: number; state?: string | boolean };
  active: boolean;
  closed: boolean;
  raisedAt: string;
  returnedAt: string | null;
  ackedAt: string | null;
  ackedBy: string | null;
}

export interface AlarmEvent {
  ts: string;
  kind: "raised" | "returned" | "acked" | "comment";
  user: string | null;
  text: string | null;
}

export interface Delivery {
  id: string;
  alarmId: string | null;
  recipient: string;
  channel: "email" | "sms" | "ntfy";
  address: string;
  level: Level;
  status: "pending" | "held" | "sent" | "failed";
  attempts: number;
  error: string | null;
  test: boolean;
  createdAt: string;
  sentAt: string | null;
  holdUntil: string | null;
}

export interface Recipient {
  id: string;
  name: string;
  email?: string;
  phone?: string;
  ntfyTopic?: string;
  levels: Record<Level, boolean>;
  quietHours: { start: string; end: string } | null;
  enabled: boolean;
}

export interface AlarmSettings {
  channels: Record<Level, ("email" | "sms" | "ntfy")[]>;
  email: { host: string; port: number; security: "starttls" | "ssl" | "none"; username: string; passwordEnv: string; from: string };
  sms: { provider: "clicksend"; username: string; apiKeyEnv: string; from: string; baseUrl: string };
  ntfy: { server: string; tokenEnv: string | null };
  recipients: Recipient[];
}

export interface Summary {
  unacked: Record<Level, number>;
  active: number;
}

export const listAlarms = (state = "open", extra: Record<string, string> = {}) =>
  get<Alarm[]>(`/api/alarms?${new URLSearchParams({ state, ...extra })}`);
export const getAlarm = (id: string) => get<Alarm & { events: AlarmEvent[]; deliveries: Delivery[] }>(`/api/alarms/${id}`);
export const getSummary = () => get<Summary>("/api/alarms/summary");
export const ackAlarms = (ids: string[], comment: string) => post<{ ok: boolean; acknowledged: Alarm[] }>("/api/alarms/ack", { ids, comment });
export const commentAlarm = (id: string, text: string) => post<{ ok: boolean }>(`/api/alarms/${id}/comment`, { text });
export const listDeliveries = (limit = 200) => get<Delivery[]>(`/api/alarm-deliveries?limit=${limit}`);
export const getAlarmSettings = () => get<{ settings: AlarmSettings; warnings: string[] }>("/api/alarm-settings");
export const putAlarmSettings = (s: AlarmSettings) => put<{ ok: boolean; settings: AlarmSettings; warnings: string[] }>("/api/alarm-settings", s);
export const sendTest = (recipient: string, channel: string) =>
  post<{ ok: boolean; error: string | null }>("/api/alarm-settings/test", { recipient, channel });

export const LEVEL_TEXT: Record<Level, string> = { critical: "Critical", warning: "Warning", info: "Info" };
// Level by shape and text as well as colour (main spec 5.5 and 6.3).
export const LEVEL_SHAPE: Record<Level, string> = { critical: "◆", warning: "▲", info: "●" };
