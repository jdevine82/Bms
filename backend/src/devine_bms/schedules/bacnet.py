"""Copy a schedule into a BACnet Schedule object on a controller, and check it (main spec 5.4;
milestone 7 decision 6). BACnet/IP only for now.

- The weekly pattern goes to `weeklySchedule` (Monday first): the occupied value at each period's
  start and the unoccupied value at its end; `scheduleDefault` is the unoccupied value.
- BACnet exceptions replace a whole day, so the BMS writes one exception per date that a
  holiday or one-off touches (from today, up to a year ahead), with that day's full timeline
  worked out by the same rule as the BMS uses (one-off, then holiday, then week). Priority 1.
- Read-back compares the controller's weekly pattern and exceptions with what the BMS would
  write now; a difference means someone changed the controller locally.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from devine_bms.schedules.model import DAYS, CalendarIn, ControllerTarget, ScheduleIn, _hhmm, state_at

HORIZON_DAYS = 366


def _value(target: ControllerTarget, occupied: bool) -> Any:
    v = target.occupied if occupied else target.unoccupied
    return {"real": float, "boolean": bool}.get(target.value_type, int)(v)


def weekly_plan(s: ScheduleIn, target: ControllerTarget) -> list[list[tuple[str, Any]]]:
    out = []
    for day in DAYS:
        entries: list[tuple[str, Any]] = []
        for p in s.weekly.get(day, []):
            entries.append((p.start, _value(target, True)))
            if p.end != "24:00":
                entries.append((p.end, _value(target, False)))
        out.append(entries)
    return out


def day_timeline(s: ScheduleIn, calendars: dict[str, CalendarIn], day: date, target: ControllerTarget) -> list[tuple[str, Any]]:
    """The whole day as (HH:MM, value) changes, starting at 00:00."""
    marks = {time()}
    for p in s.weekly.get(DAYS[day.weekday()], []):
        marks.add(_hhmm(p.start))
        if p.end != "24:00":
            marks.add(_hhmm(p.end))
    for e in s.exceptions:
        for t in (e.start, e.end):
            if t.date() == day:
                marks.add(t.time())
    out: list[tuple[str, Any]] = []
    last = None
    for t in sorted(marks):
        occupied, _ = state_at(s, calendars, datetime.combine(day, t))
        if occupied != last:
            out.append((t.strftime("%H:%M"), _value(target, occupied)))
            last = occupied
    return out


def exception_days(s: ScheduleIn, calendars: dict[str, CalendarIn], today: date) -> list[date]:
    days: set[date] = set()
    end = today + timedelta(days=HORIZON_DAYS)
    for e in s.exceptions:
        d = e.start.date()
        while d <= e.end.date() and d <= end:
            if d >= today and not (e.end == datetime.combine(d, time())):
                days.add(d)
            d += timedelta(days=1)
    for name in s.calendars:
        for h in calendars[name].dates if name in calendars else []:
            d = h.start
            while d <= (h.end or h.start) and d <= end:
                if d >= today:
                    days.add(d)
                d += timedelta(days=1)
    return sorted(days)


def plan(s: ScheduleIn, calendars: dict[str, CalendarIn], target: ControllerTarget, today: date) -> dict:
    """What the controller should hold, in a plain form that also serves for comparing."""
    return {"weekly": weekly_plan(s, target),
            "exceptions": {d.isoformat(): day_timeline(s, calendars, d, target)
                           for d in exception_days(s, calendars, today)},
            "default": _value(target, False)}


# ---------- bacpypes3 ----------

def _atomic(target: ControllerTarget, v: Any):
    from bacpypes3.primitivedata import Boolean, Enumerated, Real, Unsigned

    return {"real": Real, "boolean": Boolean, "unsigned": Unsigned, "enumerated": Enumerated}[target.value_type](v)


def to_bacnet(p: dict, target: ControllerTarget) -> tuple[Any, Any, Any]:
    from bacpypes3.basetypes import CalendarEntry, DailySchedule, SpecialEvent, SpecialEventPeriod, TimeValue
    from bacpypes3.constructeddata import ArrayOf
    from bacpypes3.primitivedata import Date, Time

    def tvs(entries):
        return [TimeValue(time=Time(f"{t}:00"), value=_atomic(target, v)) for t, v in entries]

    weekly = ArrayOf(DailySchedule)([DailySchedule(daySchedule=tvs(day)) for day in p["weekly"]])
    exceptions = ArrayOf(SpecialEvent)([
        SpecialEvent(period=SpecialEventPeriod(calendarEntry=CalendarEntry(date=Date(d))), listOfTimeValues=tvs(entries),
                     eventPriority=1)
        for d, entries in sorted(p["exceptions"].items())])
    return weekly, exceptions, _atomic(target, p["default"])


def _plain(v: Any) -> Any:
    v = v.get_value() if hasattr(v, "get_value") else v
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return round(v, 4)
    return int(v) if isinstance(v, int) else v


def from_bacnet(weekly: Any, exceptions: Any, target: ControllerTarget) -> dict:
    def entries(tvs):
        out = []
        for tv in tvs or []:
            t = tv.time  # (hour, minute, second, hundredths)
            out.append((f"{t[0]:02d}:{t[1]:02d}", _value(target, False) if _plain(tv.value) is None
                        else {"real": float, "boolean": bool}.get(target.value_type, int)(_plain(tv.value))))
        return out

    ex: dict[str, list] = {}
    for e in exceptions or []:
        d = e.period.calendarEntry.date if e.period and e.period.calendarEntry else None
        if d is None or d[0] == 255:
            ex[f"(not a single date: {e.period})"] = entries(e.listOfTimeValues)
            continue
        ex[date(1900 + d[0], d[1], d[2]).isoformat()] = entries(e.listOfTimeValues)
    return {"weekly": [entries(day.daySchedule) for day in (weekly or [])], "exceptions": ex}


def differences(want: dict, have: dict) -> list[str]:
    """Plain-language differences between what the BMS would write and what the controller holds."""
    out = []
    for i, day in enumerate(DAYS):
        w = [list(x) for x in want["weekly"][i]] if i < len(want["weekly"]) else []
        h = [list(x) for x in have["weekly"][i]] if i < len(have["weekly"]) else []
        if w != h:
            out.append(f"{day}: the BMS has {_fmt(w)}, the controller has {_fmt(h)}")
    for d in sorted(set(want["exceptions"]) | set(have["exceptions"])):
        w, h = want["exceptions"].get(d), have["exceptions"].get(d)
        if [list(x) for x in w or []] != [list(x) for x in h or []]:
            out.append(f"{d}: the BMS has {_fmt(w) if w else 'no exception'}, "
                       f"the controller has {_fmt(h) if h else 'no exception'}")
    return out


def _fmt(entries) -> str:
    return ", ".join(f"{t}={v}" for t, v in entries) or "nothing"
