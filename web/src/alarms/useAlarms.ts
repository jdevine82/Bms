// Open alarms and the summary, refreshed when an alarm changes on the live connection.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Alarm, Summary } from "./alarmsApi";
import { getSummary, listAlarms } from "./alarmsApi";

export function useAlarmChanges(onChange: () => void) {
  const ref = useRef(onChange);
  ref.current = onChange;
  useEffect(() => {
    let timer: number | undefined;
    const on = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => ref.current(), 200); // many changes at once: one refresh
    };
    window.addEventListener("bms-alarm", on);
    const poll = window.setInterval(() => ref.current(), 60000); // in case the live connection was down
    return () => {
      window.removeEventListener("bms-alarm", on);
      window.clearInterval(poll);
      window.clearTimeout(timer);
    };
  }, []);
}

export function useOpenAlarms() {
  const [alarms, setAlarms] = useState<Alarm[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const refresh = useCallback(() => {
    listAlarms("open").then(setAlarms).catch(() => undefined);
    getSummary().then(setSummary).catch(() => undefined);
  }, []);
  useEffect(refresh, [refresh]);
  useAlarmChanges(refresh);
  return { alarms, summary, refresh };
}
