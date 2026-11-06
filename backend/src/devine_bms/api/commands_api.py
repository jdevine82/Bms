"""Commands over HTTP (main spec 5.6; milestone 7A). Operators and admins only; never a kiosk
on its own (an HMI unlocks commands with a person's PIN)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor
from devine_bms.commands.service import CommandError, CommandService


def build_commands_router(get_commands: Callable[[], CommandService | None]) -> APIRouter:
    router = APIRouter()

    def commands() -> CommandService:
        c = get_commands()
        if c is None:
            raise HTTPException(503, "commands need the database")
        return c

    def operator(request: Request) -> Actor:
        actor = require("operator")(request)
        if actor.username is None:
            raise HTTPException(403, "Commands need a person's login (on the HMI, their PIN).")
        return actor

    async def run(call) -> Any:
        try:
            return await call
        except CommandError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    @router.post("/api/points/{point_id}/command")
    async def command(point_id: uuid.UUID, body: dict = Body(...), actor: Actor = Depends(operator),
                      svc: CommandService = Depends(commands)) -> Any:
        hours = body.get("hours")
        return await run(svc.command(actor, point_id, body.get("value"), str(body.get("mode", "set")),
                                     float(hours) if hours is not None else None, bool(body.get("confirm"))))

    @router.post("/api/points/{point_id}/release")
    async def release(point_id: uuid.UUID, actor: Actor = Depends(operator),
                      svc: CommandService = Depends(commands)) -> Any:
        return await run(svc.release(actor, point_id))

    @router.get("/api/overrides")
    async def overrides(request: Request, svc: CommandService = Depends(commands)) -> Any:
        require("viewer")(request)
        names = {p.id: p for p in svc.runtime.config.points}
        return [{"pointId": str(pid), "point": names[pid].name, "dis": names[pid].dis, **o}
                for pid, o in sorted(svc.overrides.items(), key=lambda kv: kv[1]["endsAt"]) if pid in names]

    @router.get("/api/command-settings")
    async def get_settings(request: Request, svc: CommandService = Depends(commands)) -> Any:
        require("viewer")(request)
        return await svc.settings()

    @router.put("/api/command-settings")
    async def put_settings(body: dict = Body(...), actor: Actor = Depends(require("admin")),
                           svc: CommandService = Depends(commands)) -> Any:
        return await run(svc.save_settings(actor, body))

    return router
