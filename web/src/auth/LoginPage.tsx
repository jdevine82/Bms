// The login page and the first-run page (main spec 7; milestone 6 decision 5).
import { useState } from "react";
import { SetupError } from "../setup/api";
import { firstAdmin, login } from "./authApi";
import "./auth.css";

export function LoginPage({ onDone }: { onDone: () => Promise<void> }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <main className="auth-page">
      <form
        className="auth-card"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await login(username, password, needCode ? code : undefined);
            await onDone();
          } catch (err) {
            if (err instanceof SetupError && err.code === "totp-required") {
              if (needCode) setError(err.message);
              else setError(null);
              setNeedCode(true);
            } else {
              setError(err instanceof Error ? err.message : String(err));
            }
          } finally {
            setBusy(false);
          }
        }}
      >
        <h1>Devine BMS</h1>
        <p className="muted">Log in to this site.</p>
        <label>
          User name
          <input autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {needCode && (
          <label>
            Code from your authenticator app
            <input inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={6} value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </label>
        )}
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button type="submit" className="btn primary" disabled={busy || !username || !password}>
          Log in
        </button>
        <p className="muted small">Forgotten your password? An admin of this site can set a new one.</p>
      </form>
    </main>
  );
}

export function FirstRunPage({ here, needsCode = false, onDone }: { here: boolean; needsCode?: boolean; onDone: () => Promise<void> }) {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [dis, setDis] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!here && !needsCode) {
    return (
      <main className="auth-page">
        <div className="auth-card">
          <h1>Devine BMS</h1>
          <h2>This site has no accounts yet</h2>
          <p>
            The first admin account can only be created on the server itself. Open the web app in a browser on the
            server (http://localhost:8000), or ask whoever installed it.
          </p>
        </div>
      </main>
    );
  }
  const mismatch = again.length > 0 && again !== password;
  return (
    <main className="auth-page">
      <form
        className="auth-card"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await firstAdmin(username, dis, password, needsCode ? code.trim() : undefined);
            await onDone();
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
      >
        <h1>Devine BMS</h1>
        <h2>Create the first admin</h2>
        <p className="muted">
          This site has no accounts yet. The admin you create here can add everyone else. There are no default
          passwords.
        </p>
        {needsCode && (
          <label>
            Setup code (on the Pi's screen)
            <input autoFocus inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" />
          </label>
        )}
        <label>
          Your name
          <input autoFocus={!needsCode} value={dis} onChange={(e) => setDis(e.target.value)} placeholder="Jason Devine" />
        </label>
        <label>
          User name
          <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="jason" />
        </label>
        <label>
          Password (at least 10 characters)
          <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          Password again
          <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        </label>
        {mismatch && <p className="auth-error">The passwords do not match.</p>}
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button type="submit" className="btn primary" disabled={!username || !dis || password.length < 10 || again !== password || (needsCode && code.trim().length !== 6)}>
          Create the admin account
        </button>
      </form>
    </main>
  );
}
