"""The server's side of HMI takeover (milestone 11, decision 029).

- `HmiGate`: asks each HMI every 5 seconds whether it is in direct mode, and once before polling
  starts. While one is, the server's runtime is held: no polling, no writes (commands are refused
  with the reason), logic and schedules wait. It resumes when the HMI hands control back.
- The standby endpoints (token): the cache the HMI keeps (setup, PIN people, delivery settings)
  and the HMI's audit log from a direct-mode period.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from sqlalchemy import select

from devine_bms.db import models as db

log = logging.getLogger(__name__)
TOKEN_HEADER = "X-BMS-Standby-Token"


def token_ok(expected: str, given: str | None) -> bool:
    return bool(expected) and bool(given) and secrets.compare_digest(expected, given)


class HmiGate:
    def __init__(self, runtime, urls: list[str], token: str, every_s: float = 5.0, record=None) -> None:
        self.runtime = runtime
        self.urls = [u.rstrip("/") for u in urls if u.strip()]
        self.token = token
        self.every_s = every_s
        self.record = record  # audit
        self.direct: dict[str, dict] = {}  # url -> the HMI's state while it is in direct mode
        self.seen: dict[str, dict | None] = {}
        self._task: asyncio.Task | None = None

    async def check(self) -> None:
        states = await asyncio.gather(*(self._ask(u) for u in self.urls))
        for url, st in zip(self.urls, states, strict=True):
            self.seen[url] = st
            if st and st.get("mode") == "direct":
                self.direct[url] = st
            elif st is not None:  # answered, not direct: it has handed back (or never took over)
                self.direct.pop(url, None)
            # No answer: keep what we knew (an HMI in direct mode that cannot be reached still holds).
        if self.direct and not self.runtime.held:
            names = ", ".join(s.get("name", u) for u, s in self.direct.items())
            await self.runtime.hold(f"the HMI {names} is in direct mode: it drives the plant until control is handed back on its screen")
        elif not self.direct and self.runtime.held and (self.runtime.held_why or "").startswith("the HMI"):
            await self.runtime.resume()

    async def _ask(self, url: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{url}/api/standby/state")
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hmi gate")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("asking the HMIs failed")
            await asyncio.sleep(self.every_s)

    def summary(self) -> dict:
        return {"hmis": [{"url": u, "state": (self.seen.get(u) or {}).get("mode"), "name": (self.seen.get(u) or {}).get("name")}
                         for u in self.urls],
                "heldBy": [s.get("name", u) for u, s in self.direct.items()]}


async def cache_payload(sessions, site_name: str) -> dict:
    """What an HMI keeps to take over: the setup (an export), the people with an HMI PIN (no
    passwords) and the alarm delivery settings."""
    from devine_bms.setup.export import export_dict

    async with sessions() as session:
        config = await export_dict(session, site_name)
        config.pop("counters", None)  # the HMI does not count; and they would change the revision every minute
        site = await session.scalar(select(db.Site).where(db.Site.name == site_name))
        users = (await session.scalars(select(db.User).where(db.User.site_id == site.id, db.User.enabled.is_(True),
                                                             db.User.pin_hash.is_not(None)))).all()
        people = [{"username": u.username, "dis": u.dis, "role": u.role, "pinHash": u.pin_hash} for u in users]
        alarm_settings = dict(site.alarm_settings or {})
        command_settings = dict(site.command_settings or {})
    body = {"config": config, "people": people, "alarmSettings": alarm_settings, "commandSettings": command_settings}
    revision = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {**body, "revision": revision}


async def store_audit(sessions, site_name: str, hmi: str, entries: list[dict]) -> int:
    """The HMI's direct-mode audit entries, marked as from that HMI (main spec 9)."""
    async with sessions() as session, session.begin():
        site = await session.scalar(select(db.Site).where(db.Site.name == site_name))
        n = 0
        for e in entries:
            try:
                at = datetime.fromisoformat(e["at"])
            except (KeyError, TypeError, ValueError):
                continue
            session.add(db.AuditEntry(site_id=site.id, ts=at, client=f"hmi:{hmi}"[:64], username=e.get("username"),
                                      action=str(e.get("action", ""))[:32], kind=e.get("kind"), name=e.get("name"),
                                      summary=f"[HMI {hmi}, direct mode] {e.get('summary', '')}",
                                      before=e.get("before"), after=e.get("after")))
            n += 1
    return n


def build_router(get_sessions, get_site_name, get_token, get_gate):
    router = APIRouter()

    def guard(request: Request) -> None:
        if not token_ok(get_token(), request.headers.get(TOKEN_HEADER)):
            raise HTTPException(403, "the standby token is missing or wrong")

    @router.get("/api/standby/cache")
    async def cache(request: Request) -> Any:
        guard(request)
        return await cache_payload(get_sessions(), get_site_name())

    @router.post("/api/standby/audit")
    async def audit(request: Request, body: dict = Body(...)) -> Any:
        guard(request)
        entries = body.get("entries")
        if not isinstance(entries, list):
            raise HTTPException(422, "send {hmi, entries: [...]}")
        n = await store_audit(get_sessions(), get_site_name(), str(body.get("hmi") or "hmi"), entries)
        return {"ok": True, "stored": n}

    @router.get("/api/standby/gate")
    async def gate() -> Any:
        g = get_gate()
        return g.summary() if g else {"hmis": [], "heldBy": []}

    return router
