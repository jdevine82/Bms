// The HMI Setpoints tab (milestone 7A): writable setpoints and commands with large controls.
// Commands need a person: on a kiosk they pick their name and enter their PIN, which unlocks
// commands for 5 minutes idle (main spec 7 and 6.2). View only until then.
import { useEffect, useState } from "react";
import type { LiveValue, Point } from "../api";
import { useAuth } from "../auth/AuthGate";
import { logout } from "../auth/authApi";
import { CommandPanel, OverrideBadge } from "../commands/CommandPanel";
import { pinLogin, pinUsers } from "../commands/commandsApi";
import { formatValue } from "../format";
import { SetupError } from "../setup/api";

export function PinPad({ onDone }: { onDone: () => void }) {
  const [people, setPeople] = useState<{ username: string; dis: string }[] | null>(null);
  const [who, setWho] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    pinUsers().then(setPeople).catch((e) => setError(e instanceof SetupError ? e.message : String(e)));
  }, []);
  if (!who) {
    return (
      <div className="hmi-pin">
        <h2>Unlock to change settings</h2>
        {error && <p className="hmi-pin-error">{error}</p>}
        {people && !people.length && <p>Nobody has an HMI PIN yet. Set one on My account in the web app.</p>}
        <div className="hmi-pin-people">
          {(people ?? []).map((p) => (
            <button key={p.username} type="button" onClick={() => { setWho(p.username); setError(null); }}>{p.dis}</button>
          ))}
        </div>
      </div>
    );
  }
  const press = (d: string) => setPin((x) => (x.length < 12 ? x + d : x));
  return (
    <div className="hmi-pin">
      <h2>PIN for {people?.find((p) => p.username === who)?.dis}</h2>
      <div className="hmi-pin-dots" aria-label={`${pin.length} digits entered`}>{"●".repeat(pin.length) || " "}</div>
      {error && <p className="hmi-pin-error" role="alert">{error}</p>}
      <div className="hmi-pin-keys">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
          <button key={d} type="button" onClick={() => press(d)}>{d}</button>
        ))}
        <button type="button" onClick={() => { setWho(null); setPin(""); }}>Back</button>
        <button type="button" onClick={() => press("0")}>0</button>
        <button type="button" disabled={pin.length < 4} onClick={async () => {
          try { await pinLogin(who, pin); onDone(); }
          catch (e) { setError(e instanceof SetupError ? e.message : String(e)); setPin(""); }
        }}>Unlock</button>
      </div>
    </div>
  );
}

export function HmiSetpoints({ points, live, tz }: { points: Point[]; live: Record<string, LiveValue>; tz: string }) {
  const { user, refresh, can } = useAuth();
  const [open, setOpen] = useState<string | null>(null);
  const [unlocking, setUnlocking] = useState(false);
  const unlocked = !!user.username && can("operator");
  const writable = points.filter((p) => p.writable);
  const groups = new Map<string, Point[]>();
  for (const p of writable) {
    const key = p.equip?.dis ?? p.space?.dis ?? "Site";
    groups.set(key, [...(groups.get(key) ?? []), p]);
  }
  const target = writable.find((p) => p.id === open) ?? null;

  if (unlocking && !unlocked) return <PinPad onDone={async () => { setUnlocking(false); await refresh(); }} />;

  return (
    <div className="hmi-setpoints">
      <div className="hmi-lockbar">
        {unlocked ? (
          <>
            <span>Changes by {user.dis}{user.pinSession ? " · locks after 5 minutes idle" : ""}</span>
            {user.pinSession && <button type="button" onClick={async () => { await logout(); await refresh(); }}>Lock now</button>}
          </>
        ) : (
          <>
            <span>View only</span>
            <button type="button" onClick={() => setUnlocking(true)}>Unlock with PIN</button>
          </>
        )}
      </div>
      {!writable.length && <p className="hmi-empty">No setpoints or commands on this site yet.</p>}
      {[...groups.entries()].map(([name, list]) => (
        <section key={name} className="hmi-card">
          <h2>{name}</h2>
          {list.map((p) => {
            const cur = live[p.id] ?? p.cur;
            return (
              <div key={p.id} className="hmi-sp-row">
                <div>
                  <div>{p.dis}</div>
                  <OverrideBadge point={p} tz={tz} />
                </div>
                <div className="hmi-sp-value">{formatValue(p, cur)}</div>
                {unlocked && <button type="button" className="hmi-sp-change" onClick={() => setOpen(p.id)}>Change</button>}
              </div>
            );
          })}
        </section>
      ))}
      {target && unlocked && (
        <div className="command-overlay" onClick={(e) => e.target === e.currentTarget && setOpen(null)}>
          <CommandPanel big tz={tz} point={target} live={live[target.id] ?? target.cur} onClose={() => setOpen(null)} />
        </div>
      )}
    </div>
  );
}
