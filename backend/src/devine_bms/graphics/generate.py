"""Starting graphics (milestone 9A): made from a site's setup when it has none.

A Site graphic with every room as a rectangle (its temperature, with a colour rule, when it has
one), one graphic per room with its equipment's symbols bound by tags and laid out in rows, and a
Plant graphic for equipment that is in no room. They are plain graphics: an admin can save them
into the setup, regenerate them, or edit them.
"""

from __future__ import annotations

from devine_bms.config.model import (
    BandsRule,
    FollowsRule,
    Graphic,
    GraphicTab,
    LineItem,
    Point,
    RoomItem,
    SiteConfig,
    SymbolBindings,
    SymbolItem,
    TextItem,
    ValueBand,
    ValueItem,
)
from devine_bms.config.validate import writable

WIDTH = 1000
ROOM_W, ROOM_H, GAP = 220, 130, 20
CELL_W, CELL_H = 300, 230
LABEL_MAX = 26
AHU_W = 480

# Equipment tag -> symbol, first match wins (the AHU is drawn as a run of symbols).
SYMBOL_BY_TAG = [
    ("pump", "pump"), ("fcu", "fan"), ("heatExchanger", "heat-recovery"), ("fan", "fan"),
    ("lighting", "light"), ("meter", "meter"), ("chiller", "compressor"), ("heatPump", "compressor"),
    ("boiler", "coil-heating"), ("tank", "tank"), ("door", "door"), ("valve", "valve-control"),
    ("damper", "damper"), ("filter", "filter"),
]


def _bands(point: Point) -> BandsRule:
    """Cold, comfortable, warm, hot."""
    f = bool(point.unit and "F" in point.unit)
    limits = (64, 77, 82) if f else (18, 25, 28)
    return BandsRule(type="bands", point=point.name, hysteresis=1 if f else 0.5, bands=[
        ValueBand(below=limits[0], colour="blue"), ValueBand(below=limits[1], colour="green"),
        ValueBand(below=limits[2], colour="amber"), ValueBand(colour="red")])


def _find(points: list[Point], tags: set[str], type_: str | None = None, kind: str | None = None,
          without: set[str] = frozenset()) -> Point | None:
    for p in points:
        if tags <= set(p.tags) and not (without & set(p.tags)) \
                and (type_ is None or p.type.value == type_) and (kind is None or p.kind.value == kind):
            return p
    return None


def _label(text: str) -> str:
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1].rstrip() + "…"


def _name(p: Point | None) -> str | None:
    return p.name if p else None


class _Ids:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def __call__(self, base: str) -> str:
        base = base[:56].replace("_", "-") or "item"
        out, n = base, 2
        while out in self.used:
            out, n = f"{base}-{n}", n + 1
        self.used.add(out)
        return out


def _run_bindings(points: list[Point], without_fault: set[str] = frozenset()) -> SymbolBindings:
    state = _find(points, {"run"}, "sensor", "Bool")
    cmd = _find(points, {"run"}, "cmd", "Bool")
    fault = _find(points, {"alarm"}, "sensor", "Bool", without=without_fault)
    return SymbolBindings(run=_name(state or cmd), fault=_name(fault),
                          command=cmd.name if cmd is not None and writable(cmd) else None)


def _values(points: list[Point], skip: set[str], limit: int) -> list[Point]:
    """The equipment's numbers worth showing: temperatures, setpoints, then the rest."""
    numbers = [p for p in points if p.kind.value == "Number" and p.name not in skip]
    numbers.sort(key=lambda p: (0 if "temp" in p.tags else 1, 0 if p.type.value == "sp" else 1))
    return numbers[:limit]


def _equip_cell(equip, points: list[Point], x: float, y: float, ids: _Ids) -> list:
    """One piece of equipment: its symbol, name and a few readings."""
    tags = set(equip.tags)
    symbol = next((s for tag, s in SYMBOL_BY_TAG if tag in tags), None)
    items: list = []
    used: set[str] = set()
    cx = x + CELL_W / 2
    if symbol is not None:
        bind = _run_bindings(points)
        if symbol == "meter":
            bind.value = _name(_find(points, {"power"}, "sensor", "Number"))
        items.append(SymbolItem(type="symbol", id=ids(equip.name), symbol=symbol, x=cx - 40, y=y, size=80,
                                label=equip.dis, bind=bind))
        used |= {n for n in (bind.run, bind.value, bind.fault, bind.command) if n}
    else:
        items.append(TextItem(type="text", id=ids(equip.name), x=cx, y=y + 40, text=equip.dis, size=16))
    for i, p in enumerate(_values(points, used, 3)):
        items.append(ValueItem(type="value", id=ids(p.name), x=cx, y=y + 130 + i * 26, point=p.name, label=_label(p.dis)))
    return items


def _ahu(equip, points: list[Point], x: float, y: float, ids: _Ids) -> list:
    """An air handler as a duct run: outside air damper, filter, heating and cooling coils, fan."""
    fan = _run_bindings(points, without_fault={"filter"})
    parts = [
        ("damper", "Outside air", SymbolBindings(value=_name(_find(points, {"damper", "outside"}, kind="Number")))),
        ("filter", "Filter", SymbolBindings(fault=_name(_find(points, {"filter", "alarm"}, "sensor", "Bool")))),
        ("coil-heating", "Heating", SymbolBindings(value=_name(_find(points, {"heat", "valve"}, kind="Number")))),
        ("coil-cooling", "Cooling", SymbolBindings(value=_name(_find(points, {"cool", "valve"}, kind="Number")))),
        ("fan", equip.dis, fan),
    ]
    items: list = []
    fan_id = None
    step = AHU_W / len(parts)
    mid = y + 32
    symbol_ids = []
    for i, (symbol, label, bind) in enumerate(parts):
        sid = ids(f"{equip.name}-{symbol}")
        symbol_ids.append(sid)
        if symbol == "fan":
            fan_id = sid
        items.append(SymbolItem(type="symbol", id=sid, symbol=symbol, x=x + i * step + (step - 64) / 2, y=y,
                                size=64, label=label, bind=bind))
    duct = LineItem(type="duct", id=ids(f"{equip.name}-duct"), path=[(x, mid), (x + AHU_W, mid)], width=14,
                    rule=FollowsRule(type="follows", item=fan_id) if fan.run else None)
    used = {n for _s, _l, b in parts for n in (b.run, b.value, b.fault, b.command) if n}
    values = [ValueItem(type="value", id=ids(p.name), x=x + 120 + (i % 2) * 240, y=y + 120 + (i // 2) * 26,
                        point=p.name, label=_label(p.dis))
              for i, p in enumerate(_values(points, used, 6))]
    return [duct, *items, *values]


def _equipment_rows(equips, points_of, top: float, ids: _Ids) -> tuple[list, float]:
    items: list = []
    x, y = 20.0, top
    for e in equips:
        pts = points_of.get(e.name, [])
        width = AHU_W + 20 if "ahu" in e.tags else CELL_W
        if x + width > WIDTH and x > 20:
            x, y = 20.0, y + CELL_H
        items += _ahu(e, pts, x, y, ids) if "ahu" in e.tags else _equip_cell(e, pts, x, y, ids)
        x += width + 20
    bottom = y + CELL_H if equips else top
    return items, bottom


def generate(config: SiteConfig) -> list[Graphic]:
    points_of: dict[str, list[Point]] = {}
    room_points: dict[str, list[Point]] = {}
    for p in config.points:
        if p.equip:
            points_of.setdefault(p.equip, []).append(p)
        elif p.space:
            room_points.setdefault(p.space, []).append(p)
    equips_in: dict[str | None, list] = {}
    for e in config.equips:
        equips_in.setdefault(e.space, []).append(e)

    def room_temp(space: str) -> Point | None:
        own = _find(room_points.get(space, []), {"temp", "zone"}, "sensor", "Number")
        if own:
            return own
        for e in equips_in.get(space, []):
            found = _find(points_of.get(e.name, []), {"temp", "zone"}, "sensor", "Number")
            if found:
                return found
        return None

    graphics: list[Graphic] = []

    # The Site graphic: every room as a rectangle.
    ids = _Ids()
    site_items: list = [TextItem(type="text", id=ids("title"), x=20, y=30, text=config.site.dis, size=22)]
    cols = max(1, (WIDTH - GAP) // (ROOM_W + GAP))
    for i, s in enumerate(config.spaces):
        x, y = GAP + (i % cols) * (ROOM_W + GAP), 60 + (i // cols) * (ROOM_H + GAP)
        temp = room_temp(s.name)
        extra = [p.name for p in (_find(room_points.get(s.name, []), {"humidity"}, "sensor", "Number"),
                                  _find(room_points.get(s.name, []), {"co2"}, "sensor", "Number")) if p]
        site_items.append(RoomItem(type="room", id=ids(s.name), space=s.name, x=x, y=y, w=ROOM_W, h=ROOM_H,
                                   values=([temp.name] if temp else []) + extra,
                                   rule=_bands(temp) if temp else None))
    if not config.spaces:
        site_items.append(TextItem(type="text", id=ids("empty"), x=20, y=80, size=16,
                                   text="No rooms are set up yet. Add rooms in Setup to see them here."))
    rows = (len(config.spaces) + cols - 1) // cols
    graphics.append(Graphic(name="site", dis="Site", kind="site", tab=GraphicTab(order=0),
                            height=max(300, 60 + rows * (ROOM_H + GAP) + GAP), items=site_items))

    # One graphic per room.
    for n, s in enumerate(config.spaces, start=1):
        ids = _Ids()
        items: list = [TextItem(type="text", id=ids("title"), x=20, y=30, text=s.dis, size=22)]
        for i, p in enumerate(_values(room_points.get(s.name, []), set(), 6)):
            items.append(ValueItem(type="value", id=ids(p.name), x=140 + (i % 3) * 300, y=70 + (i // 3) * 26,
                                   point=p.name, label=_label(p.dis)))
        top = 130 if room_points.get(s.name) else 70
        equip_items, bottom = _equipment_rows(equips_in.get(s.name, []), points_of, top, ids)
        items += equip_items
        graphics.append(Graphic(name=f"room_{s.name}"[:100], dis=s.dis, kind="room", space=s.name,
                                tab=GraphicTab(order=n * 10), height=max(400, bottom + 20), items=items))

    # Equipment in no room.
    plant = equips_in.get(None, [])
    if plant:
        ids = _Ids()
        equip_items, bottom = _equipment_rows(plant, points_of, 70, ids)
        graphics.append(Graphic(name="plant", dis="Plant", kind="system",
                                tab=GraphicTab(order=(len(config.spaces) + 1) * 10), height=max(400, bottom + 20),
                                items=[TextItem(type="text", id=ids("title"), x=20, y=30, text="Plant", size=22),
                                       *equip_items]))
    return graphics
