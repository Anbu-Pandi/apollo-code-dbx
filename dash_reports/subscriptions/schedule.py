import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


def clean_schedule(value):
    if not isinstance(value, dict):
        raise ValueError("Choose a delivery schedule.")
    frequency = value.get("frequency", "weekdays")
    if frequency not in {"daily", "weekdays", "weekly", "custom"}:
        raise ValueError("Choose a delivery frequency.")
    clock = value.get("time", "08:00")
    if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("Use a local delivery time in HH:MM format.")
    zone = value.get("timezone", "America/New_York")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("Choose a valid timezone.") from None
    days = list(range(7)) if frequency == "daily" else list(range(5)) if frequency == "weekdays" else value.get("days", [0])
    if not isinstance(days, list) or not days or any(type(day) is not int or day not in range(7) for day in days):
        raise ValueError("Choose at least one delivery day.")
    days = sorted(set(days))
    if frequency == "weekly" and len(days) != 1:
        raise ValueError("Choose exactly one day for weekly delivery.")
    return {"frequency": frequency, "time": clock, "timezone": zone,
            "days": list(range(7)) if frequency == "daily" else list(range(5)) if frequency == "weekdays" else days}


def next_due(schedule, after):
    if after.tzinfo is None:
        raise ValueError("Scheduling requires an aware timestamp.")
    zone = ZoneInfo(schedule["timezone"])
    local_date = after.astimezone(zone).date()
    clock = time.fromisoformat(schedule["time"])
    for offset in range(9):
        date = local_date + timedelta(days=offset)
        if date.weekday() not in schedule["days"]:
            continue
        naive = datetime.combine(date, clock)
        for minute in range(181):
            local = (naive + timedelta(minutes=minute)).replace(tzinfo=zone, fold=0)
            candidate = local.astimezone(UTC)
            if candidate.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None):
                break
        else:
            continue
        if candidate > after:
            return candidate
    raise ValueError("Could not determine the next delivery time.")
