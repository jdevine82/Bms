"""Device template library (main spec 5.1, milestone 5A).

Built-in templates ship with the software (read-only). A site saves its own templates
in the database with versions: a saved version never changes, so a new version is saved
next to it. Applying a template adds a device's points in one checked setup change.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select

from devine_bms.db import models as db
from devine_bms.setup.service import SetupError, SetupService
from devine_bms.templates.device import DeviceTemplate, builtin_templates, expand, load_template


def _version_key(version: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in version.replace("-", ".").split("."))


def _summary(t: DeviceTemplate, source: str) -> dict:
    return {"id": t.id, "version": t.version, "source": source, "vendor": t.vendor, "model": t.model,
            "dis": t.dis, "status": t.status, "protocols": list(t.protocols), "hardwareId": t.hardware_id,
            "points": len(t.points)}


class TemplateLibrary:
    def __init__(self, setup: SetupService) -> None:
        self.setup = setup

    async def _site_rows(self) -> list[db.DeviceTemplateRow]:
        async with self.setup.sessions() as session:
            site = await self.setup._site(session)
            return list((await session.scalars(
                select(db.DeviceTemplateRow).where(db.DeviceTemplateRow.site_id == site.id)
            )).all())

    async def list(self) -> list[dict]:
        out = [_summary(t, "builtin") for t in builtin_templates().values()]
        latest: dict[str, DeviceTemplate] = {}
        for row in await self._site_rows():
            t = load_template(row.data)
            if t.id not in latest or _version_key(t.version) > _version_key(latest[t.id].version):
                latest[t.id] = t
        out += [_summary(t, "site") for t in latest.values()]
        return sorted(out, key=lambda t: t["id"])

    async def versions(self, template_id: str) -> list[str]:
        if template_id in builtin_templates():
            return [builtin_templates()[template_id].version]
        rows = [r for r in await self._site_rows() if r.template_id == template_id]
        return sorted((r.version for r in rows), key=_version_key)

    async def get(self, template_id: str, version: str | None = None) -> tuple[DeviceTemplate, str]:
        if template_id in builtin_templates():
            t = builtin_templates()[template_id]
            if version and version != t.version:
                raise SetupError(f"built-in template '{template_id}' has only version {t.version}", status=404)
            return t, "builtin"
        rows = [r for r in await self._site_rows() if r.template_id == template_id]
        if version:
            rows = [r for r in rows if r.version == version]
        if not rows:
            raise SetupError(f"no template '{template_id}'" + (f" version {version}" if version else ""),
                             status=404)
        row = max(rows, key=lambda r: _version_key(r.version))
        return load_template(row.data), "site"

    async def save(self, data: dict, client: str, check_only: bool = False) -> dict:
        try:
            t = load_template(data)
        except ValidationError as exc:
            raise SetupError("the template is not valid: " + "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())) from exc
        if t.id in builtin_templates():
            raise SetupError(f"'{t.id}' is a built-in template; save a copy under a new ID")
        existing = await self.versions(t.id)
        if t.version in existing:
            raise SetupError(f"template '{t.id}' version {t.version} is already saved; give it a new version")
        if check_only:
            return {"ok": True, "applied": False}
        async with self.setup.sessions() as session, session.begin():
            site = await self.setup._site(session)
            session.add(db.DeviceTemplateRow(site_id=site.id, template_id=t.id, version=t.version,
                                             data=t.model_dump(mode="json", by_alias=True)))
            self.setup._log(session, site.id, client, "put", "template", t.id,
                            f"saved device template {t.id} version {t.version}", None,
                            {"id": t.id, "version": t.version})
        return {"ok": True, "applied": True, "id": t.id, "version": t.version}

    async def delete(self, template_id: str, version: str | None, client: str) -> dict:
        if template_id in builtin_templates():
            raise SetupError("built-in templates cannot be removed")
        if not await self.versions(template_id):
            raise SetupError(f"no template '{template_id}'", status=404)
        async with self.setup.sessions() as session, session.begin():
            site = await self.setup._site(session)
            query = delete(db.DeviceTemplateRow).where(
                db.DeviceTemplateRow.site_id == site.id, db.DeviceTemplateRow.template_id == template_id)
            if version:
                query = query.where(db.DeviceTemplateRow.version == version)
            await session.execute(query)
            self.setup._log(session, site.id, client, "delete", "template", template_id,
                            f"removed device template {template_id}" + (f" version {version}" if version else ""),
                            None, None)
        return {"ok": True, "applied": True}

    async def apply(self, template_id: str, body: dict[str, Any], client: str, check_only: bool = False) -> dict:
        """Add a device's points from a template, as one checked setup change (bulk create, main spec 5.1).

        body: device, namePrefix, equip or space, optional only (point keys), version, and
        repeat {from, to, step} with {n} in namePrefix, equip and space. Equipment named with
        {n} that does not exist yet is created, with equipDis ({n} replaced) as its name.
        """
        template, _ = await self.get(template_id, body.get("version"))
        device, prefix = body.get("device"), body.get("namePrefix")
        if not device or not prefix:
            raise SetupError("give the device and the namePrefix")
        repeat = body.get("repeat")
        numbers: list[int | None] = [None]
        step = 0
        if repeat:
            first, last, step = int(repeat.get("from", 1)), int(repeat.get("to", 1)), int(repeat.get("step", 0))
            if not 0 <= first <= last or last - first >= 256:
                raise SetupError("repeat needs 0 <= from <= to, and at most 256 repeats")
            numbers = list(range(first, last + 1))
        data = await self.setup.export()
        equips = {e["name"] for e in data.get("equips", [])}
        new_points = []
        for n in numbers:
            try:
                points = expand(template, device, prefix, body.get("equip"), body.get("space"), body.get("only"),
                                n=n, step=step)
            except (ValueError, KeyError, IndexError) as exc:
                raise SetupError(str(exc)) from exc
            new_points += points
            equip = points[0].get("equip") if points else None
            if equip and equip not in equips:
                entry = {"name": equip, "dis": (body.get("equipDis") or "{equip}").format(n=n, equip=equip),
                         "tags": body.get("equipTags", {})}
                if body.get("equipSpace"):
                    entry["space"] = body["equipSpace"]
                data.setdefault("equips", []).append(entry)
                equips.add(equip)
        existing = {p["name"] for p in data.get("points", [])}
        clashes = sorted(p["name"] for p in new_points if p["name"] in existing)
        if clashes:
            raise SetupError(f"these points already exist: {clashes[:10]}")
        for point in new_points:
            await self.setup._refuse_alias(point["name"], None)
        data.setdefault("points", []).extend(new_points)
        summary = f"added {len(new_points)} points to {device} from template {template.id} {template.version}"
        return await self.setup._apply(data, client, "apply-template", "device", device, summary, None,
                                       {"template": template.id, "version": template.version,
                                        "points": [p["name"] for p in new_points]}, check_only)
