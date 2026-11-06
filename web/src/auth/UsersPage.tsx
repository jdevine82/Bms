// Users (admin, main spec 7): add, change role, disable, set a password, unlock, remove 2FA.
import { useState } from "react";
import type { Outcome } from "../setup/ui";
import { ActionButton, Badge, OutcomeBox, outcomeFrom, useLoad } from "../setup/ui";
import { useAuth } from "./AuthGate";
import type { Role, UserRow } from "./authApi";
import { removeUserPin } from "../commands/commandsApi";
import { addUser, changeUser, listUsers, removeUser, resetTotp, setUserPassword, unlockUser } from "./authApi";

const ROLE_TEXT: Record<Role, string> = {
  viewer: "Viewer: sees graphics, values, trends and alarms",
  operator: "Operator: also acknowledges alarms and gives commands within limits",
  admin: "Admin: also sets up devices, templates, users and settings",
};

export function UsersPage() {
  const { user: me } = useAuth();
  const users = useLoad(() => listUsers(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ username: "", dis: "", role: "operator" as Role, password: "", platform: false });
  const [newPw, setNewPw] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const u = (users.data ?? []).find((x) => x.username === selected) ?? null;

  const act = async (fn: () => Promise<unknown>, message: string) => {
    try { await fn(); setOutcome({ kind: "ok", message }); await users.reload(); } catch (e) { setOutcome(outcomeFrom(e)); }
  };

  return (
    <div className="split">
      <section className="list-pane" aria-label="Users">
        <div className="pane-head">
          <h2>Users</h2>
          <button type="button" className="btn" onClick={() => { setAdding(true); setSelected(null); setOutcome(null); }}>Add</button>
        </div>
        <ul className="pick-list">
          {(users.data ?? []).map((x: UserRow) => (
            <li key={x.username}>
              <button type="button" className={selected === x.username && !adding ? "active" : ""}
                onClick={() => { setSelected(x.username); setAdding(false); setOutcome(null); setNewPw(""); }}>
                <span className="pick-main">
                  <span>{x.dis}</span>
                  <span className="mono muted small">{x.username}</span>
                </span>
                <span className="pick-side">
                  <Badge tone={x.role === "admin" ? "info" : "muted"}>{x.role}</Badge>
                  {!x.enabled && <Badge tone="muted">Disabled</Badge>}
                  {x.locked && <Badge tone="bad">Locked</Badge>}
                  {x.totp && <span className="small muted">2FA</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="detail-pane" aria-label="User details">
        <OutcomeBox outcome={outcome} />
        {adding ? (
          <div className="form">
            <h2>New account</h2>
            <label className="stack"><span>Name</span><input aria-label="Name" value={form.dis} onChange={(e) => setForm({ ...form, dis: e.target.value })} /></label>
            <label className="stack"><span>User name</span><input aria-label="User name" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
            <label className="stack"><span>Role</span>
              <select aria-label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
                {(Object.keys(ROLE_TEXT) as Role[]).map((r) => <option key={r} value={r}>{ROLE_TEXT[r]}</option>)}
              </select></label>
            <label className="stack"><span>First password (they can change it)</span>
              <input aria-label="First password" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
            <label className="check"><input type="checkbox" checked={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.checked })} /> Platform admin account</label>
            <div className="actions">
              <ActionButton primary disabled={!form.username || !form.dis || !form.password} onClick={async () => {
                try {
                  await addUser(form);
                  setOutcome({ kind: "ok", message: `Added ${form.username}.` });
                  setSelected(form.username.trim().toLowerCase()); setAdding(false);
                  setForm({ username: "", dis: "", role: "operator", password: "", platform: false });
                  await users.reload();
                } catch (e) { setOutcome(outcomeFrom(e)); }
              }}>Add account</ActionButton>
            </div>
          </div>
        ) : u ? (
          <div className="form">
            <h2>{u.dis}</h2>
            <p className="muted"><span className="mono">{u.username}</span>{u.lastLogin ? ` · last login ${new Date(u.lastLogin).toLocaleString()}` : " · never logged in"}</p>
            <label className="stack"><span>Role</span>
              <select aria-label="Role" value={u.role} onChange={(e) => act(() => changeUser(u.username, { role: e.target.value as Role }), `${u.username} is now ${e.target.value}; it applies from their next login.`)}>
                {(Object.keys(ROLE_TEXT) as Role[]).map((r) => <option key={r} value={r}>{ROLE_TEXT[r]}</option>)}
              </select></label>
            <label className="check"><input type="checkbox" checked={u.enabled} disabled={u.username === me.username}
              onChange={(e) => act(() => changeUser(u.username, { enabled: e.target.checked }), e.target.checked ? "Enabled." : "Disabled and signed out.")} /> Account enabled</label>
            {u.locked && (
              <p className="note bad">Locked after failed logins until {new Date(u.lockedUntil!).toLocaleTimeString()}.{" "}
                <ActionButton onClick={() => act(() => unlockUser(u.username), "Unlocked.")}>Unlock</ActionButton></p>
            )}
            <div className="row">
              <input type="password" aria-label="New password" placeholder="New password" autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
              <ActionButton disabled={!newPw} onClick={() => act(async () => { await setUserPassword(u.username, newPw); setNewPw(""); }, "Password set; they were signed out.")}>Set password</ActionButton>
            </div>
            {u.pin && <ActionButton onClick={() => act(() => removeUserPin(u.username), "HMI PIN removed.")}>Remove HMI PIN</ActionButton>}
            {u.totp && <ActionButton onClick={() => act(() => resetTotp(u.username), "2FA removed; they can set it up again.")}>Remove 2FA (lost phone)</ActionButton>}
            {u.username !== me.username && (
              <ActionButton danger onClick={async () => {
                if (!window.confirm(`Remove the account ${u.username}? The audit log keeps its entries.`)) return;
                await act(() => removeUser(u.username), `Removed ${u.username}.`);
                setSelected(null);
              }}>Remove account</ActionButton>
            )}
          </div>
        ) : <p className="muted">Pick a person, or add one.</p>}
      </section>
    </div>
  );
}
