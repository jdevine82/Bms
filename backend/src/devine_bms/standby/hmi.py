"""The Pi HMI's standby engine (milestone 11; main spec 9, decision 029).

Standby: the Pi keeps a cached copy of the site from the server, polls nothing, sends nothing on
any bus, listens to its serial buses, and watches the server's heartbeat. When the heartbeat has
been lost for 30 seconds and every serial bus is clear, it takes over (direct mode): it polls
and commands with its own connections, raises a Critical alarm, and runs no logic or schedules.
When the server has been healthy for 5 minutes an operator can hand control back (failback):
the Pi releases what it set, stops, sends its audit log to the server and returns to standby.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from devine_bms import __version__
from devine_bms.db import models as db
from devine_bms.standby.listen import SerialListener
from devine_bms.standby.server import TOKEN_HEADER

log = logging.getLogger(__name__)

HEARTBEAT_S = 5.0
REFRESH_S = 60.0


class StandbyError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def parse_ports(text: str) -> dict[str, str]:
    """BMS_PI_PORTS: "rs485_1=/dev/ttyBMS-rs485-1,trunk1=/dev/ttyBMS-trunk1"."""
    out = {}
    for item in (text or "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def localise(config: dict, ports: dict[str, str], mstp_host: str = "127.0.0.1") -> dict:
    """The server's setup as the Pi uses it: the Pi's own adapters for the serial buses, and on
    every MS/TP trunk the Pi's own bms-mstp service as MAC 1 (main spec 9)."""
    config = json.loads(json.dumps(config))
    for bus in config.get("buses") or []:
        if bus.get("name") in ports and "port" in bus and bus.get("type") in ("modbus_rtu", "bacnet_mstp"):
            bus["port"] = ports[bus["name"]]
        if bus.get("type") == "bacnet_mstp":
            bus["mac"] = 1
            port = str(bus.get("service", ":47900")).rsplit(":", 1)[-1]
            bus["service"] = f"{mstp_host}:{port}"
            bus.pop("router", None)  # the router is the server's job
    return config


async def apply_cache(sessions, cache: dict, ports: dict[str, str]) -> str:
    """Put the cached site into the Pi's database (PostgreSQL in RAM): the setup, the people with
    an HMI PIN (PIN only; they cannot log in with a password here), delivery and override times."""
    from devine_bms.auth.passwords import hash_password
    from devine_bms.config.sync import sync_config
    from devine_bms.config.validate import parse_config

    config, _warnings = parse_config(localise(cache["config"], ports))
    async with sessions() as session, session.begin():
        report = await sync_config(session, config)
        site = await session.get(db.Site, report.site_id)
        site.alarm_settings = cache.get("alarmSettings") or {}
        site.command_settings = cache.get("commandSettings") or {}
        users = {u.username: u for u in (await session.scalars(select(db.User).where(db.User.site_id == site.id))).all()}
        wanted = {p["username"] for p in cache.get("people") or []}
        for p in cache.get("people") or []:
            u = users.get(p["username"])
            if u is None:
                import secrets

                u = db.User(site_id=site.id, username=p["username"], dis=p["dis"], role=p["role"],
                            password_hash=hash_password(secrets.token_urlsafe(24)))
                session.add(u)
            u.dis, u.role, u.pin_hash, u.enabled = p["dis"], p["role"], p["pinHash"], True
        for name, u in users.items():
            if name not in wanted:
                u.enabled = False
    return config.site.name


class StandbyService:
    def __init__(self, runtime, sessions, site_name: str, settings, auth, commands, alarms, record=None,
                 heartbeat_s: float = HEARTBEAT_S, refresh_s: float = REFRESH_S, clock=time.monotonic) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.site_name = site_name
        self.cfg = settings
        self.auth = auth
        self.commands = commands
        self.alarms = alarms
        self.record = record
        self.heartbeat_s = heartbeat_s
        self.refresh_s = refresh_s
        self.clock = clock
        self.ports = parse_ports(getattr(settings, "pi_ports", ""))
        self.mode = "standby"
        self.server_ok = False
        self.last_ok: float = clock()  # counts from start: a server that never answers is "lost" after 30 s
        self.ok_since: float | None = None
        self.last_refresh = -1e9
        self.revision: str | None = None
        self.took_over_at: datetime | None = None
        self.audit_pending: list[dict] = []  # direct-mode entries not yet accepted by the server
        self.listeners: list[SerialListener] = []
        self.note: str | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ---------- life ----------

    async def start(self) -> None:
        cache = self._read_cache()
        if cache is not None:
            self.revision = cache.get("revision")
        self._start_listeners()
        self._task = asyncio.create_task(self._loop(), name="standby")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        for lst in self.listeners:
            lst.stop()

    def _start_listeners(self) -> None:
        for lst in self.listeners:
            lst.stop()
        self.listeners = []
        for bus in self.runtime.config.buses:
            kind = getattr(bus.settings, "type", "")
            port = getattr(bus.settings, "port", None)
            if kind in ("modbus_rtu", "bacnet_mstp") and isinstance(port, str):
                lst = SerialListener(bus.name, kind, port, getattr(bus.settings, "baudrate", 9600), self.clock)
                lst.start()
                self.listeners.append(lst)

    # ---------- the heartbeat ----------

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("standby check failed")
            await asyncio.sleep(self.heartbeat_s)

    async def _get(self, path: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=3.0) as client:
            return await client.get(f"{self.cfg.server_url.rstrip('/')}{path}",
                                    headers={TOKEN_HEADER: self.cfg.standby_token})

    async def tick(self) -> None:
        async with self._lock:
            now = self.clock()
            try:
                ok = (await self._get("/api/health")).status_code == 200
            except httpx.HTTPError:
                ok = False
            if ok:
                if not self.server_ok:
                    self.ok_since = now
                    log.info("the site server answers")
                self.server_ok, self.last_ok = True, now
                if self.mode == "standby" and now - self.last_refresh >= self.refresh_s:
                    await self.refresh_cache()
                if self.audit_pending:
                    await self.send_audit()
            else:
                if self.server_ok:
                    log.warning("the site server does not answer")
                self.server_ok, self.ok_since = False, None
            if self.mode == "standby" and not self.server_ok and now - self.last_ok >= self.cfg.takeover_after_s:
                blocked = [lst for lst in self.listeners if not lst.clear(self.cfg.bus_quiet_s)]
                if blocked:
                    names = ", ".join(lst.bus for lst in blocked)
                    self.note = f"The server is not answering, but these buses are not clear yet: {names}"
                    self.alarms.set_system("bus-not-clear", True, "warning",
                                           f"HMI cannot take over yet: bus not clear ({names})", "hmi", self.cfg.hmi_name)
                else:
                    self.alarms.set_system("bus-not-clear", False)
                    await self._take_over(now)

    async def refresh_cache(self) -> bool:
        """Fetch the site from the server; write the cache file and load it only when it changed."""
        self.last_refresh = self.clock()
        try:
            r = await self._get("/api/standby/cache")
        except httpx.HTTPError as exc:
            log.warning("fetching the cache failed: %s", exc)
            return False
        if r.status_code != 200:
            self.note = f"The server refused the cache ({r.status_code}): check BMS_STANDBY_TOKEN on both"
            return False
        cache = r.json()
        if cache.get("revision") == self.revision:
            return False
        await asyncio.to_thread(_write_atomic, Path(self.cfg.standby_cache), json.dumps(cache),
                                getattr(self.cfg, "state_rw_cmd", ""), getattr(self.cfg, "state_ro_cmd", ""))
        await apply_cache(self.sessions, cache, self.ports)
        await self.runtime.reload()  # still held: only the setup changes
        self._start_listeners()
        self.revision = cache.get("revision")
        log.info("standby cache updated (revision %s)", self.revision)
        return True

    def _read_cache(self) -> dict | None:
        path = Path(self.cfg.standby_cache)
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None

    # ---------- takeover and failback ----------

    async def _take_over(self, now: float) -> None:
        lost = round(now - self.last_ok)
        for lst in self.listeners:
            lst.stop()  # the ports are the pollers' now
        if self.cfg.mstp_start_cmd:
            await _run(self.cfg.mstp_start_cmd)
        await self.runtime.resume()
        self.mode, self.took_over_at, self.note = "direct", datetime.now(UTC), None
        self.alarms.set_system("direct-mode", True, "critical",
                               f"Direct mode: the HMI {self.cfg.hmi_name} has taken over from the site server",
                               "hmi", self.cfg.hmi_name)
        log.warning("DIRECT MODE: the server has not answered for %s s; the HMI drives the buses", lost)
        await self._audit("takeover", f"the HMI took over (direct mode): the server had not answered for {lost} s")

    def can_failback(self) -> bool:
        return (self.mode == "direct" and self.server_ok and self.ok_since is not None
                and self.clock() - self.ok_since >= self.cfg.failback_after_s)

    async def failback(self, actor) -> dict:
        async with self._lock:
            if self.mode != "direct":
                raise StandbyError("the HMI is not in direct mode")
            if not self.can_failback():
                wait = self.cfg.failback_after_s - (self.clock() - self.ok_since) if self.ok_since is not None else None
                raise StandbyError("the server is not back yet" if wait is None else
                                   f"the server has to be healthy for {round(wait)} more seconds")
            released = []
            for point_id in list(self.commands.overrides):
                try:
                    await self.commands.release(actor, point_id)
                    released.append(str(point_id))
                except Exception as exc:  # noqa: BLE001 -- handing back goes on; the server takes over
                    log.warning("releasing a hand mode on %s failed: %s", point_id, exc)
            await self.runtime.hold("standby: the site server drives the buses")
            if self.cfg.mstp_stop_cmd:
                await _run(self.cfg.mstp_stop_cmd)
            await self._audit("failback", f"control handed back to the server by {actor.username or actor.label}"
                              + (f"; {len(released)} hand modes released" if released else ""), actor)
            await self._collect_audit()
            self.mode, self.note = "standby", None
            self.alarms.set_system("direct-mode", False)
            self._start_listeners()
            sent = await self.send_audit()
            return {"ok": True, "mode": self.mode, "released": len(released), "auditSent": sent}

    async def _collect_audit(self) -> None:
        if self.took_over_at is None:
            return
        async with self.sessions() as session:
            rows = (await session.scalars(select(db.AuditEntry).where(db.AuditEntry.ts >= self.took_over_at)
                                          .order_by(db.AuditEntry.ts))).all()
        self.audit_pending += [{"at": r.ts.isoformat(), "username": r.username, "action": r.action, "kind": r.kind,
                                "name": r.name, "summary": r.summary, "before": r.before, "after": r.after}
                               for r in rows]
        self.took_over_at = None

    async def send_audit(self) -> int:
        if not self.audit_pending:
            return 0
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(f"{self.cfg.server_url.rstrip('/')}/api/standby/audit",
                                      headers={TOKEN_HEADER: self.cfg.standby_token},
                                      json={"hmi": self.cfg.hmi_name, "entries": self.audit_pending})
        except httpx.HTTPError:
            return 0
        if r.status_code != 200:
            return 0
        n = len(self.audit_pending)
        self.audit_pending = []
        return n

    async def _audit(self, action: str, summary: str, actor=None) -> None:
        if self.record is not None:
            from devine_bms.auth.service import Actor

            await self.record(actor or Actor(client="hmi", role="admin", username=None, dis="HMI"), action, summary,
                              kind="standby", name=self.cfg.hmi_name)

    # ---------- reading ----------

    def state(self) -> dict[str, Any]:
        now = self.clock()
        healthy = round(now - self.ok_since) if self.server_ok and self.ok_since is not None else None
        return {"name": self.cfg.hmi_name, "mode": self.mode, "serverOk": self.server_ok,
                "serverUrl": self.cfg.server_url.rstrip("/"),
                "version": __version__, "lastUpdate": _last_update(self.cfg),
                "serverHealthyS": healthy,
                "serverLostS": round(now - self.last_ok) if not self.server_ok else None,
                "takeoverAfterS": self.cfg.takeover_after_s, "failbackAfterS": self.cfg.failback_after_s,
                "canFailback": self.can_failback(),
                "tookOverAt": self.took_over_at.isoformat() if self.took_over_at else None,
                "buses": [lst.state(self.cfg.bus_quiet_s) for lst in self.listeners],
                "cacheRevision": self.revision, "note": self.note, "auditPending": len(self.audit_pending)}


async def _run(cmd: str) -> None:
    import shlex

    proc = await asyncio.create_subprocess_exec(*shlex.split(cmd), stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        log.warning("%s failed: %s", cmd, err.decode(errors="replace").strip())


def _write_atomic(path: Path, text: str, rw_cmd: str = "", ro_cmd: str = "") -> None:
    from devine_bms.standalone.state import write_atomic

    write_atomic(path, text, rw_cmd, ro_cmd)  # the config partition is read-only except while writing


def _last_update(cfg) -> dict | None:
    """The result of the last update of this Pi (milestone 12b), written by bms-pi-update."""
    try:
        return json.loads((Path(getattr(cfg, "update_dir", "")) / "last-update.json").read_text())
    except (OSError, ValueError, TypeError):
        return None
