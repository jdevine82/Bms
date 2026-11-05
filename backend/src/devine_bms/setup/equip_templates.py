"""Equipment templates in setup (main spec 5.1.2, milestone 5A).

Every operation is a checked setup change (SetupService._apply): save or remove a template
version, make equipment (or a room) from a template, roll a new version out, reset an
override, unlink. See templates/equip.py for how overrides are worked out.
"""

from __future__ import annotations

import copy

from pydantic import ValidationError

from devine_bms.config.model import EquipTemplate
from devine_bms.setup.service import SetupError, SetupService
from devine_bms.templates.device import builtin_templates as builtin_device_templates
from devine_bms.templates.device import expand
from devine_bms.templates.equip import (
    ROLE_FIELDS,
    all_versions,
    builtin_equip_templates,
    diff,
    find,
    overrides,
    point_name,
    role_fields,
)


def _site_templates(data: dict) -> list[EquipTemplate]:
    return [EquipTemplate.model_validate(t) for t in data.get("equipTemplates", [])]


def _summary(t: EquipTemplate, source: str, versions: list[str], instances: int) -> dict:
    return {"id": t.id, "version": t.version, "versions": versions, "source": source, "dis": t.dis,
            "appliesTo": t.applies_to, "roles": len(t.roles), "instances": instances}


class EquipTemplateService:
    def __init__(self, setup: SetupService) -> None:
        self.setup = setup

    # ---------- the templates ----------

    async def list(self) -> list[dict]:
        data = await self.setup.export()
        site = _site_templates(data)
        out = []
        for template_id, versions in all_versions(site).items():
            latest = versions[-1]
            source = "builtin" if template_id in builtin_equip_templates() else "site"
            out.append(_summary(latest, source, [t.version for t in versions], len(self._instances(data, template_id))))
        return sorted(out, key=lambda t: t["id"])

    async def get(self, template_id: str, version: str | None = None) -> dict:
        data = await self.setup.export()
        site = _site_templates(data)
        t = find(site, template_id, version)
        if t is None:
            raise SetupError(f"no equipment template '{template_id}'" + (f" version {version}" if version else ""),
                             status=404)
        return {"source": "builtin" if template_id in builtin_equip_templates() else "site",
                "versions": [v.version for v in all_versions(site)[template_id]],
                "instances": [{"kind": k, "name": i["name"], "version": i["template"]["version"]}
                              for k, i in self._instances(data, template_id)],
                "template": t.model_dump(mode="json", by_alias=True)}

    async def save(self, body: dict, client: str, check_only: bool = False) -> dict:
        try:
            t = EquipTemplate.model_validate(body)
        except ValidationError as exc:
            raise SetupError("the template is not valid: " + "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())) from exc
        data = await self.setup.export()
        existing = [v.version for v in all_versions(_site_templates(data)).get(t.id, [])]
        if t.version in existing:
            raise SetupError(f"equipment template '{t.id}' version {t.version} is already saved; give it a new version")
        data.setdefault("equipTemplates", []).append(t.model_dump(mode="json", by_alias=True))
        return await self.setup._apply(data, client, "put", "equip-template", t.id,
                                       f"saved equipment template {t.id} version {t.version}", None,
                                       {"id": t.id, "version": t.version}, check_only)

    async def delete(self, template_id: str, version: str | None, client: str) -> dict:
        if template_id in builtin_equip_templates():
            raise SetupError("built-in templates cannot be removed")
        data = await self.setup.export()
        users = [i["name"] for _k, i in self._instances(data, template_id)
                 if version is None or i["template"]["version"] == version]
        if users:
            raise SetupError(f"still used by {users[:10]}; roll them to another version or unlink them first")
        before = len(data.get("equipTemplates", []))
        data["equipTemplates"] = [t for t in data.get("equipTemplates", [])
                                  if not (t["id"] == template_id and (version is None or t["version"] == version))]
        if len(data["equipTemplates"]) == before:
            raise SetupError(f"no site template '{template_id}'" + (f" version {version}" if version else ""),
                             status=404)
        return await self.setup._apply(data, client, "delete", "equip-template", template_id,
                                       f"removed equipment template {template_id}" +
                                       (f" version {version}" if version else ""), None, None, False)

    async def diff(self, template_id: str, old: str, new: str) -> dict:
        site = _site_templates(await self.setup.export())
        a, b = find(site, template_id, old), find(site, template_id, new)
        if a is None or b is None:
            raise SetupError(f"template '{template_id}' has no version {old if a is None else new}", status=404)
        return diff(a, b)

    # ---------- making equipment ----------

    async def create(self, template_id: str, body: dict, client: str, check_only: bool = False) -> dict:
        """Make equipment (or a room) from a template.

        body: name, dis, space (equipment only), parent, version, parameters, and how roles are
        filled, either per role in `bindings`: {"role": {"point": "<existing point>"}} or
        {"role": {"device": "...", "address": {...}}}, or for every role at once from a device
        template: "fromDeviceTemplate": {"template": "...", "device": "...", "keys": {role: key}}
        (keys default to the role names). With "repeat": {"from", "to", "step"}, {n} in name, dis,
        space and parent is replaced and device template addresses move by n * step (bulk create:
        one indoor unit per Mitsubishi group).
        """
        data, draft = await self.setup.working_copy(body)
        template = find(_site_templates(data), template_id, body.get("version"))
        if template is None:
            raise SetupError(f"no equipment template '{template_id}'", status=404)
        if not body.get("name"):
            raise SetupError("give the name of the new " + template.applies_to)
        repeat = body.get("repeat")
        numbers: list[int | None] = [None]
        step = 0
        if repeat:
            first, last = int(repeat.get("from", 1)), int(repeat.get("to", 1))
            step = int(repeat.get("step", 0))
            if not 0 <= first <= last or last - first >= 256:
                raise SetupError("repeat needs 0 <= from <= to, and at most 256 repeats")
            numbers = list(range(first, last + 1))
        made = []
        for n in numbers:
            made.append(self._make_one(data, template, body, n, step))
        summary = (f"made {len(made)} {template.applies_to}{'s' if len(made) > 1 else ''} from template "
                   f"{template.id} {template.version}: {', '.join(made[:5])}{'...' if len(made) > 5 else ''}")
        return await self.setup._apply(data, client, "create-from-template", template.applies_to, made[0], summary,
                                       None, {"template": template.id, "version": template.version, "made": made},
                                       check_only, draft)

    def _make_one(self, data: dict, template: EquipTemplate, body: dict, n: int | None, step: int) -> str:
        fmt = (lambda s: s.format(n=n) if isinstance(s, str) and n is not None else s)
        name, dis = fmt(body["name"]), fmt(body.get("dis") or body["name"])
        is_space = template.applies_to == "space"
        room = name if is_space else fmt(body.get("space"))
        link = {"id": template.id, "version": template.version, "bindings": {},
                "parameters": {**template.parameters, **body.get("parameters", {})}}
        owner = {"name": name, "dis": dis, "tags": {**template.tags, **body.get("tags", {})}, "template": link}
        if not is_space:
            if room:
                owner["space"] = room
            if body.get("parent"):
                owner["parent"] = fmt(body["parent"])
        if any(i["name"] == name for i in data.get("spaces" if is_space else "equips", [])):
            raise SetupError(f"'{name}' already exists")
        data.setdefault("spaces" if is_space else "equips", []).append(owner)

        sources = self._sources(template, body, n, step)
        points = {p["name"]: p for p in data.get("points", [])}
        for role in template.roles:
            source = sources.get(role.role)
            if source is None:
                continue  # left unbound; a required role shows as a warning
            fields = role_fields(role)
            if "point" in source:
                existing = points.get(source["point"])
                if existing is None:
                    raise SetupError(f"role '{role.role}': no point '{source['point']}'")
                existing.update({k: v for k, v in fields.items() if v is not None})
                if is_space:  # a room's own points belong to the room, not to equipment
                    existing.pop("equip", None)
                    existing["space"] = name
                else:
                    existing["equip"] = name
                link["bindings"][role.role] = existing["name"]
                continue
            new_name = point_name(room, None if is_space else name, role.role) if not is_space \
                else f"{name}_{role.role}"
            if new_name in points:
                raise SetupError(f"point '{new_name}' already exists")
            point = {k: v for k, v in fields.items() if v is not None}
            point.update({"name": new_name, "device": source["device"], "address": source["address"]})
            point["space" if is_space else "equip"] = name
            data.setdefault("points", []).append(point)
            points[new_name] = point
            link["bindings"][role.role] = new_name
        return name

    @staticmethod
    def _sources(template: EquipTemplate, body: dict, n: int | None, step: int) -> dict[str, dict]:
        sources: dict[str, dict] = {}
        from_device = body.get("fromDeviceTemplate")
        if from_device:
            device_template = builtin_device_templates().get(from_device.get("template"))
            if device_template is None:
                raise SetupError(f"no built-in device template '{from_device.get('template')}'")
            keys = from_device.get("keys") or {}
            by_key = {p["name"]: p for p in expand(device_template, from_device["device"], "", equip="x", n=n,
                                                   step=step)}
            for role in template.roles:
                key = keys.get(role.role, role.role)
                if key in by_key:
                    sources[role.role] = {"device": from_device["device"], "address": by_key[key]["address"]}
        for role, spec in (body.get("bindings") or {}).items():
            if "point" in spec:
                sources[role] = {"point": spec["point"].format(n=n) if n is not None else spec["point"]}
            else:
                sources[role] = {"device": spec["device"], "address": spec["address"]}
        return sources

    # ---------- overrides, roll-out, unlink ----------

    async def instance(self, kind: str, name: str) -> dict:
        data = await self.setup.export()
        item, template = self._instance_and_template(data, kind, name)
        points = {p["name"]: p for p in data.get("points", [])}
        roles = {r.role: r for r in template.roles}
        out = {"name": name, "template": item["template"], "roles": []}
        for role in template.roles:
            bound = item["template"]["bindings"].get(role.role)
            entry = {"role": role.role, "required": role.required, "point": bound}
            if bound and bound in points:
                entry["overrides"] = overrides(points[bound], roles[role.role])
            out["roles"].append(entry)
        bound_points = set(item["template"]["bindings"].values())
        owner_key = "space" if kind == "spaces" else "equip"
        out["extraPoints"] = sorted(p["name"] for p in points.values()
                                    if p.get(owner_key) == name and p["name"] not in bound_points)
        return out

    async def reset_override(self, kind: str, name: str, role: str, field: str, client: str) -> dict:
        data = await self.setup.export()
        item, template = self._instance_and_template(data, kind, name)
        role_def = next((r for r in template.roles if r.role == role), None)
        point = next((p for p in data["points"] if p["name"] == item["template"]["bindings"].get(role)), None)
        if role_def is None or point is None or field not in ROLE_FIELDS:
            raise SetupError("no such bound role or field", status=404)
        before = point.get(field)
        value = role_fields(role_def)[field]
        if value is None:
            point.pop(field, None)
        else:
            point[field] = value
        return await self.setup._apply(data, client, "reset-override", "point", point["name"],
                                       f"reset {field} of {point['name']} to the template value",
                                       {field: before}, {field: value}, False)

    async def rollout(self, template_id: str, body: dict, client: str, check_only: bool = False) -> dict:
        """Move instances to another version (decided: all, a group or one at a time).

        body: version (the new one), and which instances: "all": true, or "names": [...], or
        "space": "<room>", or "tag": "<tag>". A point field changes only where it still has the
        old version's value; an override always wins. A role new in this version is listed as
        needing a binding if it is required; a role the new version drops leaves its point as an
        extra point of the equipment. With a draft (a whole setup holding a version not saved
        yet), this previews the roll-out before the version is saved (the template editor).
        """
        data, draft = await self.setup.working_copy(body)
        site = _site_templates(data)
        new = find(site, template_id, body.get("version"))
        if new is None:
            raise SetupError(f"no equipment template '{template_id}' version {body.get('version')}", status=404)
        targets = [(k, i) for k, i in self._instances(data, template_id) if self._picked(i, body)]
        if not targets:
            raise SetupError("no instances picked; send all, names, space or tag")
        points = {p["name"]: p for p in data.get("points", [])}
        preview = []
        for _kind, item in targets:
            link = item["template"]
            old = find(site, template_id, link["version"])
            if old.version == new.version:
                preview.append({"name": item["name"], "from": old.version, "to": new.version, "changes": [],
                                "note": "already on this version"})
                continue
            old_roles, new_roles = {r.role: r for r in old.roles}, {r.role: r for r in new.roles}
            changes, needs_binding, unbound = [], [], []
            for role_name, role in new_roles.items():
                point = points.get(link["bindings"].get(role_name, ""))
                if point is None:
                    if role.required:
                        needs_binding.append(role_name)
                    continue
                if role_name not in old_roles:
                    continue
                old_fields, new_fields = role_fields(old_roles[role_name]), role_fields(role)
                for field in ROLE_FIELDS:
                    if old_fields[field] == new_fields[field]:
                        continue
                    current = point.get(field)
                    if current == old_fields[field] or (current in (None, {}) and old_fields[field] in (None, {})):
                        changes.append({"point": point["name"], "field": field, "old": current,
                                        "new": new_fields[field]})
                        if new_fields[field] is None:
                            point.pop(field, None)
                        else:
                            point[field] = new_fields[field]
                    else:
                        changes.append({"point": point["name"], "field": field, "kept": current,
                                        "template": new_fields[field], "note": "override kept"})
            for role_name in set(old_roles) - set(new_roles):
                if role_name in link["bindings"]:
                    unbound.append({"role": role_name, "point": link["bindings"].pop(role_name)})
            if old.tags != new.tags:  # the template's own tags move; tags added by hand stay
                item["tags"] = {**{k: v for k, v in item.get("tags", {}).items() if k not in old.tags},
                                **new.tags}
            link["version"] = new.version
            preview.append({"name": item["name"], "from": old.version, "to": new.version, "changes": changes,
                            "needsBinding": needs_binding, "unbound": unbound})
        result = await self.setup._apply(data, client, "rollout", "equip-template", template_id,
                                         f"rolled {len(targets)} instances of {template_id} to {new.version}",
                                         None, {"instances": [p["name"] for p in preview]}, check_only, draft)
        return {**result, "instances": preview}

    async def unlink(self, kind: str, name: str, client: str) -> dict:
        data = await self.setup.export()
        item, _ = self._instance_and_template(data, kind, name)
        before = copy.deepcopy(item["template"])
        item.pop("template")
        return await self.setup._apply(data, client, "unlink", kind[:-1], name,
                                       f"unlinked {name} from template {before['id']}", before, None, False)

    # ---------- helpers ----------

    @staticmethod
    def _instances(data: dict, template_id: str) -> list[tuple[str, dict]]:
        return [(kind, i) for kind in ("equips", "spaces") for i in data.get(kind, [])
                if i.get("template") and i["template"]["id"] == template_id]

    @staticmethod
    def _picked(item: dict, body: dict) -> bool:
        if body.get("all"):
            return True
        if body.get("names"):
            return item["name"] in body["names"]
        if body.get("space"):
            return item.get("space") == body["space"] or item["name"] == body["space"]
        if body.get("tag"):
            return body["tag"] in item.get("tags", {})
        return False

    def _instance_and_template(self, data: dict, kind: str, name: str) -> tuple[dict, EquipTemplate]:
        if kind not in ("equips", "spaces"):
            raise SetupError("kind must be equips or spaces", status=404)
        item = next((i for i in data.get(kind, []) if i["name"] == name), None)
        if item is None or not item.get("template"):
            raise SetupError(f"'{name}' is not made from a template", status=404)
        template = find(_site_templates(data), item["template"]["id"], item["template"]["version"])
        if template is None:
            raise SetupError(f"template {item['template']['id']} {item['template']['version']} is missing")
        return item, template

