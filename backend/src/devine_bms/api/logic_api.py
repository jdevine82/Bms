"""Control logic over HTTP (main spec 5.3.1 and 5.3.2; milestone 10A). Reading and Live mode:
any signed-in role. Drafts, publishing, import and variables: admins (main spec 7), audit-logged."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from devine_bms.api.auth_api import require
from devine_bms.auth.service import Actor
from devine_bms.logic.model import BLOCKS
from devine_bms.logic.service import LogicError, LogicService


def build_logic_router(get_logic: Callable[[], LogicService | None]) -> APIRouter:
    router = APIRouter()

    def logic() -> LogicService:
        svc = get_logic()
        if svc is None:
            raise HTTPException(503, "logic needs the database")
        return svc

    async def run(call) -> Any:
        try:
            return await call
        except LogicError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    def viewer(request: Request) -> Actor:
        return require("viewer")(request)

    @router.get("/api/logic-blocks")
    async def blocks(_: Actor = Depends(viewer)) -> Any:
        """The v1 block types: group, name and settings schema (for the editor's palette)."""
        return [{"type": b.type, "group": b.group, "dis": b.dis, "timing": b.timing,
                 "settings": b.settings.model_json_schema(by_alias=True)} for b in BLOCKS.values()]

    @router.get("/api/logic")
    async def list_logic(_: Actor = Depends(viewer), svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.list())

    @router.get("/api/logic-variables")
    async def variables(_: Actor = Depends(viewer), svc: LogicService = Depends(logic)) -> Any:
        return svc.variables()

    @router.put("/api/logic-variables/{name}")
    async def put_variable(name: str, body: dict = Body(...), actor: Actor = Depends(require("admin")),
                           svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.put_site_variable(actor, name, body))

    @router.post("/api/logic-variables/publish")
    async def publish_variable(body: dict = Body(...), actor: Actor = Depends(require("admin")),
                               svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.publish_variable(actor, body))

    @router.post("/api/logic/template-draft/{template_id}")
    async def template_draft(template_id: str, actor: Actor = Depends(require("admin")),
                             svc: LogicService = Depends(logic)) -> Any:
        """Open an equipment template's logic in the editor (role mode): its draft's name and roles."""
        return await run(svc.template_draft(actor, template_id))

    @router.get("/api/logic/by-equip")
    async def by_equip(_: Actor = Depends(viewer), svc: LogicService = Depends(logic)) -> Any:
        """For each piece of equipment: the diagrams that write its points, and BMS- or device-controlled."""
        return svc.by_equip()

    @router.get("/api/logic-snippets")
    async def snippets(_: Actor = Depends(viewer), svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.snippets())

    @router.put("/api/logic-snippets/{name}")
    async def put_snippet(name: str, body: dict = Body(...), actor: Actor = Depends(require("admin")),
                          svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.put_snippet(actor, name, body))

    @router.delete("/api/logic-snippets/{name}")
    async def delete_snippet(name: str, actor: Actor = Depends(require("admin")),
                             svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.delete_snippet(actor, name))

    @router.post("/api/logic/import")
    async def import_logic(body: dict = Body(...), actor: Actor = Depends(require("admin")),
                           svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.import_(actor, body))

    @router.get("/api/logic/{name}")
    async def one(name: str, _: Actor = Depends(viewer), svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.get(name))

    @router.get("/api/logic/{name}/live")
    async def live(name: str, _: Actor = Depends(viewer), svc: LogicService = Depends(logic)) -> Any:
        return svc.engine.live(name)

    @router.put("/api/logic/{name}/draft")
    async def draft(name: str, body: dict = Body(...), actor: Actor = Depends(require("admin")),
                    svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.save_draft(actor, name, body))

    @router.post("/api/logic/{name}/check")
    async def check(name: str, body: dict | None = Body(None), _: Actor = Depends(require("admin")),
                    svc: LogicService = Depends(logic)) -> Any:
        if body is None:
            body = (await run(svc.get(name)))
            if isinstance(body, JSONResponse):
                return body
            body = body["draft"]
        # A template's logic is checked against the template's roles.
        setup = await svc.setup.export() if body.get("scope") == "template" and svc.setup is not None else None
        return svc.check(name, body, setup)

    @router.post("/api/logic/{name}/test")
    async def test(name: str, body: dict = Body(...), _: Actor = Depends(require("admin")),
                   svc: LogicService = Depends(logic)) -> Any:
        """Run the draft (or `diagram`) against simulated inputs; nothing is written. body:
        inputs {point: value or null}, steps, dt (seconds), schedules {name: bool}, start (ISO time)."""
        diagram = body.get("diagram")
        if diagram is None:
            got = await run(svc.get(name))
            if isinstance(got, JSONResponse):
                return got
            diagram = got["draft"]
        start = datetime.fromisoformat(body["start"]) if body.get("start") else None
        try:
            steps, dt = int(body.get("steps", 10)), float(body.get("dt", 1.0))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "steps and dt are numbers"}, status_code=422)
        if not 0 < dt <= 3600:
            return JSONResponse({"ok": False, "error": "dt is between 0 and 3600 seconds"}, status_code=422)
        return svc.engine.test(diagram, name, body.get("inputs") or {}, steps, dt, body.get("schedules"), start)

    @router.post("/api/logic/{name}/publish")
    async def publish(name: str, body: dict = Body(default={}), actor: Actor = Depends(require("admin")),
                      svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.publish(actor, name, bool(body.get("confirmFailsafe")), body.get("note"),
                                     body.get("templateVersion")))

    @router.post("/api/logic/{name}/restore")
    async def restore(name: str, body: dict = Body(...), actor: Actor = Depends(require("admin")),
                      svc: LogicService = Depends(logic)) -> Any:
        if not isinstance(body.get("version"), int):
            return JSONResponse({"ok": False, "error": "send {version: n}"}, status_code=422)
        return await run(svc.restore(actor, name, body["version"]))

    @router.post("/api/logic/{name}/enable")
    async def enable(name: str, body: dict = Body(...), actor: Actor = Depends(require("admin")),
                     svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.enable(actor, name, bool(body.get("enabled"))))

    @router.put("/api/logic/{name}/variables/{variable}")
    async def set_variable(name: str, variable: str, body: dict = Body(...), actor: Actor = Depends(require("admin")),
                           svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.set_diagram_variable(actor, name, variable, body.get("value")))

    @router.delete("/api/logic/{name}")
    async def delete(name: str, actor: Actor = Depends(require("admin")), svc: LogicService = Depends(logic)) -> Any:
        return await run(svc.delete(actor, name))

    return router
