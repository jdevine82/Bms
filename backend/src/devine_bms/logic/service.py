"""Logic diagrams in the database (main spec 5.3.1 Publishing, 5.3.2 Import; milestone 10A):
drafts, publish checks, versions, enable and disable, import, and the site's variables. Every
change is audit-logged; the runtime (engine.py) reloads after each one that affects it."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select

from devine_bms.auth.service import Actor
from devine_bms.db import models as db
from devine_bms.logic.check import errors, plan
from devine_bms.logic.engine import INSTANCE_SUFFIX, LogicEngine, Var
from devine_bms.logic.model import NAME_PATTERN, SCHEMA


class LogicError(Exception):
    def __init__(self, message: str, status: int = 422, issues: list[dict] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.issues = issues or []

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.message, **({"issues": self.issues} if self.issues else {})}


def _who(actor: Actor) -> str:
    return actor.username or actor.label


class LogicService:
    def __init__(self, engine: LogicEngine, sessions, record=None, setup=None) -> None:
        self.engine = engine
        self.sessions = sessions
        self.record = record
        self.setup = setup  # SetupService: publishing a variable as a point is a setup change

    async def _site_id(self, session):
        site = await session.scalar(select(db.Site).where(db.Site.name == self.engine.site_name))
        return site.id

    async def _row(self, session, name: str) -> db.LogicDiagram:
        row = await session.scalar(select(db.LogicDiagram).where(
            db.LogicDiagram.site_id == await self._site_id(session), db.LogicDiagram.name == name))
        if row is None:
            raise LogicError(f"no logic diagram '{name}'", 404)
        return row

    def _summary(self, row: db.LogicDiagram) -> dict:
        status = self.engine.status.get(row.name, {"state": "draft" if row.published_version is None else "disabled",
                                                   "issues": []})
        writes = [b["settings"]["point"] for b in row.draft.get("blocks", [])
                  if b.get("type") == "point_write" and (b.get("settings") or {}).get("point")]
        return {"name": row.name, "dis": row.dis, "scope": row.draft.get("scope", "site"),
                "equip": row.draft.get("equip"), "template": row.draft.get("template"), "enabled": row.enabled, "publishedVersion": row.published_version,
                "state": status["state"], "issues": status["issues"], "draftAt": row.draft_at.isoformat(),
                "draftBy": row.draft_by, "writes": writes}

    # ---------- reading ----------

    async def list(self) -> list[dict]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(db.LogicDiagram).where(
                db.LogicDiagram.site_id == await self._site_id(session)).order_by(db.LogicDiagram.name))).all()
            out = [self._summary(r) for r in rows]
        return out + [self._instance_summary(n) for n in sorted(self.engine.instances)]

    def _instance_summary(self, name: str) -> dict:
        """A template logic instance (milestone 10C): it runs the template version's logic."""
        meta = self.engine.instances[name]
        status = self.engine.status.get(name, {"state": "unknown", "issues": []})
        return {"name": name, "dis": f"{meta['dis']}: {meta['template']}", "scope": "template", "equip": meta["equip"],
                "template": meta["template"], "templateVersion": meta["templateVersion"], "instance": True,
                "enabled": meta["enabled"], "publishedVersion": None, "state": status["state"],
                "issues": status["issues"], "draftAt": None, "draftBy": None, "writes": self.engine.writes.get(name, [])}

    def by_equip(self) -> list[dict]:
        """The equipment panel (main spec 5.3): which diagrams write each piece of equipment's
        points, and whether it is BMS-controlled (a running diagram writes one) or device-controlled."""
        cfg = self.engine.runtime.config
        names = {e.id: e for e in cfg.equips}
        equip_of = {p.name: names[p.equip_id].name for p in cfg.points if p.equip_id in names}
        out = {e.name: {"equip": e.name, "dis": e.dis, "control": "device", "diagrams": []} for e in cfg.equips}
        for diagram, points in sorted(self.engine.writes.items()):
            state = self.engine.status.get(diagram, {}).get("state", "unknown")
            for equip in sorted({equip_of[p] for p in points if p in equip_of}):
                out[equip]["diagrams"].append({"name": diagram, "state": state,
                                               "points": [p for p in points if equip_of.get(p) == equip],
                                               "template": diagram in self.engine.instances})
                if state == "running":
                    out[equip]["control"] = "bms"
        return list(out.values())

    def _template_body(self, name: str) -> dict | None:
        r = self.engine.running.get(name)
        return r.plan.diagram.model_dump(mode="json", by_alias=True) if r else None

    async def get(self, name: str) -> dict:
        if name in self.engine.instances:  # an instance: read-only, its body is the mapped template logic
            body = self._template_body(name)
            return {**self._instance_summary(name), "draft": body, "published": body, "draftChanged": False,
                    "versions": []}
        async with self.sessions() as session:
            row = await self._row(session, name)
            versions = (await session.scalars(select(db.LogicVersion).where(
                db.LogicVersion.diagram_id == row.id).order_by(db.LogicVersion.version))).all()
            published = next((v.body for v in versions if v.version == row.published_version), None)
            return {**self._summary(row), "draft": row.draft, "published": published,
                    "draftChanged": published is not None and published != row.draft,
                    "versions": [{"version": v.version, "note": v.note, "by": v.published_by,
                                  "at": v.published_at.isoformat()} for v in versions]}

    def check(self, name: str, body: dict, setup: dict | None = None) -> dict:
        site = self._role_view(body, setup) if body.get("scope") == "template" and setup is not None \
            else self.engine.site_view(exclude=name)
        if body.get("scope") == "equipment" and body.get("equip"):
            # An equipment diagram replaces that equipment's template logic, so it does not compete with it.
            replaced = f"{body['equip']}{INSTANCE_SUFFIX}"
            site.writers = {p: d for p, d in site.writers.items() if d != replaced}
        p, issues = plan(body, site)
        # `ok` is that the request worked; `valid` is whether the diagram passes the checks.
        return {"ok": True, "valid": p is not None and not errors(issues), "issues": [i.as_dict() for i in issues]}

    # ---------- changing ----------

    async def save_draft(self, actor: Actor, name: str, body: dict, create: bool = True) -> dict:
        """Save the draft (drafts may be incomplete; they never run). The body must at least be a
        diagram object with this name."""
        if not re.match(NAME_PATTERN, name):
            raise LogicError("a diagram name is lowercase letters, digits and _, starting with a letter")
        if not isinstance(body, dict) or not isinstance(body.get("blocks", []), list):
            raise LogicError("the draft is not a logic diagram")
        body = {"schema": SCHEMA, **body, "name": name}
        async with self.sessions() as session, session.begin():
            site_id = await self._site_id(session)
            row = await session.scalar(select(db.LogicDiagram).where(
                db.LogicDiagram.site_id == site_id, db.LogicDiagram.name == name))
            created = row is None
            if created:
                if not create:
                    raise LogicError(f"no logic diagram '{name}'", 404)
                row = db.LogicDiagram(site_id=site_id, name=name, draft=body, enabled=True)
                session.add(row)
            row.draft, row.dis, row.draft_by = body, str(body.get("dis") or name), _who(actor)
            row.draft_at = func.now()
        if created:
            await self._audit(actor, "logic-create", f"created logic diagram {name}", name, None, None)
        setup = await self.setup.export() if body.get("scope") == "template" and self.setup is not None else None
        return {"ok": True, "created": created, **self.check(name, body, setup)}

    async def publish(self, actor: Actor, name: str, confirm_failsafe: bool, note: str | None = None,
                      template_version: str | None = None) -> dict:
        async with self.sessions() as session:
            row = await self._row(session, name)
            body = row.draft
        setup = await self.setup.export() if body.get("scope") == "template" and self.setup is not None else None
        result = self.check(name, body, setup)
        if not result["valid"]:
            raise LogicError("the diagram fails the publish checks", issues=result["issues"])
        if not confirm_failsafe and any(b.get("type") == "point_write" for b in body.get("blocks", [])):
            raise LogicError("confirm each output's fail-safe before publishing (confirmFailsafe)", 409)
        new_template = None
        if body.get("scope") == "template":
            new_template = await self._publish_template(actor, body, setup, template_version, note)
        async with self.sessions() as session, session.begin():
            row = await self._row(session, name)
            last = await session.scalar(select(func.max(db.LogicVersion.version)).where(
                db.LogicVersion.diagram_id == row.id))
            version = (last or 0) + 1
            session.add(db.LogicVersion(diagram_id=row.id, version=version, body=body, note=note,
                                        published_by=_who(actor)))
            row.published_version = version
        await self.engine.reload()
        await self._audit(actor, "logic-publish", f"published logic diagram {name} version {version}"
                          + (f" as template {new_template['id']} version {new_template['version']}" if new_template else "")
                          + (f": {note}" if note else ""), name, None, {"version": version})
        return {"ok": True, "version": version, "issues": result["issues"], "state": self.engine.status.get(name),
                **({"templateVersion": new_template["version"]} if new_template else {})}

    # ---------- template logic (milestone 10C, decision 7) ----------

    def _role_view(self, body: dict, setup: dict):
        """The checks' view for a template's logic: its roles stand in for points."""
        from devine_bms.config.model import EquipTemplate
        from devine_bms.logic.check import SitePoint
        from devine_bms.templates.equip import find

        t = find([EquipTemplate.model_validate(x) for x in setup.get("equipTemplates") or []], body.get("template") or "")
        site = self.engine.site_view()
        site.writers = {}
        site.points = {} if t is None else {
            r.role: SitePoint(r.role, r.kind.value, r.type.value in ("sp", "cmd"), r.occupancy is not None)
            for r in t.roles}
        return site

    async def _publish_template(self, actor: Actor, body: dict, setup: dict, version: str | None,
                                note: str | None) -> dict:
        """Save the logic in a new version of its template (instances move by the roll-out)."""
        from devine_bms.config.model import EquipTemplate
        from devine_bms.setup.equip_templates import EquipTemplateService
        from devine_bms.setup.service import SetupError
        from devine_bms.templates.equip import all_versions, builtin_equip_templates

        tid = body.get("template") or ""
        if tid in builtin_equip_templates():
            raise LogicError(f"'{tid}' is a built-in template; save a copy of it under a new ID first, then give it logic")
        versions = all_versions([EquipTemplate.model_validate(x) for x in setup.get("equipTemplates") or []]).get(tid)
        if not versions:
            raise LogicError(f"no equipment template '{tid}'", 404)
        latest = versions[-1]
        if not version:
            parts = latest.version.split(".")
            version = ".".join([*parts[:-1], str(int(parts[-1]) + 1)]) if parts[-1].isdigit() else f"{latest.version}.1"
        logic = {k: v for k, v in body.items() if k not in ("name", "schema")}
        new = latest.model_dump(mode="json", by_alias=True) | {"version": version, "logic": {"schema": SCHEMA, **logic}}
        if note:
            new["notes"] = [*new.get("notes", []), f"{version}: {note}"]
        try:
            await EquipTemplateService(self.setup).save(new, actor)
        except SetupError as exc:
            raise LogicError(exc.message, exc.status) from exc
        return {"id": tid, "version": version}

    async def template_draft(self, actor: Actor, template_id: str) -> dict:
        """Open a template's logic for editing: its draft, started from the latest version's logic."""
        from devine_bms.config.model import EquipTemplate
        from devine_bms.templates.equip import find

        data = await self.setup.export()
        t = find([EquipTemplate.model_validate(x) for x in data.get("equipTemplates") or []], template_id)
        if t is None:
            raise LogicError(f"no equipment template '{template_id}'", 404)
        name = "tpl_" + re.sub(r"[^a-z0-9_]", "_", template_id)
        async with self.sessions() as session:
            exists = await session.scalar(select(db.LogicDiagram).where(
                db.LogicDiagram.site_id == await self._site_id(session), db.LogicDiagram.name == name))
        if exists is None:
            start = dict(t.logic or {})
            start.update({"name": name, "dis": f"{t.dis} logic", "scope": "template", "template": t.id})
            start.setdefault("variables", [])
            start.setdefault("blocks", [])
            start.setdefault("wires", [])
            await self.save_draft(actor, name, start)
        return {"ok": True, "name": name, "roles": [{"role": r.role, "dis": r.dis, "kind": r.kind.value,
                                                     "type": r.type.value, "unit": r.unit} for r in t.roles]}

    async def restore(self, actor: Actor, name: str, version: int) -> dict:
        async with self.sessions() as session, session.begin():
            row = await self._row(session, name)
            v = await session.scalar(select(db.LogicVersion).where(
                db.LogicVersion.diagram_id == row.id, db.LogicVersion.version == version))
            if v is None:
                raise LogicError(f"diagram '{name}' has no version {version}", 404)
            row.draft, row.draft_by, row.draft_at = v.body, _who(actor), func.now()
        await self._audit(actor, "logic-restore", f"restored version {version} of logic diagram {name} into its draft",
                          name, None, {"version": version})
        return {"ok": True, "version": version}

    async def enable(self, actor: Actor, name: str, enabled: bool) -> dict:
        async with self.sessions() as session, session.begin():
            if name in self.engine.instances:  # a template instance's own switch
                site_id = await self._site_id(session)
                row = await session.scalar(select(db.LogicInstance).where(
                    db.LogicInstance.site_id == site_id, db.LogicInstance.name == name))
                if row is None:
                    row = db.LogicInstance(site_id=site_id, name=name)
                    session.add(row)
            else:
                row = await self._row(session, name)
            row.enabled = enabled
        await self.engine.reload()
        await self._audit(actor, "logic-enable" if enabled else "logic-disable",
                          f"{'enabled' if enabled else 'disabled'} logic diagram {name}"
                          + ("" if enabled else "; its outputs went to their fail-safe"), name,
                          None, {"enabled": enabled})
        return {"ok": True, "enabled": enabled, "state": self.engine.status.get(name)}

    async def delete(self, actor: Actor, name: str) -> dict:
        if name in self.engine.instances:
            raise LogicError("a template's logic runs on each instance; switch this one off, or unlink the equipment")
        async with self.sessions() as session, session.begin():
            row = await self._row(session, name)
            await session.delete(row)
            for v in (await session.scalars(select(db.LogicVariable).where(
                    db.LogicVariable.scope == "diagram", db.LogicVariable.owner == name))).all():
                await session.delete(v)
        for key in [k for k in self.engine.vars if k[0] == "diagram" and k[1] == name]:
            del self.engine.vars[key]
        await self.engine.reload()
        await self._audit(actor, "logic-delete", f"removed logic diagram {name}; its outputs went to their fail-safe",
                          name, None, None)
        return {"ok": True}

    async def import_(self, actor: Actor, body: dict) -> dict:
        """Import pasted or uploaded JSON as a draft (main spec 5.3.2): errors stop it, warnings
        are returned. `body`: {"diagram": {...}, "name": optional new name, "replace": bool}."""
        diagram = body.get("diagram")
        if isinstance(diagram, str):
            import json

            try:
                diagram = json.loads(diagram)
            except json.JSONDecodeError as exc:
                raise LogicError(f"this is not JSON: {exc}") from exc
        if not isinstance(diagram, dict):
            raise LogicError("send {diagram: {...}} (or its text)")
        if diagram.get("schema") not in (None, SCHEMA):
            raise LogicError(f"this is not a {SCHEMA} diagram (schema: {diagram.get('schema')})")
        name = body.get("name") or diagram.get("name")
        if not isinstance(name, str):
            raise LogicError("the diagram has no name")
        diagram = {**diagram, "name": name, "schema": SCHEMA}
        # Errors in the diagram itself block the import; what does not match this site (points,
        # variables, schedules, another writer) is a warning to fix in the draft (main spec 5.3.2).
        _p, structural = plan(diagram, None)
        blocking = [i.as_dict() for i in errors(structural)]
        seen = {(i["block"], i["message"]) for i in blocking if "block" in i} | {(None, i["message"]) for i in blocking}
        full = [] if blocking else self.check(name, diagram)["issues"]
        warnings = [{**i, "level": "warning"} for i in full if (i.get("block"), i["message"]) not in seen]
        async with self.sessions() as session:
            exists = await session.scalar(select(db.LogicDiagram).where(
                db.LogicDiagram.site_id == await self._site_id(session), db.LogicDiagram.name == name))
        if body.get("check"):  # the import dialog's check: nothing is created
            return {"ok": True, "valid": not blocking, "name": name, "exists": exists is not None,
                    "issues": blocking + warnings}
        if blocking:
            raise LogicError("the import has errors; nothing was created", issues=blocking)
        result = {"issues": warnings}
        if exists is not None and not body.get("replace"):
            raise LogicError(f"there is already a diagram '{name}'; give a new name or replace its draft", 409)
        await self.save_draft(actor, name, diagram)
        await self._audit(actor, "logic-import", f"imported logic diagram {name} as a draft", name, None,
                          {"blocks": len(diagram.get("blocks", []))})
        return {"ok": True, "name": name, "issues": result["issues"]}

    # ---------- variables ----------

    def variables(self) -> list[dict]:
        return [v.as_dict() for v in sorted(self.engine.vars.values(), key=lambda v: (v.scope, v.owner, v.name))]

    async def put_site_variable(self, actor: Actor, name: str, body: dict) -> dict:
        """Create or change a site variable (definition and value), or set a constant's value."""
        if not re.match(NAME_PATTERN, name):
            raise LogicError("a variable name is lowercase letters, digits and _, starting with a letter")
        key = ("site", "", name)
        have = self.engine.vars.get(key)
        vtype = body.get("type", have.type if have else "number")
        kind = body.get("kind", have.kind if have else "constant")
        if vtype not in ("number", "bool", "text") or kind not in ("constant", "memory"):
            raise LogicError("type is number, bool or text; kind is constant or memory")
        value = body.get("value", have.value if have else None)
        lo = body.get("min", have.min if have else None)
        hi = body.get("max", have.max if have else None)
        value = _variable_value(vtype, value, lo, hi)
        before = have.as_dict() if have else None
        var = Var("site", "", name, vtype, kind, value, body.get("unit", have.unit if have else None),
                  body.get("dis", have.dis if have else None), lo, hi, dirty=True)
        self.engine.vars[key] = var
        await self.engine.flush_variables(force=True)
        await self._audit(actor, "variable", f"set site variable {name} to {value}", name, before, var.as_dict(),
                          kind="variable")
        return {"ok": True, "variable": var.as_dict()}

    async def set_diagram_variable(self, actor: Actor, diagram: str, name: str, value: Any) -> dict:
        v = self.engine.vars.get(("diagram", diagram, name))
        if v is None:
            raise LogicError(f"diagram '{diagram}' has no variable '{name}' (publish it first)", 404)
        before = v.value
        v.value, v.dirty = _variable_value(v.type, value, v.min, v.max), True
        await self.engine.flush_variables(force=True)
        await self._audit(actor, "variable", f"set {diagram}.{name} to {v.value}", f"{diagram}.{name}",
                          {"value": before}, {"value": v.value}, kind="variable")
        return {"ok": True, "variable": v.as_dict()}

    async def publish_variable(self, actor: Actor, body: dict) -> dict:
        """Make a point for a variable on the BMS logic device (milestone 10C, decision 6). body:
        variable, diagram (for a diagram's own variable), point (its name), dis, equip or space."""
        from devine_bms.setup.service import SetupError

        if self.setup is None:
            raise LogicError("setup is not available")
        name, diagram = body.get("variable"), body.get("diagram") or None
        v = self.engine.vars.get(("diagram", diagram, name)) if diagram else self.engine.vars.get(("site", "", name))
        if v is None:
            raise LogicError(f"no variable '{name}'" + (f" in {diagram} (publish the diagram first)" if diagram else ""), 404)
        point = body.get("point") or ""
        if not re.match(NAME_PATTERN, point):
            raise LogicError("give the point a name: lowercase letters, digits and _, starting with a letter")
        if not body.get("equip") and not body.get("space"):
            raise LogicError("choose the equipment or room the point belongs to")
        data = await self.setup.export()
        if not any(d.get("protocol") == "logic" for d in data["devices"]):
            data["devices"].append({"name": "bms_logic", "dis": "BMS logic", "protocol": "logic", "connection": {}})
        device = next(d["name"] for d in data["devices"] if d.get("protocol") == "logic")
        item = {"name": point, "dis": body.get("dis") or v.dis or name, "device": device,
                "type": "sp" if v.kind == "constant" else "sensor",
                "kind": {"number": "Number", "bool": "Bool", "text": "Str"}[v.type],
                "address": {"variable": name, **({"diagram": diagram} if diagram else {})},
                "pollClass": "fast", "tags": {}}
        if v.type == "text":
            raise LogicError("text variables cannot be points yet (a text point needs a list of values)")
        if v.unit:
            item["unit"] = v.unit
        if v.kind == "constant" and v.type == "number" and v.min is not None and v.max is not None:
            item["writeLimits"] = {"min": v.min, "max": v.max}
        item.update({k: body[k] for k in ("equip", "space") if body.get(k)})
        if any(p["name"] == point for p in data["points"]):
            raise LogicError(f"there is already a point '{point}'", 409)
        data["points"].append(item)
        try:
            result = await self.setup._apply(data, actor, "put", "point", point,
                                             f"published logic variable {name} as point {point}", None, item, False)
        except SetupError as exc:
            raise LogicError(exc.message, exc.status, [{"level": i.severity.value, "message": f"{i.where}: {i.message}"}
                                                       for i in exc.issues]) from exc
        return {"ok": True, "point": point, "warnings": result.get("warnings", [])}

    # ---------- snippets (milestone 10C, decision 8) ----------

    async def snippets(self) -> list[dict]:
        return list((await self.setup.export()).get("logicSnippets") or [])

    async def put_snippet(self, actor: Actor, name: str, body: dict) -> dict:
        from devine_bms.setup.service import SetupError

        fragment = body.get("fragment")
        if not isinstance(fragment, dict) or not isinstance(fragment.get("blocks"), list) or not fragment["blocks"]:
            raise LogicError("a snippet needs blocks (select some first)")
        _p, issues = plan({"name": "snippet", **fragment}, None)
        # A snippet is a piece of a diagram: its inputs are wired where it is inserted.
        blocking = [i for i in errors(issues) if "is not wired and has no value" not in i.message]
        if blocking:
            raise LogicError("the snippet has errors", issues=[i.as_dict() for i in blocking])
        data = await self.setup.export()
        snippets = [x for x in data.get("logicSnippets") or [] if x["name"] != name]
        item = {"name": name, "dis": body.get("dis") or name, "fragment": {"schema": SCHEMA, **fragment}}
        data["logicSnippets"] = [*snippets, item]
        try:
            await self.setup._apply(data, actor, "put", "logic-snippet", name, f"saved logic snippet {item['dis']}",
                                    None, None, False)
        except SetupError as exc:
            raise LogicError(exc.message, exc.status) from exc
        return {"ok": True, "snippet": item}

    async def delete_snippet(self, actor: Actor, name: str) -> dict:
        data = await self.setup.export()
        before = data.get("logicSnippets") or []
        if not any(x["name"] == name for x in before):
            raise LogicError(f"no snippet '{name}'", 404)
        data["logicSnippets"] = [x for x in before if x["name"] != name]
        await self.setup._apply(data, actor, "delete", "logic-snippet", name, f"removed logic snippet {name}",
                                None, None, False)
        return {"ok": True}

    async def _audit(self, actor: Actor, action: str, summary: str, name: str | None, before: Any, after: Any,
                     kind: str = "logic") -> None:
        if self.record is not None:
            await self.record(actor, action, summary, kind=kind, name=name, before=before, after=after)


def _variable_value(vtype: str, value: Any, lo: float | None, hi: float | None) -> Any:
    if value is None:
        return None
    if vtype == "bool":
        if not isinstance(value, bool):
            raise LogicError("the value must be true or false")
        return value
    if vtype == "text":
        return str(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LogicError("the value must be a number")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise LogicError(f"the value must be between {lo} and {hi}")
    return float(value)
