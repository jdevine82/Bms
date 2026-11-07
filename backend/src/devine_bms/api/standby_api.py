"""HMI standby and takeover over HTTP (milestone 11, decision 029).

Server: `GET /api/standby/cache` and `POST /api/standby/audit` (the shared standby token, for the
HMIs), `GET /api/standby/gate` (signed in: which HMIs there are, and whether one holds the site).
Pi: `GET /api/standby/state` (public, readable from the server's pages: the screen asks the Pi
while the server is down) and `POST /api/standby/failback` (an operator, after the PIN).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor
from devine_bms.standby.hmi import StandbyError, StandbyService
from devine_bms.standby.server import build_router as build_server_router

# Any page may read the Pi's standby state (it holds no secrets): the server's HMI page asks the
# Pi on localhost, a private address, so the Private Network Access preflight is answered too.
OPEN = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET",
        "Access-Control-Allow-Private-Network": "true", "Cache-Control": "no-store"}


def build_standby_router(get_sessions, get_site_name, get_token, get_gate,
                         get_standby: Callable[[], StandbyService | None]) -> APIRouter:
    router = build_server_router(get_sessions, get_site_name, get_token, get_gate)

    def svc() -> StandbyService:
        s = get_standby()
        if s is None:
            raise HTTPException(404, "this is not an HMI in standby mode")
        return s

    @router.get("/api/standby/state")
    async def state(s: StandbyService = Depends(svc)) -> Any:
        return JSONResponse(s.state(), headers=OPEN)

    @router.options("/api/standby/state")
    async def state_preflight() -> Any:
        return Response(status_code=204, headers={**OPEN, "Access-Control-Allow-Headers": "*"})

    @router.post("/api/standby/failback")
    async def failback(actor: Actor = Depends(require("operator")), s: StandbyService = Depends(svc)) -> Any:
        try:
            return await s.failback(actor)
        except StandbyError as exc:
            return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status)

    return router

