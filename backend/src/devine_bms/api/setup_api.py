"""Setup API (milestone 5A): read, change, rename and remove the site setup.

Reading is open, like the rest of the API. Changes are accepted only from the server
itself (127.0.0.1 or ::1) until logins exist (milestone 5 decision 2, main spec 7);
milestone 6 replaces this guard with the admin role.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from devine_bms.setup.service import KINDS, SetupError, SetupService

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def client_label(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    return f"{host} (for {forwarded})" if forwarded else host


def local_only(request: Request) -> str:
    """Refuse setup changes from anywhere but this machine. Returns the client label for the change log."""
    host = request.client.host if request.client else ""
    forwarded = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if host not in LOOPBACK or any(h not in LOOPBACK for h in forwarded):
        raise HTTPException(
            403, "Setup changes are only accepted from the server itself until logins exist (milestone 6).")
    return client_label(request)


def build_setup_router(get_service: Callable[[], SetupService | None]) -> APIRouter:
    router = APIRouter(prefix="/api/setup")

    def service() -> SetupService:
        svc = get_service()
        if svc is None:
            raise HTTPException(503, "setup needs the database (it is not available in this mode)")
        return svc

    async def run(call) -> Any:
        try:
            return await call
        except SetupError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    @router.get("/config")
    async def export(svc: SetupService = Depends(service)) -> Any:
        return await run(svc.export())

    @router.put("/config")
    async def import_config(data: dict = Body(...), check: bool = False, client: str = Depends(local_only),
                            svc: SetupService = Depends(service)) -> Any:
        return await run(svc.import_config(data, client, check))

    @router.put("/site")
    async def put_site(body: dict = Body(...), check: bool = False, client: str = Depends(local_only),
                       svc: SetupService = Depends(service)) -> Any:
        return await run(svc.put_site(body.get("site", {}), body.get("bacnet"), client, check))

    @router.put("/feeds")
    async def put_feeds(feeds: list[dict] = Body(...), check: bool = False, client: str = Depends(local_only),
                        svc: SetupService = Depends(service)) -> Any:
        return await run(svc.put_feeds(feeds, client, check))

    @router.get("/changes")
    async def changes(limit: int = Query(50, ge=1, le=1000), svc: SetupService = Depends(service)) -> Any:
        return await run(svc.changes(limit))

    @router.get("/{kind}")
    async def list_items(kind: str, svc: SetupService = Depends(service)) -> Any:
        if kind not in KINDS:
            raise HTTPException(404, f"unknown kind '{kind}'")
        return (await svc.export()).get(kind, [])

    @router.get("/{kind}/{name}")
    async def get_item(kind: str, name: str, svc: SetupService = Depends(service)) -> Any:
        if kind not in KINDS:
            raise HTTPException(404, f"unknown kind '{kind}'")
        for item in (await svc.export()).get(kind, []):
            if item["name"] == name:
                return item
        raise HTTPException(404, f"no {kind[:-1]} '{name}'")

    @router.put("/{kind}/{name}")
    async def put_item(kind: str, name: str, item: dict = Body(...), check: bool = False,
                       client: str = Depends(local_only), svc: SetupService = Depends(service)) -> Any:
        return await run(svc.put_item(kind, name, item, client, check))

    @router.delete("/{kind}/{name}")
    async def delete_item(kind: str, name: str, check: bool = False, client: str = Depends(local_only),
                          svc: SetupService = Depends(service)) -> Any:
        return await run(svc.delete_item(kind, name, client, check))

    @router.post("/{kind}/{name}/rename")
    async def rename_item(kind: str, name: str, body: dict = Body(...), check: bool = False,
                          client: str = Depends(local_only), svc: SetupService = Depends(service)) -> Any:
        new_name = body.get("newName")
        if not isinstance(new_name, str) or not new_name:
            raise HTTPException(422, "give the new name as {\"newName\": \"...\"}")
        return await run(svc.rename_item(kind, name, new_name, client, check))

    return router
