"""Cleaning imported SVGs (main spec 6.4, milestone 9B, decision 026).

An allow-list: only drawing elements and their attributes are kept. Scripts, foreignObject,
images, style sheets, fonts, event handlers and links outside the file are removed; a DOCTYPE or
entity is refused. IDs get a prefix so they cannot clash with the page. The result is stable:
cleaning a cleaned SVG gives the same text, which is how the setup checks know a stored SVG is
clean.

For a symbol, the groups with the ID `off` and `on` (or Inkscape layers labelled Off and On) are
marked with the classes `state-off` and `state-on`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
INKSCAPE = "http://www.inkscape.org/namespaces/inkscape"
ET.register_namespace("", SVG)

LIMITS = {"symbol": 64 * 1024, "background": 1024 * 1024}
MAX_ELEMENTS = 5000

ELEMENTS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title",
    "desc", "defs", "linearGradient", "radialGradient", "stop", "clipPath", "mask", "marker", "use", "symbol",
    "animate", "animateTransform", "animateMotion", "set", "mpath",
}
DROP_QUIETLY = {"metadata"}  # editor housekeeping, not worth reporting

ATTRS = {
    # structure and geometry
    "id", "class", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height", "d", "points",
    "transform", "viewBox", "preserveAspectRatio", "href", "dx", "dy", "rotate", "textLength", "lengthAdjust",
    "pathLength", "gradientUnits", "gradientTransform", "spreadMethod", "fx", "fy", "fr", "offset",
    "clipPathUnits", "maskUnits", "maskContentUnits", "markerUnits", "markerWidth", "markerHeight", "refX", "refY",
    "orient",
    # presentation
    "style", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "opacity", "color",
    "display", "visibility", "clip-path", "clip-rule", "mask", "marker-start", "marker-mid", "marker-end",
    "stop-color", "stop-opacity", "font-family", "font-size", "font-weight", "font-style", "text-anchor",
    "dominant-baseline", "alignment-baseline", "letter-spacing", "word-spacing", "text-decoration",
    "vector-effect", "paint-order", "shape-rendering", "transform-origin",
    # SMIL animation
    "attributeName", "attributeType", "from", "to", "by", "values", "keyTimes", "keySplines", "calcMode", "dur",
    "begin", "end", "repeatCount", "repeatDur", "fill-mode", "additive", "accumulate", "type", "path",
    "keyPoints", "restart", "min", "max",
}
BAD_CSS = re.compile(r"url\(\s*['\"]?(?!#)|expression\s*\(|javascript:|@import|behavior\s*:|-moz-binding",
                     re.IGNORECASE)
URL_REF = re.compile(r"url\(\s*(['\"]?)#([^)'\"]+)\1\s*\)")


class SanitizeError(ValueError):
    pass


@dataclass
class Cleaned:
    svg: str
    view_box: str
    removed: list[str] = field(default_factory=list)


def _local(tag: str) -> tuple[str | None, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return None, tag


def sanitize(text: str, purpose: Literal["symbol", "background"], prefix: str) -> Cleaned:
    raw = text.encode() if isinstance(text, str) else text
    if len(raw) > LIMITS[purpose]:
        raise SanitizeError(f"the file is {len(raw) // 1024} KB; a {purpose} can be at most "
                            f"{LIMITS[purpose] // 1024} KB")
    if re.search(rb"<!(DOCTYPE|ENTITY)", raw, re.IGNORECASE):
        raise SanitizeError("the file has a DOCTYPE or entities, which are not allowed")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SanitizeError(f"this is not a readable SVG file: {exc}") from exc
    ns, name = _local(root.tag)
    if name != "svg" or ns not in (SVG, None):
        raise SanitizeError("this is not an SVG file (its top element is not <svg>)")
    if sum(1 for _ in root.iter()) > MAX_ELEMENTS:
        raise SanitizeError(f"the file has more than {MAX_ELEMENTS} elements")

    removed: Counter[str] = Counter()
    prefix = re.sub(r"[^A-Za-z0-9_-]", "-", prefix)

    # The view box: from viewBox, else from width and height.
    view_box = root.get("viewBox")
    if not view_box:
        try:
            w = float(re.sub(r"[a-z%]+$", "", root.get("width", "")))
            h = float(re.sub(r"[a-z%]+$", "", root.get("height", "")))
        except ValueError as exc:
            raise SanitizeError("the SVG has no viewBox and no width and height") from exc
        view_box = f"0 0 {w:g} {h:g}"
    parts = view_box.replace(",", " ").split()
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise SanitizeError(f"the viewBox '{view_box}' is not four numbers") from exc
    if len(nums) != 4 or nums[2] <= 0 or nums[3] <= 0:
        raise SanitizeError(f"the viewBox '{view_box}' is not four numbers with a positive size")
    view_box = " ".join(f"{n:g}" for n in nums)

    # Symbol state groups, found before the Inkscape attributes are removed.
    states: dict[str, ET.Element] = {}
    if purpose == "symbol":
        for el in root.iter():
            if _local(el.tag)[1] != "g":
                continue
            classes = (el.get("class") or "").split()
            for state in ("off", "on"):
                label = (el.get(f"{{{INKSCAPE}}}label") or "").strip().lower()
                if ((el.get("id") or "").lower() == state or label == state or f"state-{state}" in classes) \
                        and state not in states:
                    states[state] = el
        missing = [s for s in ("off", "on") if s not in states]
        if missing:
            raise SanitizeError("a symbol needs a group with the ID 'off' and one with the ID 'on' "
                                f"(or Inkscape layers named Off and On); missing: {', '.join(missing)}")

    ids: set[str] = set()

    def clean(el: ET.Element) -> None:
        for child in list(el):
            if not isinstance(child.tag, str):  # comments and processing instructions
                el.remove(child)
                continue
            cns, cname = _local(child.tag)
            if cns not in (SVG, None) or cname not in ELEMENTS:
                el.remove(child)
                if cname not in DROP_QUIETLY:
                    removed[f"<{cname}>" if cns in (SVG, None) else f"<{cname}> (editor data)"] += 1
                continue
            clean(child)
        for key in list(el.attrib):
            ans, aname = _local(key)
            value = el.attrib.pop(key)
            if ans == XLINK and aname == "href":
                aname, ans = "href", None
            if ans is not None:
                continue  # editor data (inkscape:, sodipodi:): dropped quietly
            if aname.lower().startswith("on"):
                removed["event handlers"] += 1
                continue
            if aname not in ATTRS:
                removed[f"'{aname}' attributes"] += 1
                continue
            if aname == "href" and not value.startswith("#"):
                removed["links outside the file"] += 1
                continue
            if aname == "attributeName" and (value.lower().startswith("on") or "href" in value.lower()):
                removed["animations of links or handlers"] += 1
                el.set("data-drop", "1")
                continue
            if aname == "style":
                kept = [d for d in value.split(";") if d.strip() and not BAD_CSS.search(d)]
                if len(kept) != len([d for d in value.split(";") if d.strip()]):
                    removed["unsafe style declarations"] += 1
                value = ";".join(d.strip() for d in kept)
                if not value:
                    continue
            elif BAD_CSS.search(value):
                removed["links outside the file"] += 1
                continue
            el.set(aname, value)
            if aname == "id":
                ids.add(value)
        for child in list(el):
            if child.get("data-drop"):
                el.remove(child)

    clean(root)
    if root.get("data-drop"):
        del root.attrib["data-drop"]

    for state, el in states.items():
        el.attrib.pop("id", None)
        classes = [c for c in (el.get("class") or "").split() if not c.startswith("state-")]
        el.set("class", " ".join([*classes, f"state-{state}"]))

    # Prefix IDs and the references to them, so two imports cannot clash with each other or the page.
    ids = {i for i in ids if any(e.get("id") == i for e in root.iter())}
    mapping = {i: (i if i.startswith(f"{prefix}-") else f"{prefix}-{i}") for i in ids}

    def ref(m: re.Match) -> str:
        return f"url(#{mapping.get(m.group(2), m.group(2))})"

    for el in root.iter():
        for key, value in list(el.attrib.items()):
            if key == "id" and value in mapping:
                el.set(key, mapping[value])
            elif key == "href" and value[1:] in mapping:
                el.set(key, "#" + mapping[value[1:]])
            elif key in ("begin", "end"):
                el.set(key, re.sub(r"(?<![\w-])([A-Za-z_][\w-]*)\.(?=begin|end|click)",
                                   lambda m: mapping.get(m.group(1), m.group(1)) + ".", value))
            elif "url(" in value:
                el.set(key, URL_REF.sub(ref, value))

    for key in ("width", "height", "x", "y", "viewBox"):
        root.attrib.pop(key, None)
    out = ET.Element(f"{{{SVG}}}svg", {"viewBox": view_box})
    for child in root:
        out.append(child)
    for el in out.iter():  # every element in the SVG namespace, whatever the file used
        if not el.tag.startswith("{"):
            el.tag = f"{{{SVG}}}{el.tag}"
        # Whitespace between elements is dropped; text inside <text> and <tspan> is kept.
        if el.text is not None and not el.text.strip():
            el.text = None
        if el.tail is not None and not el.tail.strip():
            el.tail = None
    svg = ET.tostring(out, encoding="unicode", short_empty_elements=True)
    return Cleaned(svg=svg, view_box=view_box,
                   removed=[f"{n} × {what}" if n > 1 else what for what, n in sorted(removed.items())])


def is_clean(svg: str, purpose: Literal["symbol", "background"], prefix: str) -> str | None:
    """None when cleaning `svg` changes nothing, else the reason it is not clean."""
    try:
        cleaned = sanitize(svg, purpose, prefix)
    except SanitizeError as exc:
        return str(exc)
    if cleaned.svg != svg:
        return "it is not a cleaned SVG; import the file again"
    return None
