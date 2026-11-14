"""Club occupancy profile (from the club's hour-by-weekday spreadsheet) and schedule helpers.

The spreadsheet gives typical court occupancy (%) for each weekday (ISO 1=Mon .. 7=Sun) and hour.
Rally uses it to (1) derive opening hours, (2) lay out the 90-minute slot grid, and (3) label a slot
as off-peak when its typical occupancy is below the policy threshold.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

WEEKDAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}

BAND_LABELS = {
    "weekday_early": "weekday mornings (before 10am)",
    "weekday_midday": "weekday midday (10am–2pm)",
    "weekday_afternoon": "weekday afternoons (2–5pm)",
    "weekday_evening": "weekday evenings (5–9pm)",
    "weekday_late": "weekday late nights (after 9pm)",
    "weekend_morning": "weekend mornings",
    "weekend_afternoon": "weekend afternoons",
    "weekend_evening": "weekend evenings",
}


def time_band(start: datetime) -> str:
    """Map a slot start to the coarse availability band players declare at opt-in."""
    hour = start.hour
    if start.isoweekday() <= 5:
        if hour < 10:
            return "weekday_early"
        if hour < 14:
            return "weekday_midday"
        if hour < 17:
            return "weekday_afternoon"
        if hour < 21:
            return "weekday_evening"
        return "weekday_late"
    if hour < 12:
        return "weekend_morning"
    if hour < 17:
        return "weekend_afternoon"
    return "weekend_evening"


@dataclass(frozen=True)
class OccupancyProfile:
    """Typical occupancy percent keyed by (weekday_iso, hour)."""

    pct: dict[tuple[int, int], float]

    @classmethod
    def from_csv(cls, path: str | Path) -> "OccupancyProfile":
        values: dict[tuple[int, int], float] = {}
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                values[(int(row["weekday_iso"]), int(row["hour"]))] = float(row["occupancy_pct"])
        missing = [(d, h) for d in range(1, 8) for h in range(24) if (d, h) not in values]
        if missing:
            raise ValueError(f"occupancy profile is missing {len(missing)} weekday/hour cells, e.g. {missing[:3]}")
        return cls(values)

    def at(self, weekday_iso: int, hour: int) -> float:
        return self.pct[(weekday_iso, hour)]

    def opening_hours(self, min_pct: float) -> dict[int, tuple[int, int]]:
        """Per weekday: (first open hour, closing hour exclusive). An hour is open if occupancy >= min_pct."""
        hours: dict[int, tuple[int, int]] = {}
        for weekday in range(1, 8):
            open_hours = [h for h in range(24) if self.at(weekday, h) >= min_pct]
            if not open_hours:
                raise ValueError(f"no open hours found for weekday {weekday}")
            hours[weekday] = (min(open_hours), max(open_hours) + 1)
        return hours

    def slot_occupancy(self, start: datetime, end: datetime) -> float:
        """Minute-weighted typical occupancy over [start, end). Slots never cross midnight here."""
        total = 0.0
        minutes = 0
        cursor = start
        while cursor < end:
            next_hour = (cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            segment_end = min(next_hour, end)
            seg_minutes = int((segment_end - cursor).total_seconds() // 60)
            total += seg_minutes * self.at(cursor.isoweekday(), cursor.hour)
            minutes += seg_minutes
            cursor = segment_end
        return round(total / minutes, 1) if minutes else 0.0


def slot_grid(day: date, open_hour: int, close_hour: int, slot_minutes: int, tzinfo) -> list[tuple[datetime, datetime]]:
    """Saleable slots on a fixed grid that starts at opening time (policy: slot_grid=fixed_from_opening)."""
    slots = []
    start = datetime.combine(day, time(hour=open_hour), tzinfo=tzinfo)
    close = datetime.combine(day, time(0), tzinfo=tzinfo) + timedelta(hours=close_hour)
    while start + timedelta(minutes=slot_minutes) <= close:
        slots.append((start, start + timedelta(minutes=slot_minutes)))
        start += timedelta(minutes=slot_minutes)
    return slots
