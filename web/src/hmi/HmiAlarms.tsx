// Active alarms on the HMI overview (milestone 6B). View only: acknowledging is done in the web
// app until HMI PINs arrive with commands (milestone 7).
import { LEVEL_SHAPE, LEVEL_TEXT } from "../alarms/alarmsApi";
import { useOpenAlarms } from "../alarms/useAlarms";

export function HmiAlarms() {
  const { alarms } = useOpenAlarms();
  const active = alarms.filter((a) => a.active);
  if (!active.length) return null;
  return (
    <section className="hmi-alarms" aria-label="Active alarms">
      <h2>Alarms ({active.length})</h2>
      <ul>
        {active.slice(0, 6).map((a) => (
          <li key={a.id} className={`hmi-alarm ${a.level}`}>
            <span className="hmi-alarm-level">{LEVEL_SHAPE[a.level]} {LEVEL_TEXT[a.level]}</span>
            <span>{a.message}</span>
            <span className="hmi-alarm-state">{a.ackedAt ? "Acknowledged" : "Not acknowledged"}</span>
          </li>
        ))}
      </ul>
      {active.length > 6 && <p>and {active.length - 6} more</p>}
    </section>
  );
}
