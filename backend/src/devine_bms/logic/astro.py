"""Sunrise and sunset, calculated locally (main spec 5.3.1 astro time switch; milestone 10A).

The NOAA / "Almanac for Computers" method with the standard zenith of 90.833 degrees
(refraction and the sun's radius): accurate to a minute or two, which is plenty for lights and
blinds, and needs no internet.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ZENITH = 90.833


def _utc_hours(day: date, lat: float, lng: float, rising: bool) -> float | None:
    """The UTC time of sunrise or sunset in hours, or None when the sun does not rise or set."""
    n = day.timetuple().tm_yday
    lng_hour = lng / 15
    t = n + ((6 if rising else 18) - lng_hour) / 24
    m = 0.9856 * t - 3.289  # mean anomaly
    sun = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(sun)))) % 360
    ra = (ra + (math.floor(sun / 90) * 90 - math.floor(ra / 90) * 90)) / 15
    sin_dec = 0.39782 * math.sin(math.radians(sun))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(ZENITH)) - sin_dec * math.sin(math.radians(lat))) / (cos_dec * math.cos(math.radians(lat)))
    if cos_h > 1 or cos_h < -1:
        return None
    h = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    local_t = h / 15 + ra - 0.06571 * t - 6.622
    return (local_t - lng_hour) % 24


def sun_times(day: date, lat: float, lng: float, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """Sunrise and sunset on a local date, as aware local times (None: no sunrise or sunset)."""
    out = []
    for rising in (True, False):
        hours = _utc_hours(day, lat, lng, rising)
        if hours is None:
            out.append(None)
            continue
        noon_utc = datetime.combine(day, time(12), tz).astimezone(UTC)
        base = datetime.combine(noon_utc.date(), time(0), UTC)
        t = (base + timedelta(hours=hours)).astimezone(tz)
        # The UTC date can differ from the local date: keep the event on the asked local day.
        if t.date() > day:
            t -= timedelta(days=1)
        elif t.date() < day:
            t += timedelta(days=1)
        out.append(t)
    return out[0], out[1]


def daylight(now: datetime, lat: float, lng: float, tz: ZoneInfo, sunrise_offset: int = 0,
             sunset_offset: int = 0) -> tuple[bool, datetime | None]:
    """Whether it is 'day' at `now` (between sunrise + offset and sunset + offset, local time),
    and when that next changes (within two days)."""
    local = now.astimezone(tz)
    edges: list[tuple[datetime, bool]] = []  # (time, day starts?)
    for d in range(-1, 3):
        day = local.date() + timedelta(days=d)
        rise, sets = sun_times(day, lat, lng, tz)
        if rise is not None:
            edges.append((rise + timedelta(minutes=sunrise_offset), True))
        if sets is not None:
            edges.append((sets + timedelta(minutes=sunset_offset), False))
    edges.sort()
    past = [e for e in edges if e[0] <= local]
    if past:
        is_day = past[-1][1]
    else:  # polar day or night: day if the sun is above the horizon at noon
        n = local.timetuple().tm_yday
        declination = 23.44 * math.sin(math.radians(360 * (284 + n) / 365))
        is_day = 90 - abs(lat - declination) > 90 - ZENITH
    nxt = next((t for t, starts in edges if t > local and starts != is_day), None)
    return is_day, nxt
