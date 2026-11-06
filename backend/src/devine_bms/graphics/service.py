"""Graphics in the site setup (milestone 9A). Saved graphics are part of the setup (`graphics`),
so every change is a checked setup change. A site with none is shown starting graphics made
from its setup (generate.py) until an admin saves some."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from devine_bms.config.model import CustomSymbol, Graphic, SiteConfig
from devine_bms.graphics.generate import generate
from devine_bms.graphics.library import library, symbol_ids
from devine_bms.graphics.sanitize import SanitizeError, sanitize
from devine_bms.setup.service import SetupError, SetupService


def _dump(g: Graphic) -> dict:
    return g.model_dump(mode="json", by_alias=True, exclude_none=True)


def _invalid(what: str, exc: ValidationError) -> SetupError:
    return SetupError(f"the {what} is not valid: " + "; ".join(
        f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]))


def _order(g: dict) -> tuple:
    tab = g.get("tab")
    return (0 if tab else 1, tab["order"] if tab else 0, g["dis"].lower())


class GraphicsService:
    def __init__(self, setup: SetupService) -> None:
        self.setup = setup

    async def list(self) -> dict:
        data = await self.setup.export()
        saved = data.get("graphics") or []
        if saved:
            return {"source": "setup", "graphics": sorted(saved, key=_order)}
        return {"source": "generated", "graphics": [_dump(g) for g in generate(SiteConfig.model_validate(data))]}

    async def put(self, name: str, body: dict, client: Any, check_only: bool = False) -> dict:
        try:
            g = Graphic.model_validate(body)
        except ValidationError as exc:
            raise _invalid("graphic", exc) from exc
        if g.name != name:
            raise SetupError(f"the graphic's name is '{g.name}', not '{name}'")
        if g.background is not None:  # cleaned here, so an editor can send the file as read
            try:
                g.background.svg = sanitize(g.background.svg, "background", f"bg-{g.name}").svg
            except SanitizeError as exc:
                raise SetupError(f"the background is refused: {exc}") from exc
        data = await self.setup.export()
        graphics = list(data.get("graphics") or [])
        if not graphics:  # the first change saves the starting graphics with it
            graphics = [_dump(x) for x in generate(SiteConfig.model_validate(data))]
        before = next((x for x in graphics if x["name"] == name), None)
        graphics = [x for x in graphics if x["name"] != name] + [_dump(g)]
        data["graphics"] = graphics
        return await self.setup._apply(data, client, "put", "graphic", name,
                                       f"{'changed' if before else 'added'} graphic {g.dis}", before, _dump(g), check_only)

    async def delete(self, name: str, client: Any) -> dict:
        data = await self.setup.export()
        graphics = list(data.get("graphics") or [])
        before = next((x for x in graphics if x["name"] == name), None)
        if before is None:
            raise SetupError(f"no saved graphic '{name}'", status=404)
        data["graphics"] = [x for x in graphics if x["name"] != name]
        return await self.setup._apply(data, client, "delete", "graphic", name, f"removed graphic {before['dis']}",
                                       before, None, False)

    async def generate(self, client: Any, check_only: bool = False) -> dict:
        """Replace the saved graphics with freshly made starting graphics."""
        data = await self.setup.export()
        made = [_dump(g) for g in generate(SiteConfig.model_validate(data))]
        data["graphics"] = made
        return await self.setup._apply(data, client, "generate", "graphic", None,
                                       f"made {len(made)} starting graphics", None, [g["name"] for g in made], check_only)

    # ---------- the library and the site's own symbols (milestone 9B) ----------

    async def library(self) -> list[dict]:
        data = await self.setup.export()
        site = [{"id": x["id"], "dis": x["dis"], "category": x.get("category", "Site symbols"), "source": "site",
                 "kind": x.get("kind", "state"), "animation": x.get("animation", "none"), "svg": x["svg"],
                 **({"shapes": x["shapes"]} if x.get("shapes") is not None else {})}
                for x in data.get("symbols") or []]
        return [*library(), *sorted(site, key=lambda x: x["dis"].lower())]

    @staticmethod
    def clean(body: dict) -> dict:
        purpose = body.get("purpose", "symbol")
        if purpose not in ("symbol", "background") or not isinstance(body.get("svg"), str):
            raise SetupError("send {svg, purpose}, where purpose is symbol or background")
        try:
            c = sanitize(body["svg"], purpose, str(body.get("prefix") or "import"))
        except SanitizeError as exc:
            raise SetupError(str(exc)) from exc
        return {"svg": c.svg, "viewBox": c.view_box, "removed": c.removed}

    async def put_symbol(self, symbol_id: str, body: dict, client: Any, check_only: bool = False) -> dict:
        if symbol_id in symbol_ids():
            raise SetupError(f"'{symbol_id}' is a library symbol; give the site's symbol another ID")
        try:
            sym = CustomSymbol.model_validate({**body, "id": symbol_id})
            sym.svg = sanitize(sym.svg, "symbol", symbol_id).svg
        except ValidationError as exc:
            raise _invalid("symbol", exc) from exc
        except SanitizeError as exc:
            raise SetupError(f"the symbol's SVG is refused: {exc}") from exc
        data = await self.setup.export()
        symbols = list(data.get("symbols") or [])
        before = next((x for x in symbols if x["id"] == symbol_id), None)
        data["symbols"] = [x for x in symbols if x["id"] != symbol_id] + [
            sym.model_dump(mode="json", by_alias=True, exclude_none=True)]
        return await self.setup._apply(data, client, "put", "symbol", symbol_id,
                                       f"{'changed' if before else 'added'} symbol {sym.dis}",
                                       {"dis": before["dis"]} if before else None, {"dis": sym.dis}, check_only)

    async def delete_symbol(self, symbol_id: str, client: Any) -> dict:
        data = await self.setup.export()
        symbols = list(data.get("symbols") or [])
        before = next((x for x in symbols if x["id"] == symbol_id), None)
        if before is None:
            raise SetupError(f"no site symbol '{symbol_id}'", status=404)
        users = [g["dis"] for g in data.get("graphics") or []
                 if any(i.get("type") == "symbol" and i.get("symbol") == symbol_id for i in g.get("items", []))]
        if users:
            raise SetupError(f"still used on {', '.join(users[:10])}; remove it from those graphics first")
        data["symbols"] = [x for x in symbols if x["id"] != symbol_id]
        return await self.setup._apply(data, client, "delete", "symbol", symbol_id, f"removed symbol {before['dis']}",
                                       {"dis": before["dis"]}, None, False)

    async def put_tabs(self, order: list[str], client: Any) -> dict:
        """Make `order` the tabs, in that order; every other graphic stops being a tab."""
        data = await self.setup.export()
        graphics = list(data.get("graphics") or []) or [_dump(x) for x in generate(SiteConfig.model_validate(data))]
        names = {g["name"] for g in graphics}
        unknown = [n for n in order if n not in names]
        if unknown or len(set(order)) != len(order):
            raise SetupError(f"unknown or repeated graphics: {unknown or order}")
        for g in graphics:
            if g["name"] in order:
                g["tab"] = {"order": order.index(g["name"]) * 10}
            else:
                g.pop("tab", None)
        data["graphics"] = graphics
        return await self.setup._apply(data, client, "put", "graphic-tabs", None, "changed the graphics tabs",
                                       None, order, False)
