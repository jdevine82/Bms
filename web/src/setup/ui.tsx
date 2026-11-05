// Small building blocks for the setup screens.
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Issue } from "./api";
import { SetupError } from "./api";

export function IssueList({ issues }: { issues: Issue[] }) {
  const [all, setAll] = useState(false);
  if (!issues.length) return null;
  const shown = all ? issues : issues.slice(0, 8);
  return (
    <ul className="issues">
      {shown.map((i, n) => (
        <li key={n} className={i.severity}>
          <span className="badge">{i.severity === "error" ? "Error" : "Warning"}</span> <code>{i.where}</code>{" "}
          {i.message}
        </li>
      ))}
      {issues.length > shown.length && (
        <li>
          <button type="button" className="link" onClick={() => setAll(true)}>
            Show all {issues.length}
          </button>
        </li>
      )}
    </ul>
  );
}

/** The outcome of the last action: a message, and the failed checks of a refused change. */
export interface Outcome {
  kind: "ok" | "error" | "info";
  message: string;
  issues?: Issue[];
}

export function outcomeFrom(e: unknown): Outcome {
  if (e instanceof SetupError) return { kind: "error", message: e.message, issues: e.issues };
  return { kind: "error", message: e instanceof Error ? e.message : String(e) };
}

export function OutcomeBox({ outcome }: { outcome: Outcome | null }) {
  if (!outcome) return null;
  return (
    <div className={`outcome ${outcome.kind}`} role={outcome.kind === "error" ? "alert" : "status"}>
      <p>{outcome.message}</p>
      {outcome.issues && <IssueList issues={outcome.issues} />}
    </div>
  );
}

/** Load something on mount and on demand. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(() => {
    setLoading(true);
    return load()
      .then((d) => {
        setData(d);
        setError(null);
        return d;
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      })
      .finally(() => setLoading(false));
  }, deps);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

/** A button that shows it is busy while its action runs. */
export function ActionButton({
  onClick,
  children,
  primary,
  danger,
  disabled,
  title,
}: {
  onClick: () => Promise<unknown> | void;
  children: ReactNode;
  primary?: boolean;
  danger?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      title={title}
      className={`btn${primary ? " primary" : ""}${danger ? " danger" : ""}`}
      disabled={disabled || busy}
      aria-busy={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onClick();
        } finally {
          setBusy(false);
        }
      }}
    >
      {children}
    </button>
  );
}

export function Badge({ tone, children }: { tone: "ok" | "warn" | "bad" | "info" | "muted"; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function FilePick({ label, accept, onText }: { label: string; accept: string; onText: (text: string, name: string) => void }) {
  return (
    <label className="btn file-pick">
      {label}
      <input
        type="file"
        accept={accept}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (file) onText(await file.text(), file.name);
          e.target.value = "";
        }}
      />
    </label>
  );
}
