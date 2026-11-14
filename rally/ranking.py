"""rank_candidates(): transparent, rule-based scoring (decision D05, no ML predictions).

Each feature is 0..1 and shown in the UI; the total is a weighted sum (weights live in the policy).
Empty courts rank a compatible GROUP, not four independent high scores: Rally picks the skill band
whose best first wave scores highest, then ranks players inside that band.
"""

from __future__ import annotations

import statistics
from itertools import combinations

from .eligibility import within_tolerance
from .occupancy import BAND_LABELS, time_band
from .policy import ClubPolicy
from .schemas import CandidateScore, Opportunity, OpportunityType, PlayerProfile, Shortlist


def _activity(days: int | None) -> float:
    if days is None:
        return 0.2
    if days <= 7:
        return 1.0
    if days <= 21:
        return 0.7
    if days <= 45:
        return 0.4
    return 0.2


def score_player(p: PlayerProfile, opp: Opportunity, policy: ClubPolicy, target_level: float,
                 peers: set[str], names: dict[str, str]) -> CandidateScore:
    tol = policy.eligibility.skill_tolerance
    delta = abs(round(p.level - target_level, 2))
    if delta <= tol + 1e-9:
        skill = 1 - 0.5 * delta / tol
    else:  # only reachable in rank_only mode
        skill = max(0.0, 0.5 - 0.5 * (delta - tol) / tol)
    band = time_band(opp.start)
    time_fit = 1.0 if band in p.preferred_bands else policy.ranking.time_fit_when_not_preferred
    known = sorted(set(p.played_with) & peers)
    social = min(1.0, len(known) / 2)
    activity = _activity(p.last_played_days_ago)
    cap = policy.contact.max_invites_per_player_per_7_days
    freshness = max(0.0, 1 - p.invites_last_7d / cap)
    features = {"skill_fit": skill, "time_fit": time_fit, "social": social, "activity": activity,
                "freshness": freshness}
    w = policy.ranking.weights
    total = round(100 * sum(w[k] * v for k, v in features.items()), 1)

    bits = [f"level {p.level:.1f}" + (" (exact)" if delta == 0 else f" (Δ{delta:.1f})")]
    bits.append(f"prefers {BAND_LABELS.get(band, band)}" if time_fit == 1.0 else "does not list this time")
    if known:
        bits.append("has played with " + ", ".join(names.get(k, k).split()[0] for k in known))
    if p.last_played_days_ago is not None:
        bits.append(f"played {p.last_played_days_ago}d ago")
    bits.append(f"{p.invites_last_7d}/{cap} invites this week")
    return CandidateScore(player_id=p.player_id, name=p.name, level=p.level, total=total,
                          features={k: round(v, 2) for k, v in features.items()}, explanation=" · ".join(bits))


def _sort(scores: list[CandidateScore], target: float) -> list[CandidateScore]:
    ordered = sorted(scores, key=lambda s: (-s.total, abs(s.level - target), s.player_id))  # stable tie break
    for i, s in enumerate(ordered, start=1):
        s.rank = i
    return ordered


def rank_candidates(opp: Opportunity, eligible: list[PlayerProfile], policy: ClubPolicy,
                    all_names: dict[str, str]) -> Shortlist:
    if not eligible:
        return Shortlist(opportunity_id=opp.opportunity_id, candidates=[],
                         escalation="No eligible players: escalate to the manager (policy: on_zero_eligible).")
    if opp.type == OpportunityType.MISSING_PLAYER:
        target = opp.level_target if opp.level_target is not None else statistics.median(p.level for p in eligible)
        peers = set(opp.present_player_ids)
        ranked = _sort([score_player(p, opp, policy, target, peers, all_names) for p in eligible], target)
        return Shortlist(opportunity_id=opp.opportunity_id, candidates=ranked[: policy.missing_player.shortlist_size],
                         reserve=ranked[policy.missing_player.shortlist_size:], group_level=target)
    return _rank_group(opp, eligible, policy, all_names)


def _rank_group(opp: Opportunity, eligible: list[PlayerProfile], policy: ClubPolicy,
                all_names: dict[str, str]) -> Shortlist:
    tol = policy.eligibility.skill_tolerance
    seats = policy.empty_court.min_group_size
    wave_size = policy.empty_court.first_wave_max_candidates
    median_level = statistics.median(p.level for p in eligible)
    best = None
    for center in sorted({round(p.level, 1) for p in eligible}):
        cohort = [p for p in eligible if within_tolerance(p.level, center, tol)]
        if len(cohort) < seats:
            continue
        ids = {p.player_id for p in cohort}
        scored = _sort([score_player(p, opp, policy, center, ids - {p.player_id}, all_names) for p in cohort], center)
        wave = scored[:wave_size]
        # The four seats matter most; extra first-wave depth is a smaller bonus.
        value = sum(s.total for s in wave[:seats]) + 0.25 * sum(s.total for s in wave[seats:])
        key = (round(value, 3), len(cohort), -abs(center - median_level))
        if best is None or key > best[0]:
            best = (key, center, cohort, scored)
    if best is None:
        return Shortlist(opportunity_id=opp.opportunity_id, candidates=[],
                         escalation=f"Fewer than {seats} mutually compatible players (level ±{tol}) are eligible.")
    _, center, cohort, scored = best
    wave = scored[:wave_size]
    by_id = {p.player_id: p for p in cohort}
    pairs = sum(1 for a, b in combinations(wave, 2)
                if b.player_id in by_id[a.player_id].played_with or a.player_id in by_id[b.player_id].played_with)
    band = time_band(opp.start)
    prefer = sum(1 for s in wave if band in by_id[s.player_id].preferred_bands)
    note = (f"Level {center - tol:.1f}–{center + tol:.1f} group · {len(cohort)} compatible players · "
            f"{prefer} of the first {len(wave)} prefer {BAND_LABELS.get(band, band)} · "
            f"{pairs} pair(s) in the first wave have played together")
    return Shortlist(opportunity_id=opp.opportunity_id, candidates=wave, reserve=scored[wave_size:],
                     group_level=center, group_note=note)
