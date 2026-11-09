"""Maintenance counters over HTTP (main spec 5.10).

`GET /api/counters` (any signed-in role): every counted piece of equipment with its run hours,
starts, the counts since the last service, its service interval and whether it is due.
`POST /api/counters/{equip}/service-done` (admins): the service was done; the "since service"
counts start again and the reminder clears. Audit-logged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor
from devine_bms.counters.engine import CounterEngine


def build_counters_router(get_engine: Callable[[], CounterEngine | None]) -> APIRouter:
    router = APIRouter()

    def engine() -> CounterEngine:
        e = get_engine()
        if e is None:
            raise HTTPException(404, "counters need the database")
        return e

    @router.get("/api/counters")
    async def counters(equip: str | None = None, _: Actor = Depends(require("viewer")),
                       e: CounterEngine = Depends(engine)) -> Any:
        rows = e.status()
        return [r for r in rows if r["equip"] == equip] if equip else rows

    @router.post("/api/counters/{equip}/service-done")
    async def service_done(equip: str, actor: Actor = Depends(require("admin")),
                           e: CounterEngine = Depends(engine)) -> Any:
        try:
            return await e.service_done(equip, actor)
        except LookupError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    return router
