"""Graphics over HTTP (main spec 6.4 and 6.5; milestone 9A). Viewing: any signed-in role.
Changes: admins (they are part of the site setup)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor
from devine_bms.graphics.service import GraphicsService
from devine_bms.setup.service import SetupError, SetupService


def build_graphics_router(get_setup: Callable[[], SetupService | None]) -> APIRouter:
    router = APIRouter()

    def graphics() -> GraphicsService:
        svc = get_setup()
        if svc is None:
            raise HTTPException(503, "graphics need the database")
        return GraphicsService(svc)

    async def run(call) -> Any:
        try:
            return await call
        except SetupError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    @router.get("/api/graphics/library")
    async def symbols(request: Request, svc: GraphicsService = Depends(graphics)) -> Any:
        require("viewer")(request)
        return await run(svc.library())

    @router.get("/api/graphics")
    async def list_graphics(request: Request, svc: GraphicsService = Depends(graphics)) -> Any:
        require("viewer")(request)
        return await run(svc.list())

    # Fixed paths before /api/setup/graphics/{name}.
    @router.post("/api/setup/graphics/sanitize")
    async def clean(body: dict = Body(...), _: Actor = Depends(require("admin")),
                    svc: GraphicsService = Depends(graphics)) -> Any:
        try:
            return svc.clean(body)
        except SetupError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    @router.put("/api/setup/graphics/symbols/{symbol_id}")
    async def put_symbol(symbol_id: str, body: dict = Body(...), check: bool = False,
                         client: Actor = Depends(require("admin")), svc: GraphicsService = Depends(graphics)) -> Any:
        return await run(svc.put_symbol(symbol_id, body, client, check))

    @router.delete("/api/setup/graphics/symbols/{symbol_id}")
    async def delete_symbol(symbol_id: str, client: Actor = Depends(require("admin")),
                            svc: GraphicsService = Depends(graphics)) -> Any:
        return await run(svc.delete_symbol(symbol_id, client))

    @router.put("/api/setup/graphics-tabs")
    async def put_tabs(body: dict = Body(...), client: Actor = Depends(require("admin")),
                       svc: GraphicsService = Depends(graphics)) -> Any:
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(n, str) for n in order):
            return JSONResponse({"ok": False, "error": "send {order: [graphic names]}"}, status_code=422)
        return await run(svc.put_tabs(order, client))

    @router.put("/api/setup/graphics/{name}")
    async def put(name: str, body: dict = Body(...), check: bool = False, client: Actor = Depends(require("admin")),
                  svc: GraphicsService = Depends(graphics)) -> Any:
        return await run(svc.put(name, body, client, check))

    @router.delete("/api/setup/graphics/{name}")
    async def delete(name: str, client: Actor = Depends(require("admin")),
                     svc: GraphicsService = Depends(graphics)) -> Any:
        return await run(svc.delete(name, client))

    @router.post("/api/setup/graphics/generate")
    async def generate(check: bool = False, client: Actor = Depends(require("admin")),
                       svc: GraphicsService = Depends(graphics)) -> Any:
        return await run(svc.generate(client, check))

    return router
