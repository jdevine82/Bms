// Setup pages live under #/setup/<section> so a page can be linked and reloaded.
import { useEffect, useState } from "react";

export type Section =
  | "devices"
  | "buses"
  | "discover"
  | "device-templates"
  | "equip-templates"
  | "commission"
  | "mapping"
  | "rooms"
  | "equipment"
  | "site"
  | "standalone"
  | "updates"
  | "changes"
  | "users"
  | "security"
  | "audit"
  | "graphics"
  | "alarm-definitions"
  | "alarm-delivery";

export function readHash(): { page: string; section: string | null; rest: string[] } {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return { page: parts[0] ?? "", section: parts[1] ?? null, rest: parts.slice(2) };
}

export function navigate(section: Section, ...rest: string[]) {
  location.hash = `#/setup/${[section, ...rest].map(encodeURIComponent).join("/")}`;
}

export function useHash() {
  const [hash, setHash] = useState(readHash);
  useEffect(() => {
    const on = () => setHash(readHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}
