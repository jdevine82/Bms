// Standalone first boot (milestone 11a; main spec 3.1 "Setup and access"): with no accounts, the
// Pi's screen shows where to open the web app, a QR code and the one-time setup code. Setting up
// happens in the web app on a laptop or phone; this screen moves on by itself once it is done.
import QRCode from "qrcode";
import { useEffect, useState, type ReactNode } from "react";
import "./hmi.css";

interface Info { setup: boolean; code?: string; addresses?: string[]; port?: number }

export function StandaloneSetupScreen({ fallback, onDone }: { fallback: ReactNode; onDone: () => Promise<void> }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [full, setFull] = useState(false);
  const [qr, setQr] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = "dark";
    let stop = false;
    const load = async () => {
      try {
        const r = await fetch("/api/standalone/setup-info");
        if (r.status === 404) { setFull(true); return; } // full mode: the usual first-run page
        if (!r.ok) throw new Error(String(r.status));
        const got = (await r.json()) as Info;
        if (stop) return;
        if (!got.setup) { await onDone(); return; } // set up from a laptop or phone
        setInfo(got);
      } catch { /* the backend is starting; try again */ }
      if (!stop) window.setTimeout(load, 3000);
    };
    void load();
    return () => { stop = true; };
  }, [onDone]);

  const url = info?.addresses?.length ? `http://${info.addresses[0]}${info.port && info.port !== 80 ? `:${info.port}` : ""}/` : null;
  useEffect(() => {
    if (url) QRCode.toDataURL(url, { margin: 1, width: 220 }).then(setQr).catch(() => setQr(null));
  }, [url]);

  if (full) return <>{fallback}</>;
  return (
    <main className="hmi standalone-setup" aria-label="Set up this BMS">
      <h1>Set up this BMS</h1>
      {!info && <p className="hmi-note">Starting…</p>}
      {info && (
        <div className="setup-grid">
          <div>
            <p>On a laptop or phone on the same network, open:</p>
            {(info.addresses ?? []).length ? (
              <ul className="setup-urls">
                {info.addresses!.map((a) => <li key={a}><strong>http://{a}{info.port && info.port !== 80 ? `:${info.port}` : ""}/</strong></li>)}
              </ul>
            ) : <p className="hmi-note">No network address yet. Plug in the network cable, or set your laptop to 192.168.50.x and open http://192.168.50.10/.</p>}
            <p>and enter this setup code:</p>
            <p className="setup-code" aria-label="Setup code">{info.code}</p>
            <p className="hmi-note">The code works until the first admin account is made. This screen then shows the site.</p>
          </div>
          {qr && <img className="setup-qr" src={qr} alt={`QR code for ${url}`} width={220} height={220} />}
        </div>
      )}
    </main>
  );
}
