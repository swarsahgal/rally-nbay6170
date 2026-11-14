"""Build Rally's simulated club data from the club occupancy spreadsheet.

Input:  data/raw/occupancy_untitled-2.xlsx   (hour x ISO weekday x typical occupancy %)
Output: data/occupancy_profile.csv           cleaned profile (168 rows)
        data/court_hours.csv                 opening hours derived from the profile
        data/slots.csv                       saleable 90-minute slots for the demo days
        data/matches.csv                     mock booking snapshot: hand-written demo matches plus
                                             regular bookings generated to match typical occupancy

Run from the repo root:  python scripts/build_fixtures.py
The output is deterministic, so re-running it gives the same files.
"""

from __future__ import annotations

import csv
import hashlib
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from rally.occupancy import WEEKDAY_NAMES, OccupancyProfile, slot_grid, time_band  # noqa: E402
from rally.policy import load_policy  # noqa: E402

RAW_XLSX = ROOT / "data" / "raw" / "occupancy_untitled-2.xlsx"
DATA = ROOT / "data"

# Hand-written demo matches on the demo day (Tuesday). Everything else is generated.
# kind: club_open = club-managed open match (Rally may help), private = organiser's own booking.
DEMO_MATCHES = [
    {   # 1 seat open but it starts 40 minutes after the demo clock: below the 45-minute lead time.
        "match_id": "M-OPEN-1130-C4", "court_id": "C4", "start": "11:30", "kind": "club_open", "level": 3.0,
        "player_ids": ["P016", "P035", "P037"], "dropout_player_ids": [],
        "note": "Open match, one seat never filled",
    },
    {   # Demo sequence 1: missing fourth player.
        "match_id": "M-OPEN-1730-C2", "court_id": "C2", "start": "17:30", "kind": "club_open", "level": 3.5,
        "player_ids": ["P001", "P002", "P003"], "dropout_player_ids": ["P004"],
        "note": "Ethan Walsh dropped out at 10:12",
    },
    {   # Known players at 17:30, so these four have a schedule conflict with the 17:30 open match.
        "match_id": "M-PRIV-1730-C1", "court_id": "C1", "start": "17:30", "kind": "private", "level": 3.6,
        "player_ids": ["P013", "P016", "P017", "P018"], "dropout_player_ids": [],
        "note": "Private booking (players known)",
    },
    {   # Private match with an open seat: excluded by policy (no organiser consent).
        "match_id": "M-PRIV-1900-C5", "court_id": "C5", "start": "19:00", "kind": "private", "level": 4.0,
        "player_ids": ["P014", "P015", "P039"], "dropout_player_ids": [],
        "note": "Private match, organiser looking for a fourth",
    },
    {   # Second missing-player opportunity, used for the safety drills (demo sequence 3).
        "match_id": "M-OPEN-2030-C6", "court_id": "C6", "start": "20:30", "kind": "club_open", "level": 2.5,
        "player_ids": ["P020", "P021", "P022"], "dropout_player_ids": ["P023"],
        "note": "Sam Okafor dropped out at 09:41",
    },
]


def clean_profile() -> pd.DataFrame:
    raw = pd.read_excel(RAW_XLSX, dtype=str)
    if raw.shape[1] != 3:
        raise SystemExit(f"expected 3 columns, found {raw.shape[1]}: {list(raw.columns)}")
    # Column 3 is headed "Hour" in the spreadsheet but holds occupancy percentages (0-100).
    df = pd.DataFrame({
        "hour": raw.iloc[:, 0].astype(int),
        "weekday_iso": raw.iloc[:, 1].astype(int),
        "occupancy_pct": raw.iloc[:, 2].astype(float).round(2),
    })
    problems = []
    if len(df) != 168:
        problems.append(f"{len(df)} rows instead of 168")
    if df.duplicated(["weekday_iso", "hour"]).any():
        problems.append("duplicate weekday/hour rows")
    if not df["occupancy_pct"].between(0, 100).all():
        problems.append("occupancy outside 0-100")
    if not set(df["weekday_iso"]) == set(range(1, 8)) or not set(df["hour"]) == set(range(24)):
        problems.append("weekday or hour values out of range")
    if problems:
        raise SystemExit("occupancy spreadsheet failed checks: " + "; ".join(problems))
    df["weekday"] = df["weekday_iso"].map(WEEKDAY_NAMES)
    return df.sort_values(["weekday_iso", "hour"])[["weekday_iso", "weekday", "hour", "occupancy_pct"]]


def stable_order(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def main() -> None:
    policy = load_policy()
    tz = ZoneInfo(policy.club.timezone)
    courts = [f"C{i}" for i in range(1, policy.club.court_count + 1)]

    profile_df = clean_profile()
    profile_df.to_csv(DATA / "occupancy_profile.csv", index=False)
    profile = OccupancyProfile.from_csv(DATA / "occupancy_profile.csv")

    hours = profile.opening_hours(policy.schedule.open_hour_min_occupancy_pct)
    with open(DATA / "court_hours.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["weekday_iso", "weekday", "open_time", "close_time", "saleable_slots_per_court"])
        for weekday, (open_h, close_h) in hours.items():
            n_slots = len(slot_grid(date(2026, 11, 9) + timedelta(days=weekday - 1), open_h, close_h,
                                    policy.schedule.slot_minutes, tz))
            writer.writerow([weekday, WEEKDAY_NAMES[weekday], f"{open_h:02d}:00", f"{close_h:02d}:00", n_slots])

    demo_start = datetime.fromisoformat(policy.demo.start_time).replace(tzinfo=tz)
    days = [demo_start.date() + timedelta(days=i) for i in range(policy.demo.generate_days)]

    slot_rows, match_rows = [], []
    fixed_by_slot: dict[tuple[date, str], list[dict]] = {}
    for m in DEMO_MATCHES:
        fixed_by_slot.setdefault((demo_start.date(), m["start"]), []).append(m)

    for day in days:
        open_h, close_h = hours[day.isoweekday()]
        for start, end in slot_grid(day, open_h, close_h, policy.schedule.slot_minutes, tz):
            typical = profile.slot_occupancy(start, end)
            off_peak = typical < policy.schedule.off_peak_max_occupancy_pct
            hhmm = start.strftime("%H:%M")
            for court in courts:
                slot_rows.append({
                    "slot_id": f"S-{start:%Y%m%d-%H%M}-{court}", "date": day.isoformat(),
                    "weekday": WEEKDAY_NAMES[day.isoweekday()], "court_id": court,
                    "start": start.isoformat(), "end": end.isoformat(),
                    "typical_occupancy_pct": typical, "off_peak": off_peak, "time_band": time_band(start),
                })
            # Book courts to the slot's typical occupancy (expected booked courts, rounded half up).
            target = math.floor(typical * len(courts) / 100 + 0.5)
            fixed = fixed_by_slot.get((day, hhmm), [])
            taken = {m["court_id"] for m in fixed}
            for m in fixed:
                match_rows.append({
                    "match_id": m["match_id"], "court_id": m["court_id"], "start": start.isoformat(),
                    "end": end.isoformat(), "kind": m["kind"], "level": m["level"],
                    "player_ids": ";".join(m["player_ids"]), "players_known": True, "capacity": 4,
                    "status": "confirmed", "payment_status": "paid",
                    "dropout_player_ids": ";".join(m["dropout_player_ids"]), "created_by": "seed",
                    "note": m["note"],
                })
            free = sorted((c for c in courts if c not in taken), key=lambda c: stable_order(day.isoformat(), hhmm, c))
            for court in free[: max(0, target - len(fixed))]:
                match_rows.append({
                    "match_id": f"B-{start:%Y%m%d-%H%M}-{court}", "court_id": court, "start": start.isoformat(),
                    "end": end.isoformat(), "kind": "private", "level": "", "player_ids": "",
                    "players_known": False, "capacity": 4, "status": "confirmed", "payment_status": "paid",
                    "dropout_player_ids": "", "created_by": "seed", "note": "Regular booking (players not shared)",
                })

    pd.DataFrame(slot_rows).to_csv(DATA / "slots.csv", index=False)
    pd.DataFrame(match_rows).sort_values(["start", "court_id"]).to_csv(DATA / "matches.csv", index=False)

    print(f"occupancy_profile.csv: {len(profile_df)} rows")
    print("court_hours:", {WEEKDAY_NAMES[d]: f"{o:02d}:00-{c:02d}:00" for d, (o, c) in hours.items()})
    print(f"slots.csv: {len(slot_rows)} court-slots over {len(days)} days; matches.csv: {len(match_rows)} bookings")


if __name__ == "__main__":
    main()
