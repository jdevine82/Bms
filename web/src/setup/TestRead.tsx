// Test connection: read a device's points and show each value with the raw requests and answers
// (main spec 5.1 step 4). Read-only.
import { useState } from "react";
import type { Obj, TestReadResult } from "./api";
import { testRead } from "./api";
import type { Outcome } from "./ui";
import { ActionButton, Badge, OutcomeBox, outcomeFrom } from "./ui";

export function statusTone(status: string): "ok" | "warn" | "bad" | "muted" {
  return status === "ok" ? "ok" : status === "stale" ? "warn" : status === "unknown" || status === "disabled" ? "muted" : "bad";
}

export function TestReadView({ result }: { result: TestReadResult }) {
  const passed = result.results.filter((r) => r.status === "ok").length;
  return (
    <div className="test-read">
      <p>
        <strong>
          {passed} of {result.results.length} points read
        </strong>
      </p>
      <table className="grid compact">
        <thead>
          <tr>
            <th>Point</th>
            <th>Result</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {result.results.map((r) => (
            <tr key={r.point}>
              <td className="mono">{r.point}</td>
              <td>
                <Badge tone={statusTone(r.status)}>{r.status === "ok" ? "Pass" : r.status}</Badge>
              </td>
              <td>
                {r.status === "ok" ? `${String(r.value)}${r.unit ? ` ${r.unit}` : ""}` : (r.error ?? "")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <details>
        <summary>Raw requests and answers ({result.requests.length})</summary>
        {result.requests.map((q, i) => (
          <div key={i} className="raw">
            <p>
              <strong>{q.request}</strong> {q.ms != null && <span className="muted">{q.ms} ms</span>}{" "}
              {q.error && <span className="bad-text">{q.error}</span>}
            </p>
            {q.frames.map((f, j) => (
              <pre key={j}>
                {f.direction === "sent" ? "→ " : "← "}
                {f.hex ?? f.text}
              </pre>
            ))}
          </div>
        ))}
      </details>
    </div>
  );
}

/** A Test connection button with its result. `body` is what /api/setup/test-read takes. */
export function TestReadPanel({ body, label = "Test connection" }: { body: () => Obj | null; label?: string }) {
  const [result, setResult] = useState<TestReadResult | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  return (
    <div>
      <ActionButton
        onClick={async () => {
          const b = body();
          if (!b) return;
          setOutcome({ kind: "info", message: "Reading…" });
          try {
            setResult(await testRead(b));
            setOutcome(null);
          } catch (e) {
            setResult(null);
            setOutcome(outcomeFrom(e));
          }
        }}
      >
        {label}
      </ActionButton>
      <OutcomeBox outcome={outcome} />
      {result && <TestReadView result={result} />}
    </div>
  );
}
