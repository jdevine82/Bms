// Live values over the WebSocket at /api/live, reconnecting if the connection drops.

import { useEffect, useRef, useState } from "react";
import type { LiveValue } from "./api";

type Listener = (value: LiveValue) => void;

/** Subscribe to point ids ("all" for every point). Calls onValue for the snapshot and each change. */
export function useLive(ids: string[] | "all", onValue: Listener): boolean {
  const [connected, setConnected] = useState(false);
  const listener = useRef(onValue);
  listener.current = onValue;
  const key = ids === "all" ? "all" : [...ids].sort().join(",");

  useEffect(() => {
    if (key === "") return;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let delay = 1000;

    const open = () => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${location.host}/api/live`);
      socket.onopen = () => {
        delay = 1000;
        setConnected(true);
        socket?.send(JSON.stringify({ subscribe: key === "all" ? "all" : key.split(",") }));
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data as string);
        if (message.type === "snapshot") message.values.forEach((v: LiveValue) => listener.current(v));
        if (message.type === "value") listener.current(message as LiveValue);
        // Alarm changes come on the same connection (milestone 6B); pages listen for this event.
        if (message.type === "alarm") window.dispatchEvent(new CustomEvent("bms-alarm", { detail: message.alarm }));
        // Hand-mode overrides set, released or timed out (milestone 7A).
        if (message.type === "override") window.dispatchEvent(new CustomEvent("bms-override", { detail: message }));
      };
      socket.onclose = (event) => {
        setConnected(false);
        // 4401: not signed in, or the session ended (milestone 6A). The login screen takes over.
        if (event.code === 4401) window.dispatchEvent(new Event("bms-signed-out"));
        if (!closed) {
          retry = setTimeout(open, delay);
          delay = Math.min(delay * 2, 15000);
        }
      };
    };
    open();
    return () => {
      closed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, [key]);

  return connected;
}
