"""The shipped symbol library (milestone 9A, decision 025). The SVGs are in web/public/symbols;
this manifest says what each one is and how it animates."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path


@cache
def library() -> list[dict]:
    return json.loads((Path(__file__).parent / "library.json").read_text())


@cache
def symbol_ids() -> frozenset[str]:
    return frozenset(s["id"] for s in library())
