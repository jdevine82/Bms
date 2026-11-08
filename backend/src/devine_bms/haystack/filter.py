"""Haystack 4 filters (milestone 12a-Haystack): parsing and checking an entity against one.

    filter  := or ;  or := and ("or" and)* ;  and := term ("and" term)*
    term    := "(" filter ")" | "not" path | path cmpOp value | path
    path    := name ("->" name)*      (each "->" follows a Ref to the entity it names)
    cmpOp   := == != < <= > >=
    value   := true | false | @ref | "str" | `uri` | number[unit] | date | time

Comparisons only hold between values of the same kind (numbers with the same unit, or a filter
number with no unit); otherwise the term is false, as in the Haystack spec.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from typing import Any

from devine_bms.haystack.kinds import Number, Ref, Uri

Resolver = Callable[[str], dict | None]


class FilterError(ValueError):
    pass


@dataclass(frozen=True)
class Has:
    path: tuple[str, ...]


@dataclass(frozen=True)
class Missing:
    path: tuple[str, ...]


@dataclass(frozen=True)
class Cmp:
    path: tuple[str, ...]
    op: str
    val: Any


@dataclass(frozen=True)
class And:
    a: Any
    b: Any


@dataclass(frozen=True)
class Or:
    a: Any
    b: Any


_TOKEN = re.compile(r"""
    \s*(?:
      (?P<op>==|!=|<=|>=|<|>|->|\(|\))
    | (?P<str>"(?:[^"\\]|\\.)*")
    | (?P<uri>`(?:[^`\\]|\\.)*`)
    | (?P<ref>@[A-Za-z0-9_:\-.~]+)
    | (?P<datetime_or_date>\d{4}-\d{2}-\d{2}(?:T[0-9:.+\-Z]+(?:\s+[A-Za-z][A-Za-z_\-+/0-9]*)?)?)
    | (?P<time>\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)
    | (?P<num>-?\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?(?:[A-Za-z%_/$\u0080-￿][A-Za-z0-9%_/$\u0080-￿]*)?)
    | (?P<name>[a-zA-Z_][A-Za-z0-9_]*)
    )""", re.VERBOSE)
_NUMBER = re.compile(r"^(-?\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)(.*)$")


def _tokens(text: str) -> list[tuple[str, str]]:
    out, i = [], 0
    text = text.strip()
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m or m.end() == i:
            raise FilterError(f"cannot read the filter at {text[i:i + 15]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        i = m.end()
        while i < len(text) and text[i].isspace():
            i += 1
    return out


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks = _tokens(text)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> tuple[str, str]:
        t = self.peek()
        if t is None:
            raise FilterError("the filter ends too soon")
        self.i += 1
        return t

    def word(self, w: str) -> bool:
        t = self.peek()
        if t and t[0] == "name" and t[1] == w:
            self.i += 1
            return True
        return False

    def parse(self):
        node = self.or_()
        if self.peek() is not None:
            raise FilterError(f"unexpected {self.peek()[1]!r} in the filter")
        return node

    def or_(self):
        node = self.and_()
        while self.word("or"):
            node = Or(node, self.and_())
        return node

    def and_(self):
        node = self.term()
        while self.word("and"):
            node = And(node, self.term())
        return node

    def term(self):
        t = self.peek()
        if t == ("op", "("):
            self.i += 1
            node = self.or_()
            if self.take() != ("op", ")"):
                raise FilterError("expected )")
            return node
        if self.word("not"):
            return Missing(self.path())
        path = self.path()
        t = self.peek()
        if t and t[0] == "op" and t[1] in ("==", "!=", "<", "<=", ">", ">="):
            self.i += 1
            return Cmp(path, t[1], self.value())
        return Has(path)

    def path(self) -> tuple[str, ...]:
        kind, val = self.take()
        if kind != "name" or val in ("and", "or", "not"):
            raise FilterError(f"expected a tag name, not {val!r}")
        names = [val]
        while self.peek() == ("op", "->"):
            self.i += 1
            kind, val = self.take()
            if kind != "name":
                raise FilterError(f"expected a tag name after ->, not {val!r}")
            names.append(val)
        return tuple(names)

    def value(self) -> Any:
        kind, val = self.take()
        if kind == "name":
            if val == "true":
                return True
            if val == "false":
                return False
            raise FilterError(f"{val!r} is not a value (strings need quotes)")
        if kind == "str":
            from devine_bms.haystack.codec import value_from_zinc

            return value_from_zinc(val)
        if kind == "uri":
            return Uri(val[1:-1].replace("\\`", "`"))
        if kind == "ref":
            return Ref(val[1:])
        if kind == "datetime_or_date":
            if len(val) == 10:
                return date.fromisoformat(val)
            from devine_bms.haystack.codec import value_from_zinc

            return value_from_zinc(val)
        if kind == "time":
            return time.fromisoformat(val)
        if kind == "num":
            m = _NUMBER.match(val)
            return Number(float(m.group(1).replace("_", "")), m.group(2) or None)
        raise FilterError(f"{val!r} is not a value")


def parse(text: str):
    if not text or not text.strip():
        raise FilterError("the filter is empty")
    return _Parser(text).parse()


def _get(entity: dict, path: tuple[str, ...], resolve: Resolver) -> Any:
    cur: Any = entity
    for k, name in enumerate(path):
        if not isinstance(cur, dict):
            return None
        val = cur.get(name)
        if k == len(path) - 1:
            return val
        cur = resolve(val.val) if isinstance(val, Ref) else None
    return None


def _cmp(a: Any, op: str, b: Any) -> bool:
    if a is None:  # a missing tag makes every comparison false (Haystack 4)
        return False
    if op == "!=":  # with the tag present: not equal, whatever the kinds
        return not _cmp(a, "==", b)
    if isinstance(b, Number):
        if not isinstance(a, Number) or (b.unit and a.unit != b.unit):
            return False
        a, b = a.val, b.val
    elif isinstance(b, Ref):
        if not isinstance(a, Ref):
            return False
        a, b = a.val, b.val
    elif isinstance(b, bool):
        if not isinstance(a, bool):
            return False
    elif type(a) is not type(b):
        return False
    try:
        return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    except TypeError:
        return False


def matches(node, entity: dict, resolve: Resolver) -> bool:
    if isinstance(node, Has):
        return _get(entity, node.path, resolve) is not None
    if isinstance(node, Missing):
        return _get(entity, node.path, resolve) is None
    if isinstance(node, Cmp):
        return _cmp(_get(entity, node.path, resolve), node.op, node.val)
    if isinstance(node, And):
        return matches(node.a, entity, resolve) and matches(node.b, entity, resolve)
    if isinstance(node, Or):
        return matches(node.a, entity, resolve) or matches(node.b, entity, resolve)
    raise FilterError("bad filter")
