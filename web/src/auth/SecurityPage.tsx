// Site settings → Security (main spec 7 and 8): login rules, HMI kiosks and allowed networks.
import { useEffect, useState } from "react";
import type { Outcome } from "../setup/ui";
import { ActionButton, OutcomeBox, outcomeFrom } from "../setup/ui";
import type { Security } from "./authApi";
import { getSecurity, putSecurity } from "./authApi";

const list = (text: string) => text.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

export function SecurityPage() {
  const [s, setS] = useState<Security | null>(null);
  const [kiosks, setKiosks] = useState("");
  const [networks, setNetworks] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  useEffect(() => {
    getSecurity().then((x) => { setS(x); setKiosks(x.kioskAddresses.join("\n")); setNetworks(x.allowedNetworks.join("\n")); })
      .catch((e) => setOutcome(outcomeFrom(e)));
  }, []);
  if (!s) return <OutcomeBox outcome={outcome ?? { kind: "info", message: "Loading…" }} />;
  const num = (k: keyof Security, label: string, min: number, max: number) => (
    <div className="form-row">
      <label htmlFor={`sec-${k}`}>{label}</label>
      <input id={`sec-${k}`} type="number" min={min} max={max} value={s[k] as number} onChange={(e) => setS({ ...s, [k]: Number(e.target.value) })} />
    </div>
  );
  return (
    <div className="main-pane narrow-page">
      <h2>Security</h2>
      <div className="form">
        <h3>Login rules</h3>
        {num("minPasswordLength", "Shortest password", 10, 128)}
        {num("idleTimeoutMin", "Sign out after idle (minutes)", 5, 720)}
        {num("lockoutAttempts", "Lock after failed logins", 3, 20)}
        {num("lockoutMinutes", "Lock for (minutes)", 1, 1440)}
        <div className="form-row check">
          <label><input type="checkbox" checked={s.requireTotpForAdmins} onChange={(e) => setS({ ...s, requireTotpForAdmins: e.target.checked })} /> Require 2FA for admins</label>
        </div>
        <h3>Haystack API</h3>
        <div className="form-row check">
          <label><input type="checkbox" checked={!!s.haystackApi} onChange={(e) => setS({ ...s, haystackApi: e.target.checked })} /> Haystack API on (read only)</label>
        </div>
        <p className="muted small">Lets analytics and energy tools read the site's model, live values and history with Project Haystack 4.
          Address: <code>{location.origin}/api/haystack</code>. Only Haystack API client accounts (Users) can log in, with SCRAM.
          Nothing can be written through it.</p>
        <h3>HMI kiosks</h3>
        <p className="muted small">Addresses of HMI screens that show the site without a login (view only; commands will need a PIN, milestone 7). One per line.</p>
        <textarea className="json short" aria-label="Kiosk addresses" value={kiosks} onChange={(e) => setKiosks(e.target.value)} placeholder="192.168.10.21" />
        <h3>Allowed networks</h3>
        <p className="muted small">If set, logins are only accepted from these networks (and the server itself). Passwords cross the LAN unencrypted, so keep the web app on the device or management network (main spec 8). One per line, for example 192.168.10.0/24 or 100.64.0.0/10 for Tailscale.</p>
        <textarea className="json short" aria-label="Allowed networks" value={networks} onChange={(e) => setNetworks(e.target.value)} placeholder="(any network)" />
        <div className="actions">
          <ActionButton primary onClick={async () => {
            try {
              const saved = await putSecurity({ ...s, kioskAddresses: list(kiosks), allowedNetworks: list(networks) });
              setS(saved);
              setOutcome({ kind: "ok", message: "Saved." });
            } catch (e) { setOutcome(outcomeFrom(e)); }
          }}>Save security settings</ActionButton>
        </div>
        <OutcomeBox outcome={outcome} />
      </div>
    </div>
  );
}
