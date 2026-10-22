"""When routines run. Schedules are stored as plain data, e.g.

    {"time": "07:00", "days": [0, 1, 2, 3, 4]}        weekdays at 7 AM (0 = Monday)
    {"time": "18:30", "days": [6]}                    Sundays at 6:30 PM
    {"every_hours": 6}                                every six hours
    {"once": "2026-10-23 09:00"}                      one time

always in the person's own time zone, and turned into the next UTC run time here.
"""
import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ALL_DAYS = list(range(7))


def zone(name: str | None) -> dt.tzinfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return dt.UTC


def _parse_time(text: str) -> dt.time:
    hours, minutes = (int(x) for x in str(text).strip().split(":")[:2])
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise ValueError(f"not a time of day: {text!r}")
    return dt.time(hours, minutes)


def validate(schedule: dict) -> dict:
    """Normalise a schedule, raising ValueError if it cannot be understood."""
    if not isinstance(schedule, dict):
        raise TypeError("a schedule must be an object")
    if schedule.get("once"):
        dt.datetime.strptime(str(schedule["once"]).strip()[:16], "%Y-%m-%d %H:%M")
        return {"once": str(schedule["once"]).strip()[:16]}
    if schedule.get("every_hours"):
        hours = int(schedule["every_hours"])
        if not 1 <= hours <= 168:
            raise ValueError("every_hours must be between 1 and 168")
        return {"every_hours": hours}
    time = _parse_time(schedule.get("time", ""))
    days = sorted({int(d) for d in (schedule.get("days") or ALL_DAYS)})
    if not days or any(d not in ALL_DAYS for d in days):
        raise ValueError("days must be numbers from 0 (Monday) to 6 (Sunday)")
    return {"time": time.strftime("%H:%M"), "days": days}


def next_run(schedule: dict, tz_name: str | None, after: dt.datetime) -> dt.datetime | None:
    """The first run strictly after `after` (aware, any zone), as an aware UTC datetime."""
    schedule = validate(schedule)
    tz = zone(tz_name)
    if "once" in schedule:
        when = dt.datetime.strptime(schedule["once"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        return when.astimezone(dt.UTC) if when > after else None
    if "every_hours" in schedule:
        return (after + dt.timedelta(hours=schedule["every_hours"])).astimezone(dt.UTC)
    local = after.astimezone(tz)
    time = _parse_time(schedule["time"])
    for offset in range(8):
        day = local.date() + dt.timedelta(days=offset)
        if day.weekday() not in schedule["days"]:
            continue
        candidate = dt.datetime.combine(day, time, tzinfo=tz)
        if candidate > local:
            return candidate.astimezone(dt.UTC)
    return None  # unreachable for a valid schedule


def describe(schedule: dict) -> str:
    """Plain words for a schedule: 'Weekdays at 7:00 AM'."""
    schedule = validate(schedule)
    if "once" in schedule:
        when = dt.datetime.strptime(schedule["once"], "%Y-%m-%d %H:%M")
        return f"Once, {when.strftime('%a %d %b')} at {_clock(when.time())}"
    if "every_hours" in schedule:
        hours = schedule["every_hours"]
        return "Every hour" if hours == 1 else f"Every {hours} hours"
    days, at = schedule["days"], _clock(_parse_time(schedule["time"]))
    if days == ALL_DAYS:
        label = "Every day"
    elif days == [0, 1, 2, 3, 4]:
        label = "Weekdays"
    elif days == [5, 6]:
        label = "Weekends"
    elif len(days) == 1:
        label = f"Every {DAY_NAMES[days[0]]}"
    else:
        label = ", ".join(DAY_NAMES[d][:3] for d in days)
    return f"{label} at {at}"


def _clock(time: dt.time) -> str:
    hour = time.hour % 12 or 12
    return f"{hour}:{time.minute:02d} {'AM' if time.hour < 12 else 'PM'}"
