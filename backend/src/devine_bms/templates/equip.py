"""Equipment templates (main spec 5.1.2, milestone 5A).

An equipment template describes a type of equipment once: its point roles and the point
fields each role gives (display name, type, kind, unit, tags, speed class, logging).
Equipment made from it (an instance) binds each role to one of its points.

Overrides are worked out, not stored: a point field that differs from what its role gives
is an override. When a new template version rolls out, a field changes only where the point
still has the old version's value, so an override always wins (decided).

Built-in starter templates ship in templates/equipment/; a site's own templates are part
of the site configuration (`equipTemplates`), with every saved version.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from devine_bms.config.model import EquipTemplate, TemplateRole

ROLE_FIELDS = ("dis", "type", "kind", "unit", "enum", "tags", "pollClass", "staleAfterS", "his", "alarms", "writeLimits")


def _version_key(version: str) -> tuple:
    return tuple(int(x) if x.isdigit() else x for x in version.replace("-", ".").split("."))


@lru_cache(maxsize=1)
def builtin_equip_templates() -> dict[str, EquipTemplate]:
    folder = resources.files("devine_bms.templates") / "equipment"
    found = {}
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            t = EquipTemplate.model_validate(json.loads(entry.read_text(encoding="utf-8")))
            found[t.id] = t
    return found


def all_versions(site_templates: list[EquipTemplate]) -> dict[str, list[EquipTemplate]]:
    """Every template version by ID, oldest first: built-in first, then the site's own."""
    out: dict[str, list[EquipTemplate]] = {t.id: [t] for t in builtin_equip_templates().values()}
    for t in site_templates:
        out.setdefault(t.id, []).append(t)
    return {k: sorted(v, key=lambda t: _version_key(t.version)) for k, v in out.items()}


def find(site_templates: list[EquipTemplate], template_id: str, version: str | None = None) -> EquipTemplate | None:
    versions = all_versions(site_templates).get(template_id, [])
    if version is None:
        return versions[-1] if versions else None
    return next((t for t in versions if t.version == version), None)


def role_fields(role: TemplateRole) -> dict[str, Any]:
    """The point fields a role gives, in the config file's keys."""
    data = role.model_dump(mode="json", by_alias=True)
    return {k: data.get(k) for k in ROLE_FIELDS}


def point_name(room: str | None, equip: str, role: str) -> str:
    """<room>_<equipment>_<function> (main spec 5.2); `site` when there is no room."""
    return f"{room or 'site'}_{equip}_{role}"


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, dict) or isinstance(b, dict):
        return (a or {}) == (b or {})
    if isinstance(a, list) or isinstance(b, list):  # alarms: none and an empty list are the same
        return (a or []) == (b or [])
    return a == b


def overrides(point: dict, role: TemplateRole) -> dict[str, dict]:
    """Fields where the point differs from what the role gives: {field: {template, point}}."""
    out = {}
    for field, value in role_fields(role).items():
        if not _same(point.get(field), value):
            out[field] = {"template": value, "point": point.get(field)}
    return out


def diff(old: EquipTemplate, new: EquipTemplate) -> dict:
    """What changes between two versions: roles added, removed and changed (field by field)."""
    old_roles = {r.role: r for r in old.roles}
    new_roles = {r.role: r for r in new.roles}
    changed = {}
    for name in sorted(set(old_roles) & set(new_roles)):
        a, b = role_fields(old_roles[name]), role_fields(new_roles[name])
        fields = {f: {"old": a[f], "new": b[f]} for f in ROLE_FIELDS if not _same(a[f], b[f])}
        if old_roles[name].required != new_roles[name].required:
            fields["required"] = {"old": old_roles[name].required, "new": new_roles[name].required}
        if fields:
            changed[name] = fields
    other = {}
    for field in ("dis", "tags", "parameters", "graphic", "logic"):
        a, b = getattr(old, field), getattr(new, field)
        if not _same(a, b):
            other[field] = {"old": a, "new": b}
    return {"from": old.version, "to": new.version,
            "added": sorted(set(new_roles) - set(old_roles)),
            "removed": sorted(set(old_roles) - set(new_roles)),
            "changed": changed, "template": other}
