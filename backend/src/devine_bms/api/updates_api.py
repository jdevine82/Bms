"""Updates over HTTP (milestone 12b, decision 031).

- `GET /api/updates` (admins): the version, how this machine updates, the agent's progress, the
  HMIs and their versions.
- `POST /api/updates/upload` (admins): the release file as the request body.
- `POST /api/updates/report` (the host's updater agent, X-BMS-Update-Token): the result.
- `POST /api/standby/update` (the site server, X-BMS-Standby-Token): a standby HMI receives the
  release the server has just installed.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor
from devine_bms.standby.server import TOKEN_HEADER, token_ok
from devine_bms.updates.service import UPDATE_TOKEN_HEADER, UpdateError, UpdateService


def build_updates_router(get_svc: Callable[[], UpdateService | None]) -> APIRouter:
    router = APIRouter()

    def svc() -> UpdateService:
        s = get_svc()
        if s is None:
            raise HTTPException(404, "updates need the database")
        return s

    def failed(exc: UpdateError) -> JSONResponse:
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status)

    @router.get("/api/updates")
    async def status(_: Actor = Depends(require("admin")), s: UpdateService = Depends(svc)) -> Any:
        return await s.status()

    @router.post("/api/updates/upload")
    async def upload(request: Request, actor: Actor = Depends(require("admin")),
                     s: UpdateService = Depends(svc)) -> Any:
        try:
            return await s.upload(actor, request.stream())
        except UpdateError as exc:
            return failed(exc)

    @router.post("/api/updates/report")
    async def report(request: Request, body: dict = Body(...), s: UpdateService = Depends(svc)) -> Any:
        given = request.headers.get(UPDATE_TOKEN_HEADER) or ""
        if not (s.cfg.updater_token and secrets.compare_digest(given, s.cfg.updater_token)):
            raise HTTPException(403, "the update token is missing or wrong")
        return await s.report(body)

    @router.post("/api/standby/update")
    async def standby_update(request: Request, s: UpdateService = Depends(svc)) -> Any:
        if not token_ok(s.cfg.standby_token, request.headers.get(TOKEN_HEADER)):
            raise HTTPException(403, "the standby token is missing or wrong")
        if s.mode != "standby":
            raise HTTPException(404, "this is not an HMI in standby mode")
        try:
            path, version = await s.receive(request.stream())
            return await s.stage_pi(Actor(client="site-server", role="admin", username=None, dis="Site server"),
                                    path, version, "sent by the site server")
        except UpdateError as exc:
            return failed(exc)

    return router
