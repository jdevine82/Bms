// My account: password, 2FA with an authenticator app, sign out everywhere, and my own actions
// (main spec 7: operators see their own entries in the audit log).
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import type { Outcome } from "../setup/ui";
import { ActionButton, OutcomeBox, outcomeFrom } from "../setup/ui";
import { AuditTable } from "./AuditPage";
import { useAuth } from "./AuthGate";
import type { AuditRow } from "./authApi";
import { removeOwnPin, setOwnPin } from "../commands/commandsApi";
import { changeOwnPassword, getAudit, logout, totpBegin, totpConfirm, totpRemove } from "./authApi";

export function AccountPage() {
  const { user, refresh, can } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [setup, setSetup] = useState<{ secret: string; uri: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [removePw, setRemovePw] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [mine, setMine] = useState<AuditRow[] | null>(null);
  const [pin, setPin] = useState("");
  const [pinPw, setPinPw] = useState("");

  useEffect(() => {
    if (!can("operator") || !user.username) return;
    getAudit({ user: user.username, limit: "50" }).then(setMine).catch(() => setMine(null));
  }, [can, user.username]);

  if (!user.username) {
    return (
      <div className="main-pane narrow-page">
        <h2>{user.dis}</h2>
        <p>This screen is a listed HMI kiosk: it can view the site without a login.</p>
      </div>
    );
  }

  return (
    <div className="main-pane narrow-page account">
      <h2>{user.dis}</h2>
      <p className="muted">
        <span className="mono">{user.username}</span> · {user.role}
        {user.platform ? " · platform admin" : ""}
      </p>
      {user.totpSetupNeeded && <p className="note warn">This site requires 2FA for admins. Set it up below.</p>}
      <OutcomeBox outcome={outcome} />

      <section className="block">
        <h3>Change password</h3>
        <div className="form">
          <label className="stack"><span>Current password</span>
            <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
          <label className="stack"><span>New password (at least 10 characters)</span>
            <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></label>
          <label className="stack"><span>New password again</span>
            <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} /></label>
          <div className="actions">
            <ActionButton primary disabled={!current || next.length < 10 || next !== again} onClick={async () => {
              try {
                await changeOwnPassword(current, next);
                setCurrent(""); setNext(""); setAgain("");
                setOutcome({ kind: "ok", message: "Password changed. Other browsers signed in as you were signed out." });
              } catch (e) { setOutcome(outcomeFrom(e)); }
            }}>Change password</ActionButton>
          </div>
        </div>
      </section>

      <section className="block">
        <h3>Two-factor sign-in (2FA)</h3>
        {user.totp ? (
          <>
            <p>On: logging in asks for a code from your authenticator app.</p>
            <div className="row">
              <input type="password" aria-label="Password to turn off 2FA" placeholder="Your password" value={removePw}
                onChange={(e) => setRemovePw(e.target.value)} />
              <ActionButton danger disabled={!removePw} onClick={async () => {
                try { await totpRemove(removePw); setRemovePw(""); await refresh(); setOutcome({ kind: "ok", message: "2FA is off." }); }
                catch (e) { setOutcome(outcomeFrom(e)); }
              }}>Turn off 2FA</ActionButton>
            </div>
          </>
        ) : setup ? (
          <>
            <p>Scan this with an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password…), then enter the code it shows.</p>
            <img className="qr" src={setup.qr} alt="QR code for the authenticator app" />
            <p className="small">Or type this key into the app: <code className="mono">{setup.secret}</code></p>
            <div className="row">
              <input inputMode="numeric" aria-label="Code from the app" placeholder="123456" maxLength={6} value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
              <ActionButton primary disabled={code.length !== 6} onClick={async () => {
                try { await totpConfirm(code); setSetup(null); setCode(""); await refresh(); setOutcome({ kind: "ok", message: "2FA is on." }); }
                catch (e) { setOutcome(outcomeFrom(e)); }
              }}>Turn on 2FA</ActionButton>
            </div>
          </>
        ) : (
          <>
            <p>Off. With 2FA, logging in also needs a code from an app on your phone.</p>
            <ActionButton onClick={async () => {
              try {
                const begun = await totpBegin();
                setSetup({ ...begun, qr: await QRCode.toDataURL(begun.uri, { margin: 1, width: 200 }) });
              } catch (e) { setOutcome(outcomeFrom(e)); }
            }}>Set up 2FA</ActionButton>
          </>
        )}
      </section>

      {can("operator") && (
        <section className="block">
          <h3>HMI PIN</h3>
          <p className="small">{user.pin ? "Set." : "Not set."} On the HMI touch screen, your name and PIN unlock commands for 5 minutes. At least 4 digits, not your password.</p>
          <div className="row">
            <input type="password" inputMode="numeric" aria-label="New PIN" placeholder="New PIN" maxLength={12} value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
            <input type="password" aria-label="Password for PIN" placeholder="Your password" value={pinPw} onChange={(e) => setPinPw(e.target.value)} />
            <ActionButton disabled={pin.length < 4 || !pinPw} onClick={async () => {
              try { await setOwnPin(pin, pinPw); setPin(""); setPinPw(""); await refresh(); setOutcome({ kind: "ok", message: "HMI PIN set." }); }
              catch (e) { setOutcome(outcomeFrom(e)); }
            }}>Set PIN</ActionButton>
            {user.pin && <ActionButton danger onClick={async () => { await removeOwnPin(); await refresh(); setOutcome({ kind: "ok", message: "HMI PIN removed." }); }}>Remove PIN</ActionButton>}
          </div>
        </section>
      )}

      <section className="block">
        <h3>Sessions</h3>
        <ActionButton onClick={async () => { await logout(true); await refresh(); }}>Sign out everywhere</ActionButton>
      </section>

      {mine && (
        <section className="block">
          <h3>My recent actions</h3>
          <AuditTable rows={mine} />
        </section>
      )}
    </div>
  );
}
