"""filter_eligible(): hard rules that decide who may be contacted at all (PRD sections 4-5).

Every excluded player gets a reason code so the manager can see why. Zero eligible -> escalate.
"""

from __future__ import annotations

from .fmt import day_label, range_label
from .policy import ClubPolicy
from .schemas import EligibilityResult, Exclusion, Opportunity, OpportunityType, PlayerProfile, Snapshot

REASON_LABELS = {
    "IN_MATCH": "Already in this match",
    "DROPPED_OUT": "Dropped out of this match",
    "NO_OPT_IN": "No opt-in on file",
    "OPTED_OUT": "Opted out",
    "CHANNEL_NOT_ALLOWED": "Channel not allowed",
    "CONTACT_CAP": "Contact cap reached",
    "PENDING_INVITE": "Unanswered invite elsewhere",
    "SCHEDULE_CONFLICT": "Already booked then",
    "SKILL_OUT_OF_RANGE": "Level outside range",
}


def within_tolerance(level: float, target: float, tolerance: float) -> bool:
    return abs(round(level - target, 2)) <= tolerance + 1e-9


def filter_eligible(opp: Opportunity, profiles: list[PlayerProfile], snapshot: Snapshot,
                    policy: ClubPolicy) -> EligibilityResult:
    cap = policy.contact.max_invites_per_player_per_7_days
    tol = policy.eligibility.skill_tolerance
    busy: dict[str, str] = {}
    courts = {c.court_id: c.name for c in snapshot.courts}
    for m in snapshot.matches:
        if m.players_known and m.start < opp.end and opp.start < m.end and m.match_id != opp.match_id:
            for pid in m.player_ids:
                busy[pid] = f"{courts.get(m.court_id, m.court_id)} {day_label(m.start)} {range_label(m.start, m.end)}"

    eligible, exclusions = [], []
    for p in profiles:
        reason = None
        if p.player_id in opp.present_player_ids:
            reason = ("IN_MATCH", "Already playing in this match")
        elif p.player_id in opp.dropout_player_ids:
            reason = ("DROPPED_OUT", "Dropped out of this match earlier")
        elif p.opt_in_status == "none" and policy.consent.require_explicit_opt_in:
            reason = ("NO_OPT_IN", "No explicit opt-in on file, so Rally cannot contact them")
        elif p.opt_in_status == "opted_out":
            reason = ("OPTED_OUT", "Opted out of Rally messages (STOP)")
        elif p.opt_in_channel not in policy.consent.allowed_channels:
            reason = ("CHANNEL_NOT_ALLOWED", f"Opt-in channel {p.opt_in_channel!r} is not allowed by policy")
        elif p.invites_last_7d >= cap:
            reason = ("CONTACT_CAP", f"Already invited {p.invites_last_7d}x in the last 7 days (cap {cap})")
        elif policy.eligibility.exclude_if_pending_invite_elsewhere and p.pending_invite_campaign:
            reason = ("PENDING_INVITE", f"Has an unanswered invite in {p.pending_invite_campaign}")
        elif p.player_id in busy:
            reason = ("SCHEDULE_CONFLICT", f"Already booked: {busy[p.player_id]}")
        elif (opp.type == OpportunityType.MISSING_PLAYER and opp.level_target is not None
              and policy.eligibility.skill_mode == "hard_filter"
              and not within_tolerance(p.level, opp.level_target, tol)):
            reason = ("SKILL_OUT_OF_RANGE",
                      f"Level {p.level:.1f} is outside {opp.level_target - tol:.1f}-{opp.level_target + tol:.1f}")
        if reason:
            exclusions.append(Exclusion(player_id=p.player_id, name=p.name, level=p.level,
                                        reason_code=reason[0], detail=reason[1]))
        else:
            eligible.append(p.player_id)
    return EligibilityResult(opportunity_id=opp.opportunity_id, eligible_ids=eligible, exclusions=exclusions)
