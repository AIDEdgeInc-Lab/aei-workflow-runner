"""Which scheduled occurrences fall in a time window. Pure functions, no I/O, no clock.

The schedule vocabulary is deliberately small (see workflow.validate_schedule): every N minutes, daily at HH:MM, or chosen
weekdays at HH:MM, in an IANA time zone. Wall-clock times are interpreted in that zone, so they follow daylight saving:

  * a time that does not exist on a spring-forward day (02:30) happens once, at the equivalent instant after the gap
  * a time that occurs twice on a fall-back day (01:30) happens once, at its first occurrence

Interval schedules count from an anchor instant (the first time the scheduler saw the workflow), not from wall-clock time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .workflow import DAYS

UTC = timezone.utc
MAX_OCCURRENCES = 1000  # one evaluation never expands more than this many; the caller is told when it was cut


def zone(name: str):
    if name == "UTC":
        return UTC
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def _hm(text: str):
    return int(text[:2]), int(text[3:5])


def occurrences(schedule: dict, tzname: str, after: datetime, upto: datetime, anchor: datetime = None) -> tuple:
    """(occurrences, truncated): aware UTC datetimes t with after < t <= upto, ascending. truncated is True when more than
    MAX_OCCURRENCES existed and only the LATEST ones are returned."""
    kind = schedule["kind"]
    if kind == "manual" or upto <= after:
        return [], False
    found = []
    if kind == "interval":
        step = timedelta(minutes=schedule["every_minutes"])
        anchor = anchor or after
        first = max(1, int((after - anchor) // step) + 1)   # smallest k with anchor + k*step > after
        last = int((upto - anchor) // step)                  # largest k with anchor + k*step <= upto
        truncated = last - first + 1 > MAX_OCCURRENCES
        if truncated:
            first = last - MAX_OCCURRENCES + 1
        return [anchor + k * step for k in range(first, last + 1)], truncated
    else:
        tz = zone(tzname)
        hour, minute = _hm(schedule["at"])
        wanted = set(DAYS.index(d) for d in schedule["days"]) if kind == "weekly" else set(range(7))
        day = (after.astimezone(tz) - timedelta(days=1)).date()
        last = (upto.astimezone(tz) + timedelta(days=1)).date()
        while day <= last:
            if day.weekday() in wanted:
                local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)  # fold=0: first occurrence
                t = local.astimezone(UTC)
                if after < t <= upto:
                    found.append(t)
            day += timedelta(days=1)
    found = sorted(set(found))
    if len(found) > MAX_OCCURRENCES:
        return found[-MAX_OCCURRENCES:], True
    return found, False


def next_occurrence(schedule: dict, tzname: str, after: datetime, anchor: datetime = None):
    """The first occurrence strictly after `after`, or None (manual schedules; nothing within a year)."""
    if schedule["kind"] == "manual":
        return None
    span = timedelta(minutes=schedule["every_minutes"]) * 2 if schedule["kind"] == "interval" else timedelta(days=9)
    for _ in range(60):  # widen the window rather than guess a horizon
        found, _ = occurrences(schedule, tzname, after, after + span, anchor)
        if found:
            return found[0]
        span *= 2
        if span > timedelta(days=400):
            break
    return None
