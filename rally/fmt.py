"""Small, shared formatting helpers so messages, UI and traces describe times the same way."""

from __future__ import annotations

from datetime import datetime, timedelta


def time_label(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def range_label(start: datetime, end: datetime) -> str:
    if start.strftime("%p") == end.strftime("%p"):
        return f"{start.strftime('%I:%M').lstrip('0')}–{time_label(end)}"
    return f"{time_label(start)}–{time_label(end)}"


def day_label(dt: datetime) -> str:
    return f"{dt.strftime('%a %b')} {dt.day}"


def relative_day(dt: datetime, now: datetime) -> str:
    delta = (dt.date() - now.date()).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    return dt.strftime("%A")


def when_label(start: datetime, end: datetime, now: datetime) -> str:
    """e.g. 'today (Tue Nov 10) 5:30–7:00 PM'."""
    return f"{relative_day(start, now)} ({day_label(start)}) {range_label(start, end)}"


def duration_label(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{sign}{hours}h {mins}m"
    if hours:
        return f"{sign}{hours}h"
    return f"{sign}{mins} min"


def level_label(level: float | None) -> str:
    return "any" if level is None else f"{level:.1f}"
