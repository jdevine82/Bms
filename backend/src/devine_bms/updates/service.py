"""Updates in the web app (milestone 12b, decision 031).

- **Site server:** an uploaded release is checked and handed to the host's updater agent, which
  snapshots, installs, health-checks and rolls back. The agent reports the result here; a passed
  update is then pushed to the standby HMIs.
- **Pi (standalone, or a standby HMI receiving the server's push):** the upload is kept in RAM and
  `bms-pi-update stage` (as root) copies it onto the config partition, turns the overlay off and
  reboots into the update.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from devine_bms import __version__
from devine_bms.updates.release import MAX_RELEASE, ReleaseError, check_release

log = logging.getLogger(__name__)
UPDATE_TOKEN_HEADER = "X-BMS-Update-Token"


class UpdateError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class UpdateService:
    def __init__(self, settings, record, mode: str, get_standby=lambda: None, get_gate=lambda: None) -> None:
        self.cfg = settings
        self.record = record
        self.mode = mode  # full, standalone or standby
        self.get_standby = get_standby
        self.get_gate = get_gate
        self.pushes: dict[str, dict] = {}  # HMI url -> the last push
        self._tasks: set[asyncio.Task] = set()

    @property
    def kind(self) -> str:
        if self.mode == "full" and self.cfg.updater_url and self.cfg.updater_token:
            return "server"
        if self.mode in ("standalone", "standby") and self.cfg.pi_update_cmd:
            return "pi"
        return "none"

    # ---------- reading ----------

    async def agent_status(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.cfg.updater_url.rstrip('/')}/status",
                                     headers={UPDATE_TOKEN_HEADER: self.cfg.updater_token})
        except httpx.HTTPError as exc:
            return {"state": "unreachable", "reason": f"the updater on the host does not answer: {exc}"}
        if r.status_code != 200:
            return {"state": "unreachable", "reason": f"the updater refused ({r.status_code}): check BMS_UPDATER_TOKEN"}
        return r.json()

    def last_pi_update(self) -> dict | None:
        try:
            return json.loads((Path(self.cfg.update_dir) / "last-update.json").read_text())
        except (OSError, ValueError):
            return None

    async def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "version": __version__, "mode": self.mode}
        if self.kind == "server":
            out["agent"] = await self.agent_status()
        if self.kind == "pi":
            out["lastUpdate"] = self.last_pi_update()
        gate = self.get_gate()
        if gate is not None:
            out["hmis"] = [{"url": u, "name": (gate.seen.get(u) or {}).get("name"),
                            "mode": (gate.seen.get(u) or {}).get("mode"),
                            "version": (gate.seen.get(u) or {}).get("version"),
                            "lastUpdate": (gate.seen.get(u) or {}).get("lastUpdate"),
                            "push": self.pushes.get(u)} for u in gate.urls]
        return out

    # ---------- an upload ----------

    async def receive(self, chunks, where: Path | None = None) -> tuple[Path, str]:
        """Save an upload (at most 200 MB) and check it is a release."""
        where = where or Path(tempfile.gettempdir())
        where.mkdir(parents=True, exist_ok=True)
        f = tempfile.NamedTemporaryFile(prefix="upload-", suffix=".tar.gz", dir=where, delete=False)
        path = Path(f.name)
        size = 0
        try:
            with f:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > MAX_RELEASE:
                        raise UpdateError(f"the file is larger than {MAX_RELEASE // 2**20} MB", 413)
                    f.write(chunk)
            if not size:
                raise UpdateError("choose a release file (devine-bms-<version>.tar.gz)", 422)
            version = await asyncio.to_thread(check_release, path)
        except ReleaseError as exc:
            path.unlink(missing_ok=True)
            raise UpdateError(str(exc), 422) from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, version

    async def upload(self, actor, chunks) -> dict:
        if self.kind == "none":
            raise UpdateError("updates are not set up on this machine (deploy/README.md, Updates)", 404)
        if self.kind == "server":
            path, version = await self.receive(chunks)
            try:
                return await self._to_agent(actor, path, version)
            finally:
                path.unlink(missing_ok=True)
        path, version = await self.receive(chunks)  # in RAM: the config partition is read-only to the backend
        return await self.stage_pi(actor, path, version, "uploaded in the web app")

    async def _to_agent(self, actor, path: Path, version: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120)) as client:
                r = await client.post(f"{self.cfg.updater_url.rstrip('/')}/update", content=path.read_bytes(),
                                      headers={UPDATE_TOKEN_HEADER: self.cfg.updater_token,
                                               "Content-Type": "application/gzip"})
        except httpx.HTTPError as exc:
            raise UpdateError(f"the updater on the host does not answer: {exc}", 502) from exc
        if r.status_code != 202:
            try:
                message = r.json().get("error") or r.text
            except ValueError:
                message = r.text
            raise UpdateError(f"the updater refused the release: {message}", 409 if r.status_code == 409 else 422)
        await self.record(actor, "update-start", f"started the update from {__version__} to {version}",
                          kind="update", name=version)
        return {"ok": True, "version": version, "from": __version__}

    # ---------- the agent's report (site server) ----------

    async def report(self, body: dict) -> dict:
        from devine_bms.auth.service import Actor

        state, version = body.get("state"), str(body.get("version") or "?")[:40]
        reason = str(body.get("reason") or "")[:1000]
        summary = {
            "passed": f"update to {version} passed its health check (from {body.get('from')})",
            "rolled-back": f"update to {version} failed and both containers were rolled back to "
                           f"{body.get('from')}: {reason}",
        }.get(state, f"update to {version} failed: {reason}")
        agent = Actor(client="updater", role="admin", username=None, dis="Updater")
        steps = [s for s in body.get("steps") or [] if isinstance(s, dict)][-50:]
        await self.record(agent, "update", summary, kind="update", name=version, after={"steps": steps})
        if state == "passed":
            self._background(self.push_hmis(version))
        return {"ok": True}

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def push_hmis(self, version: str) -> None:
        """Send the release that passed to every standby HMI (decision 031, 4)."""
        from devine_bms.auth.service import Actor
        from devine_bms.standby.server import TOKEN_HEADER

        urls = [u.strip().rstrip("/") for u in (self.cfg.hmi_urls or "").split(",") if u.strip()]
        if not urls:
            return
        path = Path(self.cfg.release_dir) / f"devine-bms-{version}.tar.gz"
        agent = Actor(client="updater", role="admin", username=None, dis="Updater")
        if not path.is_file():
            for url in urls:
                self.pushes[url] = {"version": version, "result": f"the release is not at {path}",
                                    "at": datetime.now(UTC).isoformat()}
            return
        data = await asyncio.to_thread(path.read_bytes)
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120)) as client:
                    r = await client.post(f"{url}/api/standby/update", content=data,
                                          headers={TOKEN_HEADER: self.cfg.standby_token,
                                                   "Content-Type": "application/gzip"})
                result = "accepted: the HMI is updating (about 3 minutes)" if r.status_code == 200 else \
                    f"refused ({r.status_code}): {_error(r)}"
            except httpx.HTTPError as exc:
                result = f"not reachable: {exc}"
            self.pushes[url] = {"version": version, "result": result, "at": datetime.now(UTC).isoformat()}
            await self.record(agent, "update-hmi", f"sent {version} to the HMI at {url}: {result}", kind="update",
                              name=version)

    # ---------- a Pi ----------

    async def stage_pi(self, actor, path: Path, version: str, source: str) -> dict:
        standby = self.get_standby()
        if standby is not None and standby.mode == "direct":
            path.unlink(missing_ok=True)
            raise UpdateError("the HMI is in direct mode: hand control back before updating it")
        if not self.cfg.pi_update_cmd:
            path.unlink(missing_ok=True)
            raise UpdateError("updates are not set up on this Pi (BMS_PI_UPDATE_CMD)", 404)
        await self.record(actor, "update-start", f"update from {__version__} to {version} ({source}): the Pi "
                          "restarts twice and is back in about 3 minutes", kind="update", name=version)
        proc = await asyncio.create_subprocess_exec(*shlex.split(self.cfg.pi_update_cmd), str(path),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        path.unlink(missing_ok=True)  # the stage command has copied it onto the config partition
        if proc.returncode != 0:
            raise UpdateError(f"the update could not start: {out.decode(errors='replace').strip()[-400:]}", 500)
        return {"ok": True, "version": version, "from": __version__, "restarting": True}


def _error(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error") or r.text)[:300]
    except ValueError:
        return r.text[:300]
