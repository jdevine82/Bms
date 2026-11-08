"""Haystack JSON (Hayson) and Zinc, both ways (milestone 12a-Haystack, main spec 5.12).

Read: request grids from clients. Write: every answer, and the model export.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time
from typing import Any

from devine_bms.haystack.kinds import (
    _NA,
    MARKER,
    NA,
    REMOVE,
    Coord,
    DateTime,
    Grid,
    Number,
    Ref,
    Uri,
    XStr,
    _Marker,
    _Remove,
    fmt_num,
)

JSON = "application/json"
ZINC = "text/zinc"


_NUM = re.compile(r"^(-?\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)(.*)$")


class DecodeError(ValueError):
    pass


def _zone(tz: str):
    from devine_bms.api.timezones import iana_zone

    return iana_zone(tz)


def to_datetime(dt: datetime, tz: str) -> DateTime:
    """An aware datetime as a Haystack DateTime in the zone `tz` (a Haystack name)."""
    return DateTime(dt.astimezone(_zone(tz)), tz)


def _iso(dt: datetime) -> str:
    s = dt.isoformat(timespec="milliseconds" if dt.microsecond else "seconds")
    return s.replace("+00:00", "Z") if dt.utcoffset() is not None and not dt.utcoffset() else s


# ---------- Hayson ----------

def to_hayson(v: Any) -> Any:
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        return v
    if isinstance(v, _Marker):
        return {"_kind": "marker"}
    if isinstance(v, _Remove):
        return {"_kind": "remove"}
    if isinstance(v, _NA):
        return {"_kind": "na"}
    if isinstance(v, (int, float)):
        v = Number(float(v))
    if isinstance(v, Number):
        if math.isnan(v.val) or math.isinf(v.val) or v.unit:
            out = {"_kind": "number", "val": fmt_num(v.val) if not math.isfinite(v.val) else v.val}
            if v.unit:
                out["unit"] = v.unit
            return out
        return int(v.val) if v.val.is_integer() and abs(v.val) < 1e15 else v.val
    if isinstance(v, Ref):
        out = {"_kind": "ref", "val": v.val}
        if v.dis:
            out["dis"] = v.dis
        return out
    if isinstance(v, Uri):
        return {"_kind": "uri", "val": v.val}
    if isinstance(v, DateTime):
        return {"_kind": "dateTime", "val": _iso(v.val), "tz": v.tz}
    if isinstance(v, datetime):
        return to_hayson(DateTime(v, "UTC"))
    if isinstance(v, date):
        return {"_kind": "date", "val": v.isoformat()}
    if isinstance(v, time):
        return {"_kind": "time", "val": v.isoformat()}
    if isinstance(v, Coord):
        return {"_kind": "coord", "lat": v.lat, "lng": v.lng}
    if isinstance(v, XStr):
        return {"_kind": "xstr", "type": v.type, "val": v.val}
    if isinstance(v, Grid):
        return grid_to_hayson(v)
    if isinstance(v, (list, tuple)):
        return [to_hayson(x) for x in v]
    if isinstance(v, dict):
        return {k: to_hayson(x) for k, x in v.items()}
    raise TypeError(f"no Haystack kind for {type(v).__name__}")


def grid_to_hayson(g: Grid) -> dict:
    cols = []
    for name, meta in g.cols:
        col: dict[str, Any] = {"name": name}
        if meta:
            col["meta"] = {k: to_hayson(x) for k, x in meta.items()}
        cols.append(col)
    return {"_kind": "grid", "meta": {"ver": "3.0", **{k: to_hayson(x) for k, x in g.meta.items()}}, "cols": cols,
            "rows": [{k: to_hayson(x) for k, x in r.items() if x is not None} for r in g.rows]}


def from_hayson(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, (int, float)):
        return Number(float(v))
    if isinstance(v, list):
        return [from_hayson(x) for x in v]
    if not isinstance(v, dict):
        raise DecodeError(f"not Haystack JSON: {v!r}")
    kind = v.get("_kind")
    try:
        if kind is None or kind == "dict":
            return {k: from_hayson(x) for k, x in v.items() if k != "_kind"}
        if kind == "marker":
            return MARKER
        if kind == "remove":
            return REMOVE
        if kind == "na":
            return NA
        if kind == "number":
            val = v["val"]
            num = {"INF": math.inf, "-INF": -math.inf, "NaN": math.nan}.get(val, val) if isinstance(val, str) else val
            return Number(float(num), v.get("unit"))
        if kind == "ref":
            return Ref(str(v["val"]).lstrip("@"), v.get("dis"))
        if kind == "uri":
            return Uri(v["val"])
        if kind == "symbol":
            return str(v["val"])
        if kind == "date":
            return date.fromisoformat(v["val"])
        if kind == "time":
            return time.fromisoformat(v["val"])
        if kind == "dateTime":
            tz = v.get("tz") or "UTC"
            return to_datetime(datetime.fromisoformat(v["val"]), tz)
        if kind == "coord":
            return Coord(float(v["lat"]), float(v["lng"]))
        if kind == "xstr":
            return XStr(v["type"], v["val"])
        if kind == "grid":
            return grid_from_hayson(v)
    except (KeyError, ValueError, TypeError, LookupError) as exc:
        raise DecodeError(f"bad Haystack JSON {kind}: {exc}") from exc
    raise DecodeError(f"unknown Haystack JSON kind {kind!r}")


def grid_from_hayson(v: dict) -> Grid:
    meta = {k: from_hayson(x) for k, x in (v.get("meta") or {}).items() if k != "ver"}
    cols = [(c["name"], {k: from_hayson(x) for k, x in (c.get("meta") or {}).items()}) for c in v.get("cols") or []]
    rows = [{k: from_hayson(x) for k, x in r.items()} for r in v.get("rows") or []]
    if not cols and rows:
        cols = Grid.of(rows).cols
    return Grid(meta, cols, rows)


# ---------- Zinc: writing ----------

_ESC = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "$": "\\$", "\b": "\\b", "\f": "\\f"}


def _zinc_str(s: str) -> str:
    out = []
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def to_zinc(v: Any, in_grid: bool = True) -> str:
    if v is None:
        return "" if in_grid else "N"
    if isinstance(v, bool):
        return "T" if v else "F"
    if isinstance(v, _Marker):
        return "M"
    if isinstance(v, _Remove):
        return "R"
    if isinstance(v, _NA):
        return "NA"
    if isinstance(v, str):
        return _zinc_str(v)
    if isinstance(v, (int, float)):
        v = Number(float(v))
    if isinstance(v, Number):
        return fmt_num(v.val) + (v.unit or "")
    if isinstance(v, Ref):
        return "@" + v.val + (" " + _zinc_str(v.dis) if v.dis else "")
    if isinstance(v, Uri):
        return "`" + v.val.replace("`", "\\`") + "`"
    if isinstance(v, DateTime):
        return f"{_iso(v.val)} {v.tz}"
    if isinstance(v, datetime):
        return to_zinc(DateTime(v, "UTC"))
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return v.isoformat()
    if isinstance(v, Coord):
        return f"C({fmt_num(v.lat)},{fmt_num(v.lng)})"
    if isinstance(v, XStr):
        return f"{v.type}({_zinc_str(v.val)})"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(to_zinc(x, False) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + " ".join(_tag_zinc(k, x) for k, x in v.items()) + "}"
    if isinstance(v, Grid):
        return "<<\n" + grid_to_zinc(v) + ">>"
    raise TypeError(f"no Haystack kind for {type(v).__name__}")


def _tag_zinc(name: str, v: Any) -> str:
    return name if isinstance(v, _Marker) else f"{name}:{to_zinc(v, False)}"


def grid_to_zinc(g: Grid) -> str:
    head = 'ver:"3.0"' + "".join(" " + _tag_zinc(k, x) for k, x in g.meta.items() if x is not None)
    cols = ",".join(n + "".join(" " + _tag_zinc(k, x) for k, x in m.items()) for n, m in g.cols)
    lines = [head, cols]
    for r in g.rows:
        lines.append(",".join(to_zinc(r.get(n)) for n, _ in g.cols))
    return "\n".join(lines) + "\n"


# ---------- Zinc: reading ----------

class _Zinc:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < len(self.s) else ""

    def fail(self, what: str):
        raise DecodeError(f"Zinc: {what} at {self.i}: {self.s[self.i:self.i + 20]!r}")

    def spaces(self) -> None:
        while self.peek() in (" ", "\t"):
            self.i += 1

    def newline(self) -> None:
        self.spaces()
        if self.peek() == "\r":
            self.i += 1
        if self.peek() == "\n":
            self.i += 1

    def ident(self) -> str:
        j = self.i
        while self.peek() and (self.peek().isalnum() or self.peek() == "_"):
            self.i += 1
        if j == self.i:
            self.fail("expected a name")
        return self.s[j:self.i]

    def grid(self) -> Grid:
        self.spaces()
        if not self.s.startswith("ver:", self.i):
            self.fail("expected ver:")
        self.i += 4
        ver = self.value()
        if ver not in ("3.0", "2.0"):
            self.fail(f"unsupported Zinc version {ver!r}")
        meta = self.tags()
        self.newline()
        cols = []
        while True:
            self.spaces()
            name = self.ident()
            cols.append((name, self.tags()))
            self.spaces()
            if self.peek() == ",":
                self.i += 1
                continue
            break
        self.newline()
        rows = []
        while self.i < len(self.s) and not self.s.startswith(">>", self.i):
            if self.peek() in ("\n", "\r"):
                self.newline()
                continue
            row = {}
            for n, (name, _) in enumerate(cols):
                self.spaces()
                if self.peek() in (",", "\n", "\r", "") or self.s.startswith(">>", self.i):
                    val = None
                else:
                    val = self.value()
                if val is not None:
                    row[name] = val
                self.spaces()
                if n < len(cols) - 1:
                    if self.peek() != ",":
                        self.fail("expected ,")
                    self.i += 1
            rows.append(row)
            self.newline()
        return Grid(meta, cols, rows)

    def tags(self, end: str = "\n") -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            self.spaces()
            ch = self.peek()
            if ch in ("", ",", "\n", "\r", "}") or ch == end:
                return out
            name = self.ident()
            self.spaces()
            if self.peek() == ":":
                self.i += 1
                self.spaces()
                out[name] = self.value()
            else:
                out[name] = MARKER

    def value(self) -> Any:
        self.spaces()
        ch = self.peek()
        if ch == '"':
            return self.string()
        if ch == "@":
            self.i += 1
            j = self.i
            while self.peek() and (self.peek().isalnum() or self.peek() in "_:-.~"):
                self.i += 1
            ref = self.s[j:self.i]
            self.spaces()
            dis = self.string() if self.peek() == '"' else None
            return Ref(ref, dis)
        if ch == "`":
            self.i += 1
            j = self.i
            while self.peek() and self.peek() != "`":
                self.i += 2 if self.peek() == "\\" else 1
            val = self.s[j:self.i].replace("\\`", "`")
            self.i += 1
            return Uri(val)
        if ch == "[":
            self.i += 1
            items = []
            while True:
                self.spaces()
                if self.peek() == "]":
                    self.i += 1
                    return items
                items.append(self.value())
                self.spaces()
                if self.peek() == ",":
                    self.i += 1
        if ch == "{":
            self.i += 1
            d = self.tags(end="}")
            self.spaces()
            if self.peek() != "}":
                self.fail("expected }")
            self.i += 1
            return d
        if self.s.startswith("<<", self.i):
            self.i += 2
            self.newline()
            g = self.grid()
            if not self.s.startswith(">>", self.i):
                self.fail("expected >>")
            self.i += 2
            return g
        if ch == "-" and self.s.startswith("-INF", self.i):
            self.i += 4
            return Number(-math.inf)
        if ch.isdigit() or (ch == "-" and self.peek(1).isdigit()):
            return self.number_or_date()
        word = self.ident()
        if word == "N":
            return None
        if word == "M":
            return MARKER
        if word == "R":
            return REMOVE
        if word == "NA":
            return NA
        if word == "T":
            return True
        if word == "F":
            return False
        if word == "INF":
            return Number(math.inf)
        if word == "NaN":
            return Number(math.nan)
        if word == "C" and self.peek() == "(":
            self.i += 1
            j = self.s.index(")", self.i)
            lat, lng = self.s[self.i:j].split(",")
            self.i = j + 1
            return Coord(float(lat), float(lng))
        if word[0].isupper() and self.peek() == "(":
            self.i += 1
            val = self.string()
            if self.peek() != ")":
                self.fail("expected )")
            self.i += 1
            return XStr(word, val)
        self.fail(f"unknown value {word!r}")

    def string(self) -> str:
        self.i += 1  # the opening quote
        out = []
        while True:
            ch = self.peek()
            if ch == "":
                self.fail("unterminated string")
            self.i += 1
            if ch == '"':
                return "".join(out)
            if ch == "\\":
                esc = self.peek()
                self.i += 1
                if esc == "u":
                    out.append(chr(int(self.s[self.i:self.i + 4], 16)))
                    self.i += 4
                else:
                    out.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}.get(esc, esc))
            else:
                out.append(ch)

    def number_or_date(self) -> Any:
        j = self.i
        while self.peek() and (self.peek().isalnum() or self.peek() in "-+:._%/$°" or ord(self.peek()) > 127):
            self.i += 1
        word = self.s[j:self.i]
        # dates, times and date-times
        if len(word) >= 10 and word[4] == "-" and word[7] == "-":
            if len(word) == 10:
                return date.fromisoformat(word)
            dt = datetime.fromisoformat(word)
            tz = "UTC"
            self.spaces()
            if self.peek().isalpha():
                tz = self._tzname()
            return to_datetime(dt, tz)
        if len(word) >= 5 and word[2] == ":":
            return time.fromisoformat(word)
        m = _NUM.match(word)
        if not m:
            self.fail(f"bad number {word!r}")
        return Number(float(m.group(1).replace("_", "")), m.group(2) or None)

    def _tzname(self) -> str:
        j = self.i
        while self.peek() and (self.peek().isalnum() or self.peek() in "_-+/"):
            self.i += 1
        return self.s[j:self.i]


def grid_from_zinc(text: str) -> Grid:
    return _Zinc(text.lstrip("﻿")).grid()


def value_from_zinc(text: str) -> Any:
    z = _Zinc(text)
    v = z.value()
    z.spaces()
    if z.i != len(z.s):
        z.fail("unexpected text after the value")
    return v


# ---------- both ----------

def encode(g: Grid, mime: str) -> str:
    return grid_to_zinc(g) if mime == ZINC else json.dumps(grid_to_hayson(g), ensure_ascii=False)


def decode(body: bytes, content_type: str | None) -> Grid:
    text = body.decode("utf-8", errors="strict").strip()
    if not text:
        return Grid()
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == ZINC or (ct not in (JSON,) and text.startswith("ver:")):
        return grid_from_zinc(text)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise DecodeError(f"the request is neither Haystack JSON nor Zinc: {exc}") from exc
    if isinstance(data, dict) and data.get("_kind") == "grid":
        return grid_from_hayson(data)
    if isinstance(data, dict):  # a bare dict: one row
        return Grid.of([from_hayson(data)])
    raise DecodeError("send a Haystack grid")


def choose(accept: str | None) -> str:
    a = (accept or "").lower()
    if ZINC in a or a.strip() in ("zinc",):
        return ZINC
    return JSON
