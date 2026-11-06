"""Schedules and holiday calendars, and working out Occupied or Unoccupied (milestone 7B).

Times are wall-clock times in the site's time zone. A **one-off exception** beats a **holiday**,
which beats the **weekly pattern** (milestone 7 decision 5). A holiday day is Unoccupied all day.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NAME = r"^[a-z][a-z0-9_]*$"


class _M(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def _hhmm(text: str) -> time:
    if text == "24:00":
        return time.max
    return time.fromisoformat(text)


class Period(_M):
    """Occupied from `start` to `end` on one day, for example 07:00 to 18:00 (24:00 for midnight)."""

    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(([01]\d|2[0-3]):[0-5]\d|24:00)$")

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if _hhmm(self.end) <= _hhmm(self.start):
            raise ValueError(f"a period ends after it starts ({self.start} to {self.end})")
        return self


class OneOff(_M):
    """A one-off exception: Occupied or Unoccupied from `start` to `end` (local date and time)."""

    start: datetime
    end: datetime
    occupied: bool
    dis: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _ordered(self) -> OneOff:
        if self.start.tzinfo or self.end.tzinfo:
            raise ValueError("exception times are the site's local times, without a time zone")
        if self.end <= self.start:
            raise ValueError("an exception ends after it starts")
        return self


class HolidayDate(_M):
    start: date
    end: date | None = None  # inclusive; none for one day
    dis: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _ordered(self) -> HolidayDate:
        if self.end is not None and self.end < self.start:
            raise ValueError("a holiday ends on or after its first day")
        return self

    def covers(self, day: date) -> bool:
        return self.start <= day <= (self.end or self.start)


class CalendarIn(_M):
    name: str = Field(pattern=NAME, max_length=100)
    dis: str = Field(min_length=1, max_length=200)
    dates: list[HolidayDate] = []


class ControllerTarget(_M):
    """A BACnet Schedule object the schedule is copied into (main spec 5.4), on a BACnet/IP
    device. Its present value is `occupied` or `unoccupied`, of `valueType`."""

    device: str = Field(pattern=NAME)
    instance: int = Field(ge=0, le=4194302)
    value_type: Literal["enumerated", "unsigned", "real", "boolean"] = "enumerated"
    occupied: float | int | bool = 1
    unoccupied: float | int | bool = 0


class ScheduleIn(_M):
    name: str = Field(pattern=NAME, max_length=100)
    dis: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    weekly: dict[str, list[Period]] = {}
    calendars: list[str] = []
    exceptions: list[OneOff] = []
    equips: list[str] = []
    tags: list[str] = []
    controller: ControllerTarget | None = None

    @field_validator("weekly")
    @classmethod
    def _days(cls, value: dict) -> dict:
        unknown = set(value) - set(DAYS)
        if unknown:
            raise ValueError(f"weekly days are {', '.join(DAYS)}, not {sorted(unknown)}")
        for day, periods in value.items():
            ordered = sorted(periods, key=lambda p: p.start)
            for a, b in zip(ordered, ordered[1:], strict=False):
                if _hhmm(b.start) < _hhmm(a.end):
                    raise ValueError(f"{day}: periods {a.start}-{a.end} and {b.start}-{b.end} overlap")
            value[day] = ordered
        return value


def state_at(s: ScheduleIn, calendars: dict[str, CalendarIn], when: datetime) -> tuple[bool, str]:
    """(occupied, why) at a local, naive `when`."""
    for e in s.exceptions:
        if e.start <= when < e.end:
            return e.occupied, f"exception{': ' + e.dis if e.dis else ''}"
    for name in s.calendars:
        cal = calendars.get(name)
        for h in cal.dates if cal else []:
            if h.covers(when.date()):
                return False, f"holiday{': ' + h.dis if h.dis else ''}"
    now_t = when.time()
    for p in s.weekly.get(DAYS[when.weekday()], []):
        if _hhmm(p.start) <= now_t < _hhmm(p.end) or (p.end == "24:00" and now_t >= _hhmm(p.start)):
            return True, "weekly"
    return False, "weekly"


def next_change(s: ScheduleIn, calendars: dict[str, CalendarIn], when: datetime,
                days: int = 14) -> tuple[datetime, bool] | None:
    """The next local time the state changes, and the state then; None within `days`."""
    current, _ = state_at(s, calendars, when)
    candidates: set[datetime] = set()
    for d in range(days + 1):
        day = (when + timedelta(days=d)).date()
        midnight = datetime.combine(day, time())
        candidates.add(midnight)
        for p in s.weekly.get(DAYS[day.weekday()], []):
            candidates.add(datetime.combine(day, _hhmm(p.start)))
            candidates.add(midnight + timedelta(days=1) if p.end == "24:00" else datetime.combine(day, _hhmm(p.end)))
    for e in s.exceptions:
        candidates.update((e.start, e.end))
    for t in sorted(c for c in candidates if c > when):
        state, _ = state_at(s, calendars, t)
        if state != current:
            return t, state
    return None
