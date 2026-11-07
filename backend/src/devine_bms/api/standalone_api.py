"""Standalone mode over HTTP (milestone 11a). `GET /api/mode` tells the screens which mode runs.
Reading and Export now: any signed-in role; settings and network: admins. The setup info (the
one-time code) is only given to the Pi itself, while there are no accounts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from devine_bms.api.auth_api import request_client, require
from devine_bms.auth.service import Actor
from devine_bms.standalone.service import StandaloneError, StandaloneService


def build_standalone_router(get_svc: Callable[[], StandaloneService | None], get_auth: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    def svc() -> StandaloneService:
        s = get_svc()
        if s is None:
            raise HTTPException(404, "this site runs in full mode")
        return s

    def run(fn) -> Any:
        try:
            return fn()
        except StandaloneError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    @router.get("/api/mode")
    async def mode() -> Any:
        return {"mode": "standalone" if get_svc() is not None else "full"}

    @router.get("/api/standalone/setup-info")
    async def setup_info(request: Request, s: StandaloneService = Depends(svc)) -> Any:
        """For the Pi's own screen at first boot: the addresses and the one-time setup code."""
        auth = get_auth()
        if auth is None or not auth.is_loopback(request_client(request)):
            raise HTTPException(403, "only on the Pi itself")
        if await auth.has_users():
            return {"setup": False}
        port = request.url.port or 80
        return {"setup": True, "code": auth.setup_code, "addresses": s.addresses(), "port": port}

    @router.get("/api/standalone/status")
    async def status(_: Actor = Depends(require("viewer")), s: StandaloneService = Depends(svc)) -> Any:
        return s.summary()

    @router.put("/api/standalone/settings")
    async def settings(body: dict = Body(...), actor: Actor = Depends(require("admin")),
                       s: StandaloneService = Depends(svc)) -> Any:
        try:
            new = await s.save_settings(body)
        except StandaloneError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)
        if s.record is not None:
            await s.record(actor, "settings", "changed the standalone settings", kind="standalone", after=new)
        return {"ok": True, "settings": new}

    @router.post("/api/standalone/export")
    async def export(_: Actor = Depends(require("viewer")), s: StandaloneService = Depends(svc)) -> Any:
        return await s.export()

    @router.post("/api/standalone/save")
    async def save(_: Actor = Depends(require("admin")), s: StandaloneService = Depends(svc)) -> Any:
        ok = await s.keeper.save("asked")
        return {"ok": ok, "error": s.keeper.last_error}

    @router.get("/api/standalone/log.csv", response_class=PlainTextResponse)
    async def log_csv(hours: float = 24, _: Actor = Depends(require("viewer")),
                      s: StandaloneService = Depends(svc)) -> Any:
        if not 0 < hours <= 168:
            raise HTTPException(422, "hours is 1 to 168")
        return PlainTextResponse(await s.csv_text(hours), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="log-{int(hours)}h.csv"'})

    @router.get("/api/standalone/network")
    async def network(_: Actor = Depends(require("admin")), s: StandaloneService = Depends(svc)) -> Any:
        return run(s.network)

    @router.put("/api/standalone/network")
    async def set_network(body: dict = Body(...), actor: Actor = Depends(require("admin")),
                          s: StandaloneService = Depends(svc)) -> Any:
        result = run(lambda: s.set_network(body))
        if s.record is not None and isinstance(result, dict):
            await s.record(actor, "settings", f"changed the network settings ({body.get('mode')})", kind="network",
                           after=body)
        return result

    return router
