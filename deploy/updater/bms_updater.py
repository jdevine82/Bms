#!/usr/bin/env python3
"""bms-updater: the site server's update agent on the Proxmox host (milestone 12b, decision 031).

It listens on the private bridge only (10.77.0.1:8701). The backend in bms-app hands it a release
uploaded in Setup → Updates (header X-BMS-Update-Token). The agent alone:

1. checks the file is a well-formed release (not who made it: decision 031, 3);
2. notes the version and the online devices, and snapshots both containers (bms-pre-...);
3. pushes the release into bms-app and runs the release's own install-release.sh;
4. health-checks: the new version answers, the same devices are online, 60 s stable;
5. on success keeps the last 3 snapshots; on failure stops both containers, rolls both back,
   starts them and waits for the old version;
6. reports the result to the backend (POST /api/updates/report), which writes the audit log and
   pushes the release to its standby HMIs.

Its state is kept in state.json, so the page shows the result after a restart or a rollback.
Python 3 standard library only. Settings from the environment (/etc/devine-bms/updater.env):
BMS_UPDATER_TOKEN (required), BMS_UPDATER_LISTEN, BMS_APP_CT, BMS_DB_CT, BMS_APP_URL, BMS_UPDATER_DIR.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN_HEADER = "X-BMS-Update-Token"
MAX_RELEASE = 200 * 1024 * 1024
SNAP_PREFIX = "bms-pre-"
KEEP = 3
RELEASE_DIR = "/opt/devine-bms/releases"


class ReleaseError(ValueError):
    pass


def check_release(path: Path) -> str:
    """The release's version, if `path` is a well-formed release: one top folder devine-bms-<v>,
    only plain files and folders, no absolute paths or "..", and the parts an install needs."""
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            if not members:
                raise ReleaseError("the file is empty")
            top = members[0].name.split("/")[0]
            if not re.fullmatch(r"devine-bms-[0-9A-Za-z.+-]+", top):
                raise ReleaseError(f"not a Devine BMS release (top folder {top!r})")
            names = set()
            for m in members:
                parts = m.name.split("/")
                if m.name.startswith("/") or ".." in parts or parts[0] != top:
                    raise ReleaseError(f"unsafe path in the release: {m.name}")
                if not (m.isfile() or m.isdir()):
                    raise ReleaseError(f"only files and folders are allowed: {m.name}")
                names.add(m.name.rstrip("/"))
            for need in ("VERSION", "backend/pyproject.toml", "web/dist/index.html",
                         "deploy/containers/install-release.sh"):
                if f"{top}/{need}" not in names:
                    raise ReleaseError(f"the release has no {need}")
            version = tar.extractfile(f"{top}/VERSION").read().decode().strip()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ReleaseError(f"not a .tar.gz release: {exc}") from exc
    if not re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", version):
        raise ReleaseError("the release's VERSION is not valid")
    return version


def run_cmd(cmd: list[str], stdin: bytes | None = None, timeout: float = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f} s"
    except OSError as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout + p.stderr).decode(errors="replace")


def http_json(method: str, url: str, body: dict | None = None, token: str | None = None,
              timeout: float = 5) -> dict | None:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header(TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "null")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Updater:
    def __init__(self, state_dir: Path, app_ct: str = "202", db_ct: str = "201", app_url: str = "http://10.77.0.10",
                 token: str = "", runner=run_cmd, http=http_json, sleep=time.sleep, clock=time.monotonic,
                 answer_s: float = 180, stable_s: float = 60, poll_s: float = 5) -> None:
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.app, self.db = app_ct, db_ct
        self.app_url = app_url.rstrip("/")
        self.token = token
        self.run, self.http, self.sleep, self.clock = runner, http, sleep, clock
        self.answer_s, self.stable_s, self.poll_s = answer_s, stable_s, poll_s
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.state = self._load()
        if self.state.get("state") == "running":  # the agent restarted mid-update
            self.state.update(state="failed", reason="the updater stopped during the update; check the site",
                              finished=_now())
            self._save()

    # ---------- state ----------

    def _load(self) -> dict:
        try:
            return json.loads((self.dir / "state.json").read_text())
        except (OSError, ValueError):
            return {"state": "idle", "history": []}

    def _save(self) -> None:
        tmp = self.dir / "state.json.new"
        tmp.write_text(json.dumps(self.state, indent=1))
        os.replace(tmp, self.dir / "state.json")

    def _step(self, text: str) -> None:
        self.state.setdefault("steps", []).append({"at": _now(), "text": text})
        self._save()

    def status(self) -> dict:
        return dict(self.state)

    # ---------- starting ----------

    def start(self, path: Path) -> dict:
        """Check the release and start the update in a thread. Raises ReleaseError or RuntimeError."""
        version = check_release(path)
        with self.lock:
            if self.state.get("state") == "running":
                raise RuntimeError("an update is already running")
            history = (self.state.get("history") or [])[-19:]
            if self.state.get("version") and self.state.get("state") not in ("idle", None):
                history.append({k: self.state.get(k) for k in ("version", "from", "state", "reason", "started",
                                                                "finished")})
            self.state = {"state": "running", "version": version, "from": None, "started": _now(),
                          "finished": None, "reason": None, "steps": [], "history": history}
            self._save()
        self.thread = threading.Thread(target=self._job, args=(path, version), name="update", daemon=True)
        self.thread.start()
        return {"ok": True, "version": version}

    # ---------- the update ----------

    def _pct(self, *args: str, timeout: float = 600, stdin: bytes | None = None) -> str:
        rc, out = self.run(["pct", *args], stdin, timeout)
        if rc != 0:
            raise StepError(f"pct {' '.join(args[:2])} failed: {out.strip()[-400:]}")
        return out

    def _health(self) -> dict | None:
        return self.http("GET", f"{self.app_url}/api/health", None, None, 5)

    @staticmethod
    def _online(h: dict | None) -> set[str]:
        return {d["name"] for d in (h or {}).get("devices") or [] if d.get("online")}

    def _job(self, path: Path, version: str) -> None:
        try:
            self._update(path, version)
        finally:
            path.unlink(missing_ok=True)  # bms-app keeps its copy (for the HMIs)

    def _update(self, path: Path, version: str) -> None:
        snap = None
        before: dict | None = None
        try:
            before = self._health()
            if not before:
                raise StepError("the site does not answer /api/health before the update: nothing was changed")
            self.state["from"] = before.get("version")
            online = self._online(before)
            self._step(f"version {before.get('version')} running; {len(online)} devices online")
            snap = f"{SNAP_PREFIX}{re.sub(r'[^0-9A-Za-z]', '_', version)}-{datetime.now(UTC):%Y%m%d%H%M%S}"
            try:
                self._pct("snapshot", self.db, snap, "--description", f"Devine BMS before {version}")
            except StepError as exc:
                snap = None
                raise StepError(f"cannot snapshot the database container (does its storage support snapshots?): "
                                f"{exc}; nothing was changed") from exc
            try:
                self._pct("snapshot", self.app, snap, "--description", f"Devine BMS before {version}")
            except StepError:
                self.run(["pct", "delsnapshot", self.db, snap], None, 120)
                snap = None
                raise
            self.state["snapshot"] = snap
            self._step(f"snapshots {snap} taken of CT {self.db} and CT {self.app}")
            target = f"{RELEASE_DIR}/devine-bms-{version}.tar.gz"
            self._pct("exec", self.app, "--", "mkdir", "-p", RELEASE_DIR)
            self._pct("push", self.app, str(path), target, "--perms", "0644")
            self._step("release copied into bms-app; installing")
            out = self._pct("exec", self.app, "--", "bash", "-c",
                            f"tar -xzf {target} --wildcards -O '*/deploy/containers/install-release.sh' "
                            f"> /root/install-release.sh && bash /root/install-release.sh {target}", timeout=1200)
            self._step(out.strip().splitlines()[-1] if out.strip() else "installed")
            self._check(version, online)
            self.state.update(state="passed", finished=_now())
            self._step(f"health check passed: {version} is running")
            self._prune()
            self._report()
        except StepError as exc:
            self.state["reason"] = str(exc)
            self._step(f"failed: {exc}")
            if snap is not None:
                self._rollback(snap, (before or {}).get("version"))
            else:
                self.state.update(state="failed", finished=_now())
                self._save()
            self._report()
        except Exception as exc:  # noqa: BLE001 -- never leave "running" behind
            self.state.update(state="failed", reason=f"unexpected: {exc}", finished=_now())
            self._save()
            if snap is not None:
                self._rollback(snap, (before or {}).get("version"))
            self._report()

    def _check(self, version: str, online: set[str]) -> None:
        end = self.clock() + self.answer_s
        missing: set[str] = set()
        last = None
        while True:
            h = self._health()
            last = h
            if h and h.get("version") == version:
                missing = online - self._online(h)
                if not missing:
                    break
            if self.clock() >= end:
                if not last:
                    raise StepError(f"the new version did not answer within {self.answer_s:.0f} s")
                if last.get("version") != version:
                    raise StepError(f"version {last.get('version')} answers, not {version}")
                raise StepError(f"devices not back online within {self.answer_s:.0f} s: {', '.join(sorted(missing))}")
            self.sleep(self.poll_s)
        self._step(f"{version} answers and the same devices are online; checking it stays up")
        restarts = self._restarts()
        end = self.clock() + self.stable_s
        while self.clock() < end:
            self.sleep(self.poll_s)
            h = self._health()
            if not h or h.get("version") != version:
                raise StepError("the new version stopped answering during the stability check")
        if self._restarts() != restarts:
            raise StepError("bms-backend restarted during the stability check")

    def _restarts(self) -> str:
        rc, out = self.run(["pct", "exec", self.app, "--", "systemctl", "show", "-p", "NRestarts", "--value",
                            "bms-backend"], None, 30)
        return out.strip() if rc == 0 else "?"

    def _rollback(self, snap: str, old: str | None) -> None:
        self._step(f"rolling back both containers to {snap}")
        try:
            for ct in (self.app, self.db):
                self.run(["pct", "stop", ct], None, 300)
            for ct in (self.db, self.app):
                self._pct("rollback", ct, snap, timeout=900)
            for ct in (self.db, self.app):
                self._pct("start", ct, timeout=300)
        except StepError as exc:
            self.state.update(state="failed", finished=_now(),
                              reason=f"{self.state.get('reason')}; THE ROLLBACK FAILED: {exc}")
            self._step("rollback failed: the site needs attention")
            return
        end = self.clock() + self.answer_s
        while self.clock() < end:
            h = self._health()
            if h and (old is None or h.get("version") == old):
                self.state.update(state="rolled-back", finished=_now())
                self._step(f"rolled back: {h.get('version')} is running again. History and audit entries "
                           "written during the update were lost with the rollback.")
                return
            self.sleep(self.poll_s)
        self.state.update(state="failed", finished=_now(),
                          reason=f"{self.state.get('reason')}; rolled back, but the old version did not answer")
        self._step("rolled back, but the site does not answer: it needs attention")

    def _prune(self) -> None:
        for ct in (self.app, self.db):
            rc, out = self.run(["pct", "listsnapshot", ct], None, 60)
            if rc != 0:
                continue
            ours = sorted({m.group(0) for m in re.finditer(rf"{SNAP_PREFIX}[0-9A-Za-z_]+-\d{{14}}", out)},
                          key=lambda n: n[-14:])
            for old in ours[:-KEEP]:
                self.run(["pct", "delsnapshot", ct, old], None, 300)

    def _report(self) -> None:
        body = {k: self.state.get(k) for k in ("state", "version", "from", "reason", "started", "finished", "steps")}
        for _ in range(6):
            if self.http("POST", f"{self.app_url}/api/updates/report", body, self.token, 10) is not None:
                self.state["reported"] = True
                self._save()
                return
            self.sleep(10)
        self.state["reported"] = False
        self._save()


class StepError(Exception):
    pass


# ---------- HTTP ----------

def make_server(updater: Updater, listen: tuple[str, int], token: str) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "bms-updater"

        def log_message(self, fmt, *args):  # noqa: A002 -- quiet; systemd keeps stderr
            sys.stderr.write("bms-updater: " + fmt % args + "\n")

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            given = self.headers.get(TOKEN_HEADER) or ""
            if token and hmac.compare_digest(given, token):
                return True
            self._send(403, {"ok": False, "error": "the update token is missing or wrong"})
            return False

        def do_GET(self):  # noqa: N802
            if self.path != "/status":
                return self._send(404, {"ok": False, "error": "not found"})
            if self._authorised():
                self._send(200, updater.status())

        def do_POST(self):  # noqa: N802
            if self.path != "/update":
                return self._send(404, {"ok": False, "error": "not found"})
            if not self._authorised():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_RELEASE:
                return self._send(413 if length > MAX_RELEASE else 411,
                                  {"ok": False, "error": f"send the release (at most {MAX_RELEASE // 2**20} MB)"})
            fd, tmp = tempfile.mkstemp(prefix="release-", suffix=".tar.gz", dir=updater.dir)
            with os.fdopen(fd, "wb") as f:
                left = length
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            path = Path(tmp)
            try:
                if left:
                    raise ReleaseError("the upload was cut short")
                result = updater.start(path)
            except ReleaseError as exc:
                path.unlink(missing_ok=True)
                return self._send(422, {"ok": False, "error": str(exc)})
            except RuntimeError as exc:
                path.unlink(missing_ok=True)
                return self._send(409, {"ok": False, "error": str(exc)})
            self._send(202, result)

    return ThreadingHTTPServer(listen, Handler)


def main() -> int:
    token = os.environ.get("BMS_UPDATER_TOKEN", "")
    if len(token) < 20:
        print("bms-updater: BMS_UPDATER_TOKEN (at least 20 characters) is required", file=sys.stderr)
        return 2
    host, port = os.environ.get("BMS_UPDATER_LISTEN", "10.77.0.1:8701").rsplit(":", 1)
    updater = Updater(Path(os.environ.get("BMS_UPDATER_DIR", "/var/lib/devine-bms/updater")),
                      os.environ.get("BMS_APP_CT", "202"), os.environ.get("BMS_DB_CT", "201"),
                      os.environ.get("BMS_APP_URL", "http://10.77.0.10"), token)
    server = make_server(updater, (host, int(port)), token)
    print(f"bms-updater listening on {host}:{port}", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
