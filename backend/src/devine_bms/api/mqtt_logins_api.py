"""Device logins on the site's own MQTT broker (Mosquitto in bms-app), for admins.

The BMS never keeps these passwords: it makes a strong one, hands it to the root helper
(`bms-mqtt-users`, through sudo, on standard input) and shows it once to type into the device.
Every change is audit-logged (no passwords). Not available where the helper is not installed
(development machines, Pis).
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import shlex
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class LoginError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message, self.status = message, status


async def _helper(cmd: str, *args: str, stdin: str | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(*shlex.split(cmd), *args, stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate((stdin + "\n").encode() if stdin else b""), 30)
    if proc.returncode != 0:
        text = err.decode(errors="replace").strip().removeprefix("bms-mqtt-users: ")
        raise LoginError(text or f"the broker helper failed ({proc.returncode})",
                         404 if proc.returncode == 3 else 422 if proc.returncode == 2 else 500)
    return out.decode()


def build_mqtt_logins_router(get_cmd: Callable[[], str], get_record: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    def cmd() -> str:
        c = get_cmd()
        if not c:
            raise HTTPException(404, "device logins are managed only on a site server's own broker")
        return c

    async def record(actor: Actor, summary: str, name: str) -> None:
        rec = get_record()
        if rec is not None:
            await rec(actor, "mqtt-login", summary, kind="mqtt", name=name)

    async def names(c: str) -> list[str]:
        return list(json.loads(await _helper(c, "list")))

    def failed(exc: LoginError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status)

    @router.get("/api/mqtt-logins")
    async def list_logins(_: Actor = Depends(require("admin"))) -> Any:
        c = get_cmd()
        if not c:
            return {"available": False, "logins": []}
        try:
            return {"available": True, "logins": await names(c)}
        except LoginError as exc:
            return failed(exc)

    @router.post("/api/mqtt-logins")
    async def add_login(body: dict = Body(...), actor: Actor = Depends(require("admin"))) -> Any:
        c = cmd()
        name = str(body.get("name", "")).strip()
        try:
            if not NAME.match(name):
                raise LoginError("a login name is letters, digits, . _ - (up to 64), starting with a letter or digit")
            if name == "bms":
                raise LoginError("'bms' is the BMS's own login")
            if name in await names(c):
                raise LoginError(f"there is already a login '{name}': reset its password instead", 409)
            password = secrets.token_urlsafe(24)
            await _helper(c, "add", name, stdin=password)
        except LoginError as exc:
            return failed(exc)
        await record(actor, f"added the MQTT device login {name}", name)
        return {"ok": True, "name": name, "password": password}

    @router.post("/api/mqtt-logins/{name}/reset")
    async def reset_login(name: str, actor: Actor = Depends(require("admin"))) -> Any:
        c = cmd()
        try:
            if name not in await names(c):
                raise LoginError(f"no login '{name}'", 404)
            password = secrets.token_urlsafe(24)
            await _helper(c, "add", name, stdin=password)
        except LoginError as exc:
            return failed(exc)
        await record(actor, f"gave the MQTT device login {name} a new password", name)
        return {"ok": True, "name": name, "password": password}

    @router.delete("/api/mqtt-logins/{name}")
    async def remove_login(name: str, actor: Actor = Depends(require("admin"))) -> Any:
        c = cmd()
        try:
            await _helper(c, "remove", name)
        except LoginError as exc:
            return failed(exc)
        await record(actor, f"removed the MQTT device login {name}", name)
        return {"ok": True}

    return router
