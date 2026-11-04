"""Haystack time zone names to IANA zones (Haystack uses the city part, for example `Sydney`)."""

from __future__ import annotations

import zoneinfo
from functools import lru_cache

PREFERRED_REGIONS = ("Australia", "Pacific", "Asia", "Europe", "America", "Africa", "Atlantic", "Indian")


@lru_cache(maxsize=64)
def iana_zone(haystack_tz: str) -> zoneinfo.ZoneInfo:
    """`Sydney` -> Australia/Sydney. Full IANA names and `UTC` are accepted as they are."""
    if "/" in haystack_tz or haystack_tz == "UTC":
        return zoneinfo.ZoneInfo(haystack_tz)
    matches = sorted(z for z in zoneinfo.available_timezones() if z.rsplit("/", 1)[-1] == haystack_tz)
    if not matches:
        raise LookupError(f"unknown Haystack time zone '{haystack_tz}'")
    matches.sort(key=lambda z: PREFERRED_REGIONS.index(z.split("/")[0]) if z.split("/")[0] in PREFERRED_REGIONS else 99)
    return zoneinfo.ZoneInfo(matches[0])
