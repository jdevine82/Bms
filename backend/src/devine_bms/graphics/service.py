"""Graphics in the site setup (milestone 9A). Saved graphics are part of the setup (`graphics`),
so every change is a checked setup change. A site with none is shown starting graphics made
from its setup (generate.py) until an admin saves some."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from devine_bms.config.model import Graphic, SiteConfig
from devine_bms.graphics.generate import generate
from devine_bms.setup.service import SetupError, SetupService


def _dump(g: Graphic) -> dict:
    return g.model_dump(mode="json", by_alias=True, exclude_none=True)


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
            raise SetupError("the graphic is not valid: " + "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:10])) from exc
        if g.name != name:
            raise SetupError(f"the graphic's name is '{g.name}', not '{name}'")
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
