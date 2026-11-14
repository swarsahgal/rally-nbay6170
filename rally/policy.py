"""Loads and validates data/club_policy.yaml, the single wired-in knowledge source (PRD section 6).

The policy is re-read whenever the file changes, so editing the YAML while the app is running takes
effect on the next evaluation or send. Every trace event records `policy_version`.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DEFAULT_POLICY_PATH = DATA_DIR / "club_policy.yaml"


class ClubCfg(BaseModel):
    club_id: str
    name: str
    timezone: str
    court_count: int
    court_price_per_hour_usd: float
    players_per_court: int = 4


class ScheduleCfg(BaseModel):
    slot_minutes: int
    slot_grid: Literal["fixed_from_opening"]
    open_hour_min_occupancy_pct: float
    off_peak_max_occupancy_pct: float


class ConsentCfg(BaseModel):
    require_explicit_opt_in: bool
    allowed_channels: list[str]
    stop_keywords: list[str]


class EligibilityCfg(BaseModel):
    match_types_allowed: list[str]
    skill_tolerance: float
    skill_mode: Literal["hard_filter", "rank_only"]
    exclude_if_pending_invite_elsewhere: bool = True


class ContactCfg(BaseModel):
    quiet_hours_start: str
    quiet_hours_end: str
    max_invites_per_player_per_7_days: int


class TimingCfg(BaseModel):
    min_lead_time_minutes: int
    reply_timeout_minutes: int
    booking_hold_minutes: int
    detection_horizon_hours: int


class MissingPlayerCfg(BaseModel):
    outreach: Literal["sequential"]
    default_approved_candidates: int
    shortlist_size: int


class EmptyCourtCfg(BaseModel):
    outreach: Literal["batch"]
    first_wave_max_candidates: int
    min_group_size: int


class ApprovalCfg(BaseModel):
    required_for_all_invites: bool
    approval_valid_minutes: int
    new_candidate_requires_fresh_approval: bool


class BookingCfg(BaseModel):
    verification: str
    required_payment_status: str
    booking_link_base: str


class RankingCfg(BaseModel):
    weights: dict[str, float]
    time_fit_when_not_preferred: float

    @field_validator("weights")
    @classmethod
    def _weights_sum_to_one(cls, value: dict[str, float]) -> dict[str, float]:
        expected = {"skill_fit", "time_fit", "social", "activity", "freshness"}
        if set(value) != expected:
            raise ValueError(f"ranking.weights must have exactly these keys: {sorted(expected)}")
        if abs(sum(value.values()) - 1.0) > 1e-6:
            raise ValueError("ranking.weights must sum to 1.0")
        return value


class EscalationCfg(BaseModel):
    intents_requiring_manager: list[str]
    on_zero_eligible: str
    on_provider_unavailable: str


class LlmCfg(BaseModel):
    default_model: str
    max_reply_chars: int = 500


class DemoCfg(BaseModel):
    start_time: str
    generate_days: int = 2
    minutes_per_player_reply: int = 2


class ClubPolicy(BaseModel):
    policy_version: str
    status: str
    club: ClubCfg
    schedule: ScheduleCfg
    consent: ConsentCfg
    eligibility: EligibilityCfg
    contact: ContactCfg
    timing: TimingCfg
    missing_player: MissingPlayerCfg
    empty_court: EmptyCourtCfg
    approval: ApprovalCfg
    booking: BookingCfg
    ranking: RankingCfg
    escalation: EscalationCfg
    llm: LlmCfg
    demo: DemoCfg
    source_path: str = ""
    sha256: str = ""

    def excerpt(self, *sections: str) -> dict:
        """Return only the named sections, for minimal LLM context (PRD section 6)."""
        dumped = self.model_dump(include=set(sections))
        dumped["policy_version"] = self.policy_version
        return dumped


def load_policy(path: str | Path | None = None) -> ClubPolicy:
    """Load the policy, re-reading it whenever the file's modification time changes."""
    policy_path = Path(path) if path else DEFAULT_POLICY_PATH
    return _load_cached(str(policy_path), policy_path.stat().st_mtime_ns)


@lru_cache(maxsize=16)
def _load_cached(path: str, _mtime_ns: int) -> ClubPolicy:
    raw = Path(path).read_bytes()
    policy = ClubPolicy.model_validate(yaml.safe_load(raw))
    return policy.model_copy(update={"source_path": path, "sha256": hashlib.sha256(raw).hexdigest()[:12]})
