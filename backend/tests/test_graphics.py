"""Graphics (milestone 9A): the model and its checks, the symbol library, starting graphics and
the API. The API tests need BMS_TEST_DATABASE_URL (see test_db_sync.py)."""

import copy
import json
import os
from pathlib import Path

import pytest
from helpers import example_data

from devine_bms.config.validate import ConfigError, parse_config
from devine_bms.graphics.generate import generate
from devine_bms.graphics.library import library

WEB_SYMBOLS = Path(__file__).resolve().parents[2] / "web/public/symbols"
needs_db = pytest.mark.skipif(not os.environ.get("BMS_TEST_DATABASE_URL"), reason="BMS_TEST_DATABASE_URL is not set")


_next = iter(range(100, 1000))


def _point(name, equip, type_, kind, tags, address, space=None, **extra):
    p = {"name": name, "dis": name.replace("_", " ").capitalize(), "device": "sim_meter", "type": type_, "kind": kind,
         "tags": {t: True for t in tags},
         "address": {"objectType": address, "address": next(_next),
                     "dataType": "float32" if address.endswith("Register") else "bit"}, **extra}
    if equip:
        p["equip"] = equip
    if space:
        p["space"] = space
    return p


def site() -> dict:
    """The example site with a plant room (an air handler and a pump) and an office (an indoor unit)."""
    data = example_data(5020)
    data["spaces"] = [{"name": "office", "dis": "Office"}, {"name": "plantroom", "dis": "Plant room"}]
    data["equips"] += [
        {"name": "ahu1", "dis": "AHU 1", "space": "plantroom", "tags": {"ahu": True}},
        {"name": "pump1", "dis": "Pump 1", "space": "plantroom", "tags": {"pump": True, "motor": True}},
        {"name": "ac1", "dis": "Office AC", "space": "office", "tags": {"fcu": True}},
    ]
    data["points"] += [
        _point("ahu1_fan_cmd", "ahu1", "cmd", "Bool", ["fan", "run"], "coil"),
        _point("ahu1_fan_state", "ahu1", "sensor", "Bool", ["fan", "run"], "discreteInput"),
        _point("ahu1_filter", "ahu1", "sensor", "Bool", ["alarm", "filter"], "discreteInput"),
        _point("ahu1_heat", "ahu1", "cmd", "Number", ["heat", "valve"], "holdingRegister", unit="%"),
        _point("ahu1_cool", "ahu1", "cmd", "Number", ["cool", "valve"], "holdingRegister", unit="%"),
        _point("ahu1_sat", "ahu1", "sensor", "Number", ["air", "discharge", "temp"], "inputRegister", unit="°C"),
        _point("pump1_cmd", "pump1", "cmd", "Bool", ["run"], "coil"),
        _point("pump1_state", "pump1", "sensor", "Bool", ["run"], "discreteInput"),
        _point("pump1_alarm", "pump1", "sensor", "Bool", ["alarm"], "discreteInput"),
        _point("ac1_temp", "ac1", "sensor", "Number", ["air", "temp", "zone"], "inputRegister", unit="°C"),
        _point("office_humidity", None, "sensor", "Number", ["air", "humidity", "zone"], "inputRegister",
               space="office", unit="%RH"),
    ]
    return data


def with_graphics(data: dict, graphics: list) -> dict:
    data = copy.deepcopy(data)
    data["graphics"] = graphics
    return data


def errors_of(data: dict) -> list[str]:
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    return [f"{i.where}: {i.message}" for i in exc.value.issues if i.severity.value == "error"]


# ---------- the symbol library ----------

def test_every_library_symbol_has_an_svg_without_scripts():
    ids = [s["id"] for s in library()]
    assert len(ids) == len(set(ids)) >= 17
    for s in library():
        svg = (WEB_SYMBOLS / f"{s['id']}.svg").read_text()
        assert "<script" not in svg and "on" + "load" not in svg
        assert "currentColor" in svg  # recoloured by state and theme
        assert s["kind"] in ("state", "position", "level", "value")
    assert (WEB_SYMBOLS / "LICENSE-equinor.txt").exists()


# ---------- starting graphics ----------

def test_starting_graphics_from_rooms_and_equipment():
    config, _ = parse_config(site())
    graphics = {g.name: g for g in generate(config)}
    assert list(graphics) == ["site", "room_office", "room_plantroom", "plant"]
    assert [g.tab.order for g in graphics.values()] == [0, 10, 20, 30]

    rooms = {i.space: i for i in graphics["site"].items if i.type == "room"}
    assert set(rooms) == {"office", "plantroom"}
    # The office takes its temperature from its indoor unit, with the comfort colour rule.
    assert rooms["office"].values == ["ac1_temp", "office_humidity"]
    assert rooms["office"].rule.point == "ac1_temp" and [b.colour for b in rooms["office"].rule.bands] == \
        ["blue", "green", "amber", "red"]
    assert rooms["plantroom"].values == [] and rooms["plantroom"].rule is None

    plant = {i.id: i for i in graphics["room_plantroom"].items}
    pump = plant["pump1"]
    assert pump.symbol == "pump"
    assert (pump.bind.run, pump.bind.command, pump.bind.fault) == ("pump1_state", "pump1_cmd", "pump1_alarm")
    # The air handler is a duct run that follows its fan.
    fan = plant["ahu1-fan"]
    assert (fan.bind.run, fan.bind.command, fan.bind.fault) == ("ahu1_fan_state", "ahu1_fan_cmd", None)
    assert plant["ahu1-filter"].bind.fault == "ahu1_filter"
    assert plant["ahu1-coil-heating"].bind.value == "ahu1_heat"
    assert plant["ahu1-duct"].rule.type == "follows" and plant["ahu1-duct"].rule.item == "ahu1-fan"
    assert any(i.type == "value" and i.point == "ahu1_sat" for i in plant.values())

    # Equipment in no room (the example's meter) is on the Plant graphic, its symbol showing power.
    meter = next(i for i in graphics["plant"].items if i.type == "symbol")
    assert meter.symbol == "meter" and meter.bind.value == "site_meter1_power"

    # The starting graphics pass the setup checks.
    data = with_graphics(site(), [g.model_dump(mode="json", by_alias=True, exclude_none=True)
                                  for g in graphics.values()])
    parse_config(data)


def test_a_site_with_no_rooms_still_gets_a_site_graphic():
    config, _ = parse_config(example_data(5020))
    graphics = generate(config)
    assert [g.name for g in graphics] == ["site", "plant"]
    assert any(i.type == "text" and "No rooms" in i.text for i in graphics[0].items)


# ---------- checks ----------

def test_graphic_checks():
    bad = with_graphics(site(), [
        {"name": "one", "dis": "One", "kind": "room", "space": "attic", "tab": {"order": 1}, "items": [
            {"type": "symbol", "id": "a", "symbol": "teapot", "x": 0, "y": 0},
            {"type": "symbol", "id": "b", "symbol": "pump", "x": 0, "y": 0,
             "bind": {"run": "nope", "command": "pump1_state"}},
            {"type": "value", "id": "b", "x": 0, "y": 0, "point": "pump1_state"},
            {"type": "duct", "id": "d", "path": [[0, 0], [10, 0]], "rule": {"type": "follows", "item": "c"}},
            {"type": "pipe", "id": "e", "path": [[0, 0], [10, 0]],
             "rule": {"type": "bands", "point": "pump1_state", "bands": [{"below": 5, "colour": "blue"},
                                                                       {"below": 2, "colour": "red"},
                                                                       {"colour": "red"}]}},
            {"type": "pipe", "id": "f", "path": [[0, 0], [10, 0]],
             "rule": {"type": "fill", "point": "ahu1_heat", "min": 10, "max": 10}},
            {"type": "room", "id": "r", "space": "attic", "x": 0, "y": 0, "w": 10, "h": 10},
        ]},
        {"name": "one", "dis": "Again", "kind": "site", "tab": {"order": 1}},
    ])
    errors = errors_of(bad)
    expected = [
        "graphics.one: space 'attic' does not exist",
        "graphics.one: graphic names must be unique",
        "graphics.one: tab order 1 is also used by 'one'",
        "graphics.one.items.b: item IDs must be unique in a graphic",
        "graphics.one.items.a: no symbol 'teapot' in the library",
        "graphics.one.items.b: point 'nope' does not exist",
        "graphics.one.items.b: 'pump1_state' cannot be commanded, so it cannot be a symbol's command",
        "graphics.one.items.d: follows 'c', which is not a symbol in this graphic",
        "graphics.one.items.e: 'pump1_state' is not a number",
        "graphics.one.items.e: the band limits must go up",
        "graphics.one.items.f: the fill's max must be above its min",
        "graphics.one.items.r: space 'attic' does not exist",
    ]
    for e in expected:
        assert e in errors, (e, errors)


def test_the_model_refuses_unknown_item_types_and_colours():
    with pytest.raises(ConfigError):
        parse_config(with_graphics(site(), [{"name": "g", "dis": "G", "kind": "site",
                                             "items": [{"type": "sprite", "id": "a"}]}]))
    with pytest.raises(ConfigError):
        parse_config(with_graphics(site(), [{"name": "g", "dis": "G", "kind": "site", "items": [
            {"type": "pipe", "id": "p", "path": [[0, 0], [1, 1]],
             "rule": {"type": "bands", "point": "ahu1_heat", "bands": [{"colour": "pink"}]}}]}]))


# ---------- the API ----------

@needs_db
async def test_graphics_api():
    from setup_helpers import running_stack

    async for s in running_stack(site()):
        client = s["client"]
        lib = (await client.get("/api/graphics/library")).json()
        assert {x["id"] for x in lib} >= {"pump", "fan", "valve", "damper", "light"}

        # No saved graphics: the starting ones are shown.
        listed = (await client.get("/api/graphics")).json()
        assert listed["source"] == "generated"
        assert [g["name"] for g in listed["graphics"]] == ["site", "room_office", "room_plantroom", "plant"]

        # Changing one saves the starting graphics with it, and it is part of the setup export.
        office = next(g for g in listed["graphics"] if g["name"] == "room_office")
        office["items"].append({"type": "text", "id": "note", "x": 20, "y": 380, "text": "Meeting room"})
        result = (await client.put("/api/setup/graphics/room_office", json=office)).json()
        assert result["applied"], result
        listed = (await client.get("/api/graphics")).json()
        assert listed["source"] == "setup" and len(listed["graphics"]) == 4
        exported = (await client.get("/api/setup/config")).json()
        saved = next(g for g in exported["graphics"] if g["name"] == "room_office")
        assert saved["items"][-1]["text"] == "Meeting room"

        # A graphic that fails the checks is refused with the reason.
        bad = copy.deepcopy(office)
        bad["items"].append({"type": "value", "id": "v", "x": 0, "y": 0, "point": "nope"})
        refused = await client.put("/api/setup/graphics/room_office", json=bad)
        assert refused.status_code == 422
        assert any("point 'nope' does not exist" in i["message"] for i in refused.json()["issues"])
        wrong_name = await client.put("/api/setup/graphics/other", json=office)
        assert wrong_name.status_code == 422

        # Remove one; regenerate them all.
        assert (await client.delete("/api/setup/graphics/plant")).json()["applied"]
        assert [g["name"] for g in (await client.get("/api/graphics")).json()["graphics"]] == \
            ["site", "room_office", "room_plantroom"]
        assert (await client.delete("/api/setup/graphics/plant")).status_code == 404
        made = (await client.post("/api/setup/graphics/generate")).json()
        assert made["applied"]
        listed = (await client.get("/api/graphics")).json()
        assert listed["source"] == "setup" and len(listed["graphics"]) == 4
        assert not any(i["id"] == "note" for g in listed["graphics"] for i in g["items"])

        # The audit log has the changes.
        audit = (await client.get("/api/audit", params={"kind": "graphic"})).json()
        assert sorted(a["action"] for a in audit) == ["delete", "generate", "put"]


def test_published_schema_includes_graphics():
    schema = json.loads((Path(__file__).resolve().parents[2] / "config/schema/devine-bms-config-0.schema.json")
                        .read_text())
    assert "graphics" in schema["properties"]


# ---------- milestone 9B: sanitising, custom symbols, backgrounds, tabs ----------

from devine_bms.graphics.sanitize import SanitizeError, is_clean, sanitize  # noqa: E402

EVIL = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="64" height="64" onload="alert(1)">
 <metadata>editor data</metadata>
 <script>alert(1)</script>
 <style>@import url(http://evil/x.css);</style>
 <defs><linearGradient id="grad"><stop offset="0" stop-color="red"/></linearGradient></defs>
 <g inkscape:label="Off" inkscape:groupmode="layer"><rect x="1" y="1" width="10" height="10" fill="url(#grad)" onclick="x()"/></g>
 <g id="on"><circle cx="5" cy="5" r="3" style="fill:red;background:url(http://evil/x.png)"/>
   <animateTransform attributeName="transform" type="rotate" from="0 32 32" to="360 32 32" dur="1s" repeatCount="indefinite"/>
   <set attributeName="href" to="javascript:alert(1)"/>
   <text x="2" y="60">T <tspan>1</tspan> end</text>
 </g>
 <image xlink:href="http://evil/x.png"/>
 <use xlink:href="#grad"/><use href="https://evil/#x"/>
 <foreignObject><div>x</div></foreignObject>
</svg>"""


def test_sanitizing_removes_everything_unsafe_and_keeps_the_drawing():
    c = sanitize(EVIL, "symbol", "my-pump")
    lowered = c.svg.lower()
    for bad in ("script", "onload", "onclick", "evil", "javascript", "foreignobject", "<image", "@import", "<style",
                "metadata", "inkscape"):
        assert bad not in lowered, bad
    assert c.view_box == "0 0 64 64"  # from width and height
    assert 'class="state-off"' in c.svg and 'class="state-on"' in c.svg
    assert 'id="my-pump-grad"' in c.svg and 'fill="url(#my-pump-grad)"' in c.svg and 'href="#my-pump-grad"' in c.svg
    assert "<animateTransform" in c.svg  # SMIL animation is allowed
    assert "T <tspan>1</tspan> end</text>" in c.svg  # text is kept
    assert {"<script>", "<image>", "<foreignObject>", "2 × event handlers"} <= set(c.removed)
    # Cleaning is stable: a cleaned SVG is clean.
    assert sanitize(c.svg, "symbol", "my-pump").svg == c.svg
    assert is_clean(c.svg, "symbol", "my-pump") is None
    assert is_clean(EVIL, "symbol", "my-pump") is not None


@pytest.mark.parametrize("text, reason", [
    ('<!DOCTYPE svg [<!ENTITY a "b">]><svg/>', "DOCTYPE"),
    ("<html/>", "not an SVG"),
    ("<svg", "not a readable SVG"),
    ('<svg viewBox="0 0 10 10"><g id="off"/></svg>', "missing: on"),
    ("<svg><g id='off'/><g id='on'/></svg>", "no viewBox"),
    ('<svg viewBox="0 0 0 10"><g id="off"/><g id="on"/></svg>', "positive size"),
])
def test_sanitizing_refuses(text, reason):
    with pytest.raises(SanitizeError, match=reason):
        sanitize(text, "symbol", "x")


def test_size_limits():
    big = '<svg viewBox="0 0 10 10"><g id="off"/><g id="on"/>' + "<!--" + "x" * 70000 + "--></svg>"
    with pytest.raises(SanitizeError, match="at most 64 KB"):
        sanitize(big, "symbol", "x")
    assert sanitize(big, "background", "bg").svg == '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><g id="bg-off" /><g id="bg-on" /></svg>'


def _symbol(sym_id="my-fan"):
    return {"id": sym_id, "dis": "My fan", "kind": "state", "animation": "spin",
            "svg": sanitize('<svg viewBox="0 0 64 64"><g id="off"><circle cx="32" cy="32" r="20"/></g>'
                            '<g id="on"><circle cx="32" cy="32" r="20" fill="currentColor"/></g></svg>',
                            "symbol", sym_id).svg}


def test_custom_symbols_and_backgrounds_in_the_checks():
    data = site()
    data["symbols"] = [_symbol(), {**_symbol("pump"), "dis": "Clash"},
                       {**_symbol("dirty"), "svg": '<svg viewBox="0 0 64 64" onload="x()"><g id="off"/><g id="on"/></svg>'}]
    data["graphics"] = [{"name": "g", "dis": "G", "kind": "system",
                         "background": {"svg": "<svg viewBox='0 0 10 10'><script/></svg>", "w": 100, "h": 100},
                         "items": [{"type": "symbol", "id": "f", "symbol": "my-fan", "x": 0, "y": 0}]}]
    errors = errors_of(data)
    assert "symbols.pump: 'pump' is a library symbol; give the site's symbol another ID" in errors
    assert any(e.startswith("symbols.dirty: the symbol's SVG is refused") for e in errors)
    assert any(e.startswith("graphics.g.background: the background is refused") for e in errors)
    assert not any("my-fan" in e for e in errors)  # a graphic can use the site's symbol
    data["symbols"] = [_symbol()]
    data["graphics"][0]["background"]["svg"] = sanitize("<svg viewBox='0 0 10 10'><rect width='5' height='5'/></svg>",
                                                        "background", "bg-g").svg
    parse_config(data)


@needs_db
async def test_symbols_backgrounds_and_tabs_api():
    from setup_helpers import running_stack

    async for s in running_stack(site()):
        client = s["client"]
        cleaned = (await client.post("/api/setup/graphics/sanitize", json={"svg": EVIL, "purpose": "symbol",
                                                                           "prefix": "imp"})).json()
        assert "script" not in cleaned["svg"] and cleaned["viewBox"] == "0 0 64 64" and "<script>" in cleaned["removed"]
        refused = await client.post("/api/setup/graphics/sanitize", json={"svg": "<html/>", "purpose": "symbol"})
        assert refused.status_code == 422 and "not an SVG" in refused.json()["error"]

        # A symbol sent as the file was read is cleaned before it is saved.
        put = (await client.put("/api/setup/graphics/symbols/imported-pump",
                                json={"dis": "Imported pump", "svg": EVIL, "animation": "spin"})).json()
        assert put["applied"], put
        lib = {x["id"]: x for x in (await client.get("/api/graphics/library")).json()}
        assert lib["imported-pump"]["source"] == "site" and "onload" not in lib["imported-pump"]["svg"]
        assert 'id="imported-pump-grad"' in lib["imported-pump"]["svg"] and "pump" in lib  # library ones too
        clash = await client.put("/api/setup/graphics/symbols/pump", json={"dis": "x", "svg": EVIL})
        assert clash.status_code == 422

        # A graphic using it, with a background, saved; the background is cleaned too.
        g = {"name": "custom", "dis": "Custom", "kind": "system", "tab": {"order": 500},
             "background": {"svg": "<svg width='200' height='100'><rect width='10' height='10' onclick='x()'/></svg>",
                            "w": 1000, "h": 500, "opacity": 0.4},
             "items": [{"type": "symbol", "id": "p", "symbol": "imported-pump", "x": 100, "y": 100}]}
        assert (await client.put("/api/setup/graphics/custom", json=g)).json()["applied"]
        saved = next(x for x in (await client.get("/api/graphics")).json()["graphics"] if x["name"] == "custom")
        assert "onclick" not in saved["background"]["svg"] and 'viewBox="0 0 200 100"' in saved["background"]["svg"]
        exported = (await client.get("/api/setup/config")).json()
        assert [x["id"] for x in exported["symbols"]] == ["imported-pump"]

        in_use = await client.delete("/api/setup/graphics/symbols/imported-pump")
        assert in_use.status_code == 422 and "still used on Custom" in in_use.json()["error"]

        # Tabs in one step: these, in this order; the rest stop being tabs.
        r = (await client.put("/api/setup/graphics-tabs", json={"order": ["site", "custom", "room_office"]})).json()
        assert r["applied"], r
        graphics = (await client.get("/api/graphics")).json()["graphics"]
        assert [(x["name"], x.get("tab")) for x in graphics][:3] == [
            ("site", {"order": 0}), ("custom", {"order": 10}), ("room_office", {"order": 20})]
        assert all("tab" not in x for x in graphics[3:])
        bad = await client.put("/api/setup/graphics-tabs", json={"order": ["site", "nope"]})
        assert bad.status_code == 422

        # Once unused, it can be removed.
        g["items"] = []
        await client.put("/api/setup/graphics/custom", json=g)
        assert (await client.delete("/api/setup/graphics/symbols/imported-pump")).json()["applied"]
