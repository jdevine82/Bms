// Shows the first-run page, the login page, or the app (milestone 6A, main spec 7).
// Any request that comes back "log in first" (401), or a live connection closed because the
// session ended, brings the login page back. The session is checked every minute without
// counting as activity, so 30 minutes idle signs a person out as the server says.
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { AuthState, Me, Role } from "./authApi";
import { authState } from "./authApi";
import { FirstRunPage, LoginPage } from "./LoginPage";

interface Auth {
  user: Me;
  refresh: () => Promise<void>;
  can: (role: Role) => boolean;
}

const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
const AuthContext = createContext<Auth | null>(null);

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthGate");
  return auth;
}

// Every 401 from the API (except the login calls themselves) means the session has ended.
const realFetch = window.fetch.bind(window);
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const response = await realFetch(input, init);
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (response.status === 401 && url.includes("/api/") && !url.includes("/api/auth/")) {
    window.dispatchEvent(new Event("bms-signed-out"));
  }
  return response;
};

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await authState());
      setError(null);
    } catch (e) {
      setError(`Cannot reach the BMS backend: ${String(e)}`);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const signedOut = () => void refresh();
    window.addEventListener("bms-signed-out", signedOut);
    const timer = window.setInterval(async () => {
      try {
        const s = await authState(false);
        setState((old) => (old?.user && !s.user ? s : old));
      } catch {
        /* the backend is restarting; the next check tries again */
      }
    }, 60000);
    return () => {
      window.removeEventListener("bms-signed-out", signedOut);
      window.clearInterval(timer);
    };
  }, [refresh]);

  if (error) return <p className="error gate-error">{error}</p>;
  if (!state) return null;
  if (!state.user) {
    if (state.firstRun) return <FirstRunPage here={state.firstRunHere} onDone={refresh} />;
    return <LoginPage onDone={refresh} />;
  }
  const user = state.user;
  return (
    <AuthContext.Provider value={{ user, refresh, can: (role) => RANK[user.role] >= RANK[role] }}>
      {children}
    </AuthContext.Provider>
  );
}
