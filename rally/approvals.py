"""approve_campaign(): the manager authorises one specific outreach campaign (decision D03).

The approval fixes the candidate list, invitation cap, channels, outreach window, expiry and the
exact message template. scope_violation() is checked before every single send.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .messaging import validate_invite_text
from .policy import ClubPolicy
from .schemas import CampaignApproval, Opportunity, OpportunityType, Shortlist


class ApprovalError(ValueError):
    pass


def approve_campaign(*, opp: Opportunity, shortlist: Shortlist, selected_ids: list[str], max_sends: int,
                     window_end: datetime, channels: list[str], message_template: str, message_source: str,
                     facts: dict, manager: str, policy: ClubPolicy, now: datetime, attempt: int,
                     first_wave: int | None = None) -> CampaignApproval:
    ranked = shortlist.candidates + shortlist.reserve
    order = {c.player_id: c.rank for c in ranked}
    unknown = [pid for pid in selected_ids if pid not in order]
    if unknown:
        raise ApprovalError(f"{', '.join(unknown)} not in the current shortlist; re-rank before approving.")
    if not selected_ids:
        raise ApprovalError("Select at least one candidate.")
    if len(set(selected_ids)) != len(selected_ids):
        raise ApprovalError("A candidate is listed twice.")
    if opp.type == OpportunityType.EMPTY_COURT:
        if len(selected_ids) < policy.empty_court.min_group_size:
            raise ApprovalError(f"Approve at least {policy.empty_court.min_group_size} players to fill a court.")
        if len(selected_ids) > policy.empty_court.first_wave_max_candidates:
            raise ApprovalError(f"First wave is capped at {policy.empty_court.first_wave_max_candidates} players.")
    if not 1 <= max_sends <= len(selected_ids):
        raise ApprovalError("Max sends must be between 1 and the number of approved candidates.")
    if first_wave is not None:
        if opp.type != OpportunityType.EMPTY_COURT:
            raise ApprovalError("A first wave only applies to empty-court (batch) outreach.")
        if not policy.empty_court.min_group_size <= first_wave <= max_sends:
            raise ApprovalError(f"First wave must be between {policy.empty_court.min_group_size} and the max sends "
                                f"({max_sends}).")
    bad_channels = set(channels) - set(policy.consent.allowed_channels)
    if not channels or bad_channels:
        raise ApprovalError(f"Channels must be a non-empty subset of {policy.consent.allowed_channels}.")
    latest_end = opp.start - timedelta(minutes=policy.timing.min_lead_time_minutes)
    if window_end > latest_end:
        raise ApprovalError(f"Outreach must end at least {policy.timing.min_lead_time_minutes} min before the game "
                            f"({latest_end:%H:%M}).")
    if window_end <= now:
        raise ApprovalError("Outreach window has already ended.")
    problems = validate_invite_text(message_template, facts)
    if problems:
        raise ApprovalError("Message fails checks: " + "; ".join(problems))

    ordered_ids = sorted(selected_ids, key=lambda pid: order[pid])
    expires_at = min(window_end, now + timedelta(minutes=policy.approval.approval_valid_minutes))
    return CampaignApproval(
        campaign_id=f"CMP-{opp.opportunity_id}-{attempt}", opportunity_id=opp.opportunity_id,
        opportunity_type=opp.type, strategy="sequential" if opp.type == OpportunityType.MISSING_PLAYER else "batch",
        approved_player_ids=ordered_ids, max_sends=max_sends, first_wave=first_wave, channels=list(channels),
        window_start=now,
        window_end=window_end, expires_at=expires_at, message_template=message_template,
        message_source=message_source, approved_by=manager, approved_at=now, policy_version=policy.policy_version)


def scope_violation(approval: CampaignApproval, player_id: str, now: datetime, sends_so_far: int,
                    channel: str | None) -> str | None:
    """Return why a send is outside the approved scope, or None if it is inside."""
    if player_id not in approval.approved_player_ids:
        return "player is not in the approved candidate list (fresh approval needed)"
    if sends_so_far >= approval.max_sends:
        return f"approved send limit reached ({approval.max_sends})"
    if now > approval.expires_at:
        return f"approval expired at {approval.expires_at:%H:%M}"
    if not (approval.window_start <= now <= approval.window_end):
        return f"outside the approved outreach window (ends {approval.window_end:%H:%M})"
    if channel not in approval.channels:
        return f"channel {channel!r} was not approved"
    return None
