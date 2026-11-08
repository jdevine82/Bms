"""Haystack 4 value kinds and grids (milestone 12a-Haystack, main spec 5.12).

Python values stand for Haystack kinds: None (null), bool, str, `date`, `time`, `list`, `dict`;
and these classes for the rest. `Number` keeps its unit; `DateTime` keeps its Haystack time zone
name (for example `Sydney`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class _Singleton:
    _name = "?"

    def __repr__(self) -> str:
        return self._name

    def __reduce__(self):
        return self._name


class _Marker(_Singleton):
    _name = "MARKER"


class _Remove(_Singleton):
    _name = "REMOVE"


class _NA(_Singleton):
    _name = "NA"


MARKER = _Marker()
REMOVE = _Remove()
NA = _NA()


@dataclass(frozen=True)
class Number:
    val: float
    unit: str | None = None

    def __str__(self) -> str:
        return f"{fmt_num(self.val)}{self.unit or ''}"


@dataclass(frozen=True)
class Ref:
    val: str  # without the @
    dis: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Uri:
    val: str


@dataclass(frozen=True)
class Coord:
    lat: float
    lng: float


@dataclass(frozen=True)
class DateTime:
    """An aware date-time and the Haystack name of its time zone."""

    val: datetime
    tz: str = "UTC"


@dataclass(frozen=True)
class XStr:
    type: str
    val: str


@dataclass
class Grid:
    meta: dict[str, Any] = field(default_factory=dict)
    cols: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def of(cls, rows: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> Grid:
        """A grid whose columns are every tag used in `rows`, in first-seen order."""
        names: dict[str, None] = {}
        for r in rows:
            for k in r:
                names.setdefault(k, None)
        return cls(dict(meta or {}), [(n, {}) for n in names] or [("empty", {})], rows)

    @classmethod
    def error(cls, dis: str, trace: str = "") -> Grid:
        meta = {"err": MARKER, "dis": dis}
        if trace:
            meta["errTrace"] = trace
        return cls(meta, [("empty", {})], [])

    def col_names(self) -> list[str]:
        return [n for n, _ in self.cols]


def fmt_num(v: float) -> str:
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "INF" if v > 0 else "-INF"
    if float(v).is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(float(v))
