"""detect_opportunities(): finds missing-seat and unsold-slot opportunities (PRD section 5).

Runs automatically on every refresh (decision D02); it never sends anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .fmt import day_label, duration_label, level_label, range_label
from .occupancy import OccupancyProfile, slot_grid
from .policy import ClubPolicy
from .schemas import NotActionable, Opportunity, OpportunityType, Snapshot

WEEKDAY_LONG = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday"}


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def detect_opportunities(snapshot: Snapshot, profile: OccupancyProfile, policy: ClubPolicy,
                         now: datetime) -> tuple[list[Opportunity], list[NotActionable]]:
    horizon_end = now + timedelta(hours=policy.timing.detection_horizon_hours)
    min_lead = timedelta(minutes=policy.timing.min_lead_time_minutes)
    courts = {c.court_id: c for c in snapshot.courts}
    opportunities: list[Opportunity] = []
    skipped: list[NotActionable] = []

    # ---- Problem A: club-managed open matches with an open seat ------------------------------
    for match in sorted(snapshot.matches, key=lambda m: (m.start, m.court_id)):
        if not (now <= match.start <= horizon_end) or match.open_seats == 0:
            continue
        court_name = courts[match.court_id].name
        label = f"{court_name} · {day_label(match.start)} {range_label(match.start, match.end)}"
        seats = match.open_seats
        if match.kind not in policy.eligibility.match_types_allowed:
            skipped.append(NotActionable(
                ref=match.match_id, label=label, reason_code="PRIVATE_MATCH_EXCLUDED",
                detail=f"Private match with {seats} open seat(s). Rally only helps with club-managed open matches "
                       "unless the organiser authorises it."))
            continue
        lead = match.start - now
        if lead < min_lead:
            skipped.append(NotActionable(
                ref=match.match_id, label=label, reason_code="LEAD_TIME_TOO_SHORT",
                detail=f"Starts in {duration_label(lead)}, under the {policy.timing.min_lead_time_minutes}-minute "
                       "minimum lead time. Front desk should handle it by hand."))
            continue
        reasons = [f"Club-managed open match (level {level_label(match.level)}) with "
                   f"{len(match.player_ids)}/{match.capacity} players"]
        if match.note:
            reasons.append(match.note)
        reasons.append(f"Starts in {duration_label(lead)} (minimum lead time {policy.timing.min_lead_time_minutes} min)")
        opportunities.append(Opportunity(
            opportunity_id=f"MP-{match.match_id}", type=OpportunityType.MISSING_PLAYER, court_id=match.court_id,
            court_name=court_name, start=match.start, end=match.end, seats_needed=seats, level_target=match.level,
            match_id=match.match_id, present_player_ids=list(match.player_ids),
            dropout_player_ids=list(match.dropout_player_ids), reasons=reasons, detected_at=now,
            policy_version=policy.policy_version))

    # ---- Problem B: empty courts in off-peak slots -------------------------------------------
    hours = profile.opening_hours(policy.schedule.open_hour_min_occupancy_pct)
    threshold = policy.schedule.off_peak_max_occupancy_pct
    day = now.date()
    while day <= horizon_end.date():
        open_h, close_h = hours[day.isoweekday()]
        for start, end in slot_grid(day, open_h, close_h, policy.schedule.slot_minutes, now.tzinfo):
            if not (now < start <= horizon_end):
                continue
            typical = profile.slot_occupancy(start, end)
            if typical >= threshold:
                continue  # peak slot: normal demand is expected, Rally stays out of the way
            busy = {m.court_id for m in snapshot.matches if _overlaps(m.start, m.end, start, end)}
            free = [cid for cid in sorted(courts) if cid not in busy]
            if not free:
                continue
            court_id = free[0]
            label = f"{courts[court_id].name} · {day_label(start)} {range_label(start, end)}"
            lead = start - now
            if lead < min_lead:
                skipped.append(NotActionable(
                    ref=f"{start:%Y%m%d-%H%M}-{court_id}", label=label, reason_code="LEAD_TIME_TOO_SHORT",
                    detail=f"Empty off-peak court, but it starts in {duration_label(lead)}: too soon to form a group."))
                continue
            reasons = [
                f"{courts[court_id].name} has no booking in the provider snapshot ({len(free)} of {len(courts)} courts free)",
                f"Typical occupancy for {WEEKDAY_LONG[start.isoweekday()]} {range_label(start, end)} is {typical:.0f}% "
                f"(club occupancy data), below the {threshold:.0f}% off-peak line",
                f"Starts in {duration_label(lead)} (minimum lead time {policy.timing.min_lead_time_minutes} min)",
            ]
            opportunities.append(Opportunity(
                opportunity_id=f"EC-{start:%Y%m%d-%H%M}-{court_id}", type=OpportunityType.EMPTY_COURT,
                court_id=court_id, court_name=courts[court_id].name, start=start, end=end,
                seats_needed=policy.club.players_per_court, typical_occupancy_pct=typical,
                free_courts_in_slot=len(free), reasons=reasons, detected_at=now,
                policy_version=policy.policy_version))
        day += timedelta(days=1)

    return opportunities, skipped
