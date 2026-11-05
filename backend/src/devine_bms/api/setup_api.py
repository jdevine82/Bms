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

from devine_bms.setup.equip_templates import EquipTemplateService
from devine_bms.setup.service import KINDS, SetupError, SetupService
from devine_bms.setup.templates import TemplateLibrary

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


def build_setup_router(get_service: Callable[[], SetupService | None],
                       get_runtime: Callable[[], Any] = lambda: None,
                       get_discover: Callable[[], Any] = lambda: None) -> APIRouter:
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
        try:
            data = await svc.export()
        except SetupError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)
        return JSONResponse(data, headers={"X-Setup-Revision": svc.revision(data)})

    @router.post("/commit")
    async def commit(body: dict = Body(...), check: bool = False, client: str = Depends(local_only),
                     svc: SetupService = Depends(service)) -> Any:
        """Save a whole edited setup in one checked change: the wizard's go-live and the mapping
        table. body: config, revision (from X-Setup-Revision when it was read), summary."""
        if not isinstance(body.get("config"), dict) or not isinstance(body.get("revision"), str):
            raise HTTPException(422, "give the edited setup as config and the revision it was read at")
        summary = str(body.get("summary") or "saved an edited setup")
        return await run(svc.import_config(body["config"], client, check, body["revision"], summary, "commit"))

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

    # ---------- device templates (before the /{kind} routes, which would match "templates") ----------

    @router.get("/templates")
    async def list_templates(svc: SetupService = Depends(service)) -> Any:
        return await run(TemplateLibrary(svc).list())

    @router.get("/templates/{template_id}")
    async def get_template(template_id: str, version: str | None = None,
                           svc: SetupService = Depends(service)) -> Any:
        async def get():
            library = TemplateLibrary(svc)
            template, source = await library.get(template_id, version)
            return {"source": source, "versions": await library.versions(template_id),
                    "template": template.model_dump(mode="json", by_alias=True)}
        return await run(get())

    @router.post("/templates")
    async def save_template(data: dict = Body(...), check: bool = False, client: str = Depends(local_only),
                            svc: SetupService = Depends(service)) -> Any:
        return await run(TemplateLibrary(svc).save(data, client, check))

    @router.delete("/templates/{template_id}")
    async def delete_template(template_id: str, version: str | None = None, client: str = Depends(local_only),
                              svc: SetupService = Depends(service)) -> Any:
        return await run(TemplateLibrary(svc).delete(template_id, version, client))

    @router.post("/templates/{template_id}/apply")
    async def apply_template(template_id: str, body: dict = Body(...), check: bool = False,
                             client: str = Depends(local_only), svc: SetupService = Depends(service)) -> Any:
        return await run(TemplateLibrary(svc).apply(template_id, body, client, check))

    # ---------- equipment templates (main spec 5.1.2) ----------

    @router.get("/equip-templates")
    async def list_equip_templates(svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).list())

    @router.get("/equip-templates/{template_id}")
    async def get_equip_template(template_id: str, version: str | None = None,
                                 svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).get(template_id, version))

    @router.get("/equip-templates/{template_id}/diff")
    async def diff_equip_template(template_id: str, old: str = Query(..., alias="from"), new: str = Query(..., alias="to"),
                                  svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).diff(template_id, old, new))

    @router.post("/equip-templates")
    async def save_equip_template(body: dict = Body(...), check: bool = False, client: str = Depends(local_only),
                                  svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).save(body, client, check))

    @router.delete("/equip-templates/{template_id}")
    async def delete_equip_template(template_id: str, version: str | None = None, client: str = Depends(local_only),
                                    svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).delete(template_id, version, client))

    @router.post("/equip-templates/{template_id}/create")
    async def create_from_template(template_id: str, body: dict = Body(...), check: bool = False,
                                   client: str = Depends(local_only), svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).create(template_id, body, client, check))

    @router.post("/equip-templates/{template_id}/rollout")
    async def rollout(template_id: str, body: dict = Body(...), check: bool = False,
                      client: str = Depends(local_only), svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).rollout(template_id, body, client, check))

    @router.get("/instances/{kind}/{name}")
    async def instance(kind: str, name: str, svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).instance(kind, name))

    @router.post("/instances/{kind}/{name}/reset")
    async def reset_override(kind: str, name: str, body: dict = Body(...), client: str = Depends(local_only),
                             svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).reset_override(kind, name, body.get("role", ""),
                                                                  body.get("field", ""), client))

    @router.post("/instances/{kind}/{name}/unlink")
    async def unlink(kind: str, name: str, client: str = Depends(local_only),
                     svc: SetupService = Depends(service)) -> Any:
        return await run(EquipTemplateService(svc).unlink(kind, name, client))

    # ---------- test reads (read-only, but they send traffic to devices) ----------

    @router.post("/test-read")
    async def test_read_route(body: dict = Body(...), client: str = Depends(local_only),
                              svc: SetupService = Depends(service)) -> Any:
        from devine_bms.setup.testread import test_read
        return await run(test_read(get_runtime(), svc, body))

    # ---------- Discover (main spec 5.1.3) ----------

    def discover():
        manager = get_discover()
        if manager is None:
            raise HTTPException(503, "Discover needs the database (it is not available in this mode)")
        return manager

    @router.post("/discover")
    async def start_scan(body: dict = Body(...), client: str = Depends(local_only), manager=Depends(discover)) -> Any:
        params = {k: v for k, v in body.items() if k != "kind"}

        async def start():
            return (await manager.start(body.get("kind", ""), params, client)).as_dict()
        return await run(start())

    @router.get("/discover")
    async def list_scans(manager=Depends(discover)) -> Any:
        return [{k: v for k, v in s.as_dict().items() if k != "results"} | {"found": len(s.results)}
                for s in manager.scans.values()]

    @router.get("/discover/{scan_id}")
    async def get_scan(scan_id: str, manager=Depends(discover)) -> Any:
        async def get():
            return manager.get(scan_id).as_dict()
        return await run(get())

    @router.delete("/discover/{scan_id}")
    async def stop_scan(scan_id: str, client: str = Depends(local_only), manager=Depends(discover)) -> Any:
        async def stop():
            return (await manager.stop(scan_id)).as_dict()
        return await run(stop())

    @router.post("/discover/{scan_id}/add")
    async def add_found(scan_id: str, body: dict = Body(...), client: str = Depends(local_only),
                        manager=Depends(discover)) -> Any:
        return await run(manager.add(scan_id, body.get("picks", []), client))

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
