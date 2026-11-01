"""Haystack tag libraries: the Haystack 4.0.0 standard tags plus the house library.

Main spec 5.2 and Appendix A. Tags not in either library are allowed and are
treated as site custom tags.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources


class TagSource(StrEnum):
    HAYSTACK = "haystack"
    HOUSE = "house"
    CUSTOM = "custom"


@dataclass(frozen=True)
class TagLibrary:
    haystack_version: str
    haystack_tags: frozenset[str]
    house_tags: frozenset[str]

    def source_of(self, tag: str) -> TagSource:
        if tag in self.haystack_tags:
            return TagSource.HAYSTACK
        if tag in self.house_tags:
            return TagSource.HOUSE
        return TagSource.CUSTOM

    def custom_tags(self, tags: dict) -> list[str]:
        """Tags in `tags` that are in neither library, sorted."""
        return sorted(t for t in tags if self.source_of(t) == TagSource.CUSTOM)


@lru_cache(maxsize=1)
def load_library() -> TagLibrary:
    data = resources.files("devine_bms.haystack") / "data"
    lines = (data / "ph-4.0.0-tags.txt").read_text(encoding="utf-8").splitlines()
    haystack = frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))
    house = json.loads((data / "devine-house-tags.json").read_text(encoding="utf-8"))
    return TagLibrary(
        haystack_version="4.0.0",
        haystack_tags=haystack,
        house_tags=frozenset(house["tags"]),
    )
