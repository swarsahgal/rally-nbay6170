"""Pydantic contracts shared by every Rally module (PRD sections 4-6)."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class OpportunityType(str, Enum):
    MISSING_PLAYER = "MISSING_PLAYER"
    EMPTY_COURT = "EMPTY_COURT"


class State(str, Enum):
    DETECTED = "DETECTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    INVITING = "INVITING"
    AWAITING_REPLY = "AWAITING_REPLY"
    PARTIAL_COMMITMENT = "PARTIAL_COMMITMENT"
    AWAITING_BOOKING_VERIFICATION = "AWAITING_BOOKING_VERIFICATION"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    ESCALATED = "ESCALATED"
    UNVERIFIED = "UNVERIFIED"


TERMINAL_STATES = frozenset({State.CONFIRMED, State.EXPIRED, State.CANCELLED})


class InviteStatus(str, Enum):
    QUEUED = "QUEUED"                # approved, not sent yet (sequential order)
    SENT = "SENT"                    # sent, waiting for a reply
    ACCEPTED = "ACCEPTED"            # clear yes: seat held, booking link sent
    DECLINED = "DECLINED"
    TIMED_OUT = "TIMED_OUT"
    OPTED_OUT = "OPTED_OUT"
    NEEDS_MANAGER = "NEEDS_MANAGER"  # unclear reply, escalated
    WAITLISTED = "WAITLISTED"        # said yes after all seats were held
    SKIPPED = "SKIPPED"              # manager skipped after an escalation
    NOT_SENT = "NOT_SENT"            # campaign ended before this invite was needed
    CLOSED = "CLOSED"                # campaign ended while this invite was unanswered
    BOOKED = "BOOKED"                # provider verified the booking


class Court(BaseModel):
    court_id: str
    name: str
    court_type: str = ""


class Match(BaseModel):
    """A booking as the (mock) booking provider reports it."""

    match_id: str
    court_id: str
    start: datetime
    end: datetime
    kind: Literal["club_open", "private"]
    level: Optional[float] = None
    player_ids: list[str] = Field(default_factory=list)
    players_known: bool = True
    capacity: int = 4
    status: Literal["confirmed", "cancelled"] = "confirmed"
    payment_status: Literal["paid", "pending", "unknown"] = "paid"
    dropout_player_ids: list[str] = Field(default_factory=list)
    created_by: str = "seed"
    note: str = ""

    @property
    def open_seats(self) -> int:
        if not self.players_known:
            return 0
        return max(0, self.capacity - len(self.player_ids))


class RosterPlayer(BaseModel):
    player_id: str
    name: str
    first_name: str
    level: float


class Snapshot(BaseModel):
    club_id: str
    taken_at: datetime
    window_start: datetime
    window_end: datetime
    courts: list[Court]
    matches: list[Match]
    roster: list[RosterPlayer]
    source: str = "mock_provider"


class PlayerProfile(BaseModel):
    """Rally's view of a player: roster + consent + preferences + contact history."""

    player_id: str
    name: str
    first_name: str
    level: float
    phone: str
    opt_in_status: Literal["opted_in", "none", "opted_out"]
    opt_in_channel: Optional[str] = None
    opt_in_source: Optional[str] = None
    opt_in_at: Optional[str] = None
    preferred_bands: list[str] = Field(default_factory=list)
    played_with: list[str] = Field(default_factory=list)
    last_played_days_ago: Optional[int] = None
    invites_last_7d: int = 0
    pending_invite_campaign: Optional[str] = None


class Opportunity(BaseModel):
    opportunity_id: str
    type: OpportunityType
    court_id: str
    court_name: str
    start: datetime
    end: datetime
    seats_needed: int
    level_target: Optional[float] = None
    match_id: Optional[str] = None
    present_player_ids: list[str] = Field(default_factory=list)
    dropout_player_ids: list[str] = Field(default_factory=list)
    typical_occupancy_pct: Optional[float] = None
    free_courts_in_slot: Optional[int] = None
    reasons: list[str] = Field(default_factory=list)
    detected_at: datetime
    policy_version: str


class NotActionable(BaseModel):
    ref: str
    label: str
    reason_code: str
    detail: str


class Exclusion(BaseModel):
    player_id: str
    name: str
    level: float
    reason_code: str
    detail: str


class EligibilityResult(BaseModel):
    opportunity_id: str
    eligible_ids: list[str]
    exclusions: list[Exclusion]


class CandidateScore(BaseModel):
    player_id: str
    name: str
    level: float
    rank: int = 0
    total: float
    features: dict[str, float]
    explanation: str


class Shortlist(BaseModel):
    opportunity_id: str
    candidates: list[CandidateScore]
    reserve: list[CandidateScore] = Field(default_factory=list)
    group_level: Optional[float] = None
    group_note: Optional[str] = None
    escalation: Optional[str] = None


class CampaignApproval(BaseModel):
    """Exactly what the manager authorised. Anything outside it needs a fresh approval."""

    campaign_id: str
    opportunity_id: str
    opportunity_type: OpportunityType
    strategy: Literal["sequential", "batch"]
    approved_player_ids: list[str]
    max_sends: int
    channels: list[str]
    window_start: datetime
    window_end: datetime
    expires_at: datetime
    message_template: str
    message_source: str
    approved_by: str
    approved_at: datetime
    policy_version: str


class ReplyIntent(str, Enum):
    ACCEPT = "ACCEPT"
    DECLINE = "DECLINE"
    ALTERNATIVE_TIME = "ALTERNATIVE_TIME"
    TENTATIVE = "TENTATIVE"
    OPT_OUT = "OPT_OUT"
    QUESTION = "QUESTION"
    UNKNOWN = "UNKNOWN"


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ReplyInterpretation(BaseModel):
    """The only fields the LLM may return for a player reply. Python decides what happens next."""

    intent: ReplyIntent = Field(description="Exactly one intent for this reply to this invitation.")
    alternative_time: Optional[str] = Field(
        default=None, description="24-hour HH:MM if the player proposes a different time, otherwise null.")
    requires_manager: bool = Field(description="True if a human must look at this reply before anything happens.")
    safe_to_progress: bool = Field(
        description="True only for an unconditional ACCEPT, a clear DECLINE, or OPT_OUT.")
    summary: str = Field(description="One short neutral sentence describing the reply. Display only.")

    @field_validator("intent", mode="before")
    @classmethod
    def _normalise_intent(cls, value):
        # Structured outputs do not guarantee enum casing, so compare case-insensitively.
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("alternative_time", mode="before")
    @classmethod
    def _check_time(cls, value):
        if value in (None, "", "null"):
            return None
        value = str(value).strip()
        return value if _TIME_RE.match(value) else None


class DraftedInvite(BaseModel):
    message: str = Field(description="The SMS text, keeping the literal placeholder {first_name}.")


class VerificationResult(BaseModel):
    status: Literal["confirmed", "pending", "unavailable", "mismatch"]
    detail: str
    booking_id: Optional[str] = None
