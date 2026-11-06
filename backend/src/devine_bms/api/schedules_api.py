"""Schedules and holiday calendars over HTTP (main spec 5.4; milestone 7B). Viewing: any signed-in
role. Changes: operators and admins (milestone 7 decision 7), never a kiosk on its own."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor
from devine_bms.schedules.service import ScheduleError, ScheduleService


def build_schedules_router(get_schedules: Callable[[], ScheduleService | None]) -> APIRouter:
    router = APIRouter()

    def schedules() -> ScheduleService:
        s = get_schedules()
        if s is None:
            raise HTTPException(503, "schedules need the database")
        return s

    def editor(request: Request) -> Actor:
        actor = require("operator")(request)
        if actor.username is None:
            raise HTTPException(403, "Changing schedules needs a person's login (on the HMI, their PIN).")
        return actor

    async def run(call) -> Any:
        try:
            return await call
        except ScheduleError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    @router.get("/api/schedules")
    async def list_schedules(request: Request, svc: ScheduleService = Depends(schedules)) -> Any:
        require("viewer")(request)
        return [svc.describe(s) for s in sorted(svc.schedules.values(), key=lambda s: s.dis.lower())]

    @router.get("/api/schedules/{name}")
    async def one(name: str, request: Request, svc: ScheduleService = Depends(schedules)) -> Any:
        require("viewer")(request)
        if name not in svc.schedules:
            raise HTTPException(404, f"no schedule '{name}'")
        return svc.describe(svc.schedules[name])

    @router.post("/api/schedules")
    async def add(body: dict = Body(...), actor: Actor = Depends(editor), svc: ScheduleService = Depends(schedules)) -> Any:
        if body.get("name") in svc.schedules:
            return JSONResponse({"ok": False, "error": f"there is already a schedule '{body['name']}'"}, status_code=409)
        return await run(svc.save(actor, body))

    @router.put("/api/schedules/{name}")
    async def change(name: str, body: dict = Body(...), actor: Actor = Depends(editor),
                     svc: ScheduleService = Depends(schedules)) -> Any:
        if name not in svc.schedules:
            raise HTTPException(404, f"no schedule '{name}'")
        return await run(svc.save(actor, body, name))

    @router.delete("/api/schedules/{name}")
    async def remove(name: str, actor: Actor = Depends(editor), svc: ScheduleService = Depends(schedules)) -> Any:
        async def go():
            await svc.remove(actor, name)
            return {"ok": True}
        return await run(go())

    @router.post("/api/schedules/{name}/controller/push")
    async def push(name: str, actor: Actor = Depends(editor), svc: ScheduleService = Depends(schedules)) -> Any:
        return await run(svc.push(actor, name))

    @router.post("/api/schedules/{name}/controller/check")
    async def check(name: str, request: Request, svc: ScheduleService = Depends(schedules)) -> Any:
        require("viewer")(request)
        return await run(svc.check(name))

    @router.post("/api/schedules/{name}/controller/accept")
    async def accept(name: str, actor: Actor = Depends(editor), svc: ScheduleService = Depends(schedules)) -> Any:
        return await run(svc.accept(actor, name))

    @router.get("/api/holiday-calendars")
    async def calendars(request: Request, svc: ScheduleService = Depends(schedules)) -> Any:
        require("viewer")(request)
        return [c.model_dump(mode="json", by_alias=True) for c in sorted(svc.calendars.values(), key=lambda c: c.dis.lower())]

    @router.put("/api/holiday-calendars/{name}")
    async def put_calendar(name: str, body: dict = Body(...), actor: Actor = Depends(editor),
                           svc: ScheduleService = Depends(schedules)) -> Any:
        return await run(svc.save_calendar(actor, body, name))

    @router.delete("/api/holiday-calendars/{name}")
    async def remove_calendar(name: str, actor: Actor = Depends(editor), svc: ScheduleService = Depends(schedules)) -> Any:
        async def go():
            await svc.remove_calendar(actor, name)
            return {"ok": True}
        return await run(go())

    return router
