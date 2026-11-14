"""The coordination engine: dispatch_invite(), advance_state() and verify_booking() (PRD sections 4-5).

One engine runs both workflows: sequential outreach for a missing player and a small batch for an
empty court. Python owns every state change. The LLM only labels replies, and a label can never
create a booking: CONFIRMED requires a provider verification event.
"""

from __future__ import annotations

import functools
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable

from .approvals import scope_violation
from .fmt import day_label, range_label
from .llm import LLMService
from .messaging import SimulatedMessagingAdapter, booking_link, follow_up, render
from .policy import ClubPolicy
from .providers import BookingProvider, ProviderUnavailable
from .schemas import (TERMINAL_STATES, CampaignApproval, InviteStatus, Opportunity, OpportunityType,
                      PlayerProfile, ReplyIntent, ReplyInterpretation, State, VerificationResult)
from .storage import Storage

S = State
IS = InviteStatus

ALLOWED_TRANSITIONS: dict[State, set[State]] = {
    S.APPROVED: {S.INVITING, S.ESCALATED, S.CANCELLED, S.EXPIRED},
    S.INVITING: {S.AWAITING_REPLY, S.ESCALATED, S.CANCELLED, S.EXPIRED},
    S.AWAITING_REPLY: {S.INVITING, S.PARTIAL_COMMITMENT, S.AWAITING_BOOKING_VERIFICATION, S.ESCALATED,
                       S.CANCELLED, S.EXPIRED},
    S.PARTIAL_COMMITMENT: {S.AWAITING_REPLY, S.AWAITING_BOOKING_VERIFICATION, S.ESCALATED, S.CANCELLED,
                           S.EXPIRED},
    S.AWAITING_BOOKING_VERIFICATION: {S.CONFIRMED, S.UNVERIFIED, S.ESCALATED, S.CANCELLED, S.EXPIRED},
    S.UNVERIFIED: {S.AWAITING_BOOKING_VERIFICATION, S.CONFIRMED, S.ESCALATED, S.CANCELLED, S.EXPIRED},
    S.ESCALATED: {S.INVITING, S.AWAITING_BOOKING_VERIFICATION, S.CANCELLED, S.EXPIRED},
}
OUTREACH_STATES = {S.APPROVED, S.INVITING, S.AWAITING_REPLY, S.PARTIAL_COMMITMENT}
PRE_BOOKING_STATES = OUTREACH_STATES | {S.ESCALATED}
VERIFY_STATES = {S.AWAITING_BOOKING_VERIFICATION, S.UNVERIFIED}


class InvalidTransition(RuntimeError):
    def __init__(self, campaign: dict, from_state: State, to_state: State, reason: str, now: datetime,
                 actor: str, tool: str):
        super().__init__(f"{from_state.value} -> {to_state.value} is not allowed ({reason})")
        self.campaign_id = campaign["campaign_id"]
        self.opportunity_id = campaign["opportunity_id"]
        self.from_state, self.to_state, self.reason = from_state, to_state, reason
        self.now, self.actor, self.tool = now, actor, tool
        self.logged = False


@dataclass
class DispatchResult:
    sent: bool
    player_id: str
    reason: str = ""
    level: str = ""          # "" | player | campaign | gone | duplicate
    message_id: str | None = None


@dataclass
class ReplyOutcome:
    invite_id: str
    player_id: str
    text: str
    interpretation: dict
    parser: str
    decision: str
    from_state: str
    to_state: str
    llm_call: dict | None = None


def _guarded(method):
    """Invalid transitions roll back their transaction, then get logged in a fresh one."""

    @functools.wraps(method)
    def wrapper(self: "Coordinator", *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except InvalidTransition as exc:
            if not exc.logged:
                with self.storage.connect() as conn:
                    self.storage.log_event(
                        conn, sim_ts=exc.now, actor=exc.actor, tool=exc.tool, event_type="INVALID_TRANSITION",
                        policy_version=self.policy_fn().policy_version, campaign_id=exc.campaign_id,
                        opportunity_id=exc.opportunity_id, from_state=exc.from_state.value,
                        to_state=exc.to_state.value, detail={"reason": exc.reason})
                exc.logged = True
            raise

    return wrapper


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Coordinator:
    def __init__(self, storage: Storage, provider: BookingProvider, messaging: SimulatedMessagingAdapter,
                 llm: LLMService, policy_fn: Callable[[], ClubPolicy],
                 profiles_fn: Callable[[datetime], dict[str, PlayerProfile]],
                 opportunity_fn: Callable[[str], Opportunity]):
        self.storage, self.provider, self.messaging, self.llm = storage, provider, messaging, llm
        self.policy_fn, self.profiles_fn, self.opportunity_fn = policy_fn, profiles_fn, opportunity_fn

    # ---- helpers --------------------------------------------------------------------------------
    def _load(self, campaign_id: str) -> tuple[dict, CampaignApproval, Opportunity]:
        campaign = self.storage.get_campaign(campaign_id)
        if campaign is None:
            raise ValueError(f"unknown campaign {campaign_id}")
        return campaign, CampaignApproval.model_validate(campaign["approval"]), self.opportunity_fn(
            campaign["opportunity_id"])

    def _event(self, conn: sqlite3.Connection, now: datetime, event_type: str, campaign: dict, *,
               actor: str = "code", tool: str = "advance_state", detail: dict | None = None,
               from_state: str | None = None, to_state: str | None = None) -> None:
        self.storage.log_event(conn, sim_ts=now, actor=actor, tool=tool, event_type=event_type,
                               policy_version=self.policy_fn().policy_version, detail=detail,
                               campaign_id=campaign["campaign_id"], opportunity_id=campaign["opportunity_id"],
                               from_state=from_state, to_state=to_state)

    def _transition(self, conn: sqlite3.Connection, campaign: dict, to_state: State, now: datetime, reason: str, *,
                    actor: str = "code", tool: str = "advance_state", detail: dict | None = None) -> None:
        current = State(campaign["state"])
        if current == to_state:
            return
        if to_state not in ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTransition(campaign, current, to_state, reason, now, actor, tool)
        self.storage.update_campaign(conn, campaign["campaign_id"], now, state=to_state.value, state_reason=reason)
        self._event(conn, now, "STATE_CHANGED", campaign, actor=actor, tool=tool, from_state=current.value,
                    to_state=to_state.value, detail={"reason": reason, **(detail or {})})
        campaign["state"], campaign["state_reason"] = to_state.value, reason

    def _close_invites(self, conn: sqlite3.Connection, campaign_id: str, *, booked: bool = False) -> None:
        for inv in self.storage.invites(campaign_id, conn):
            status = IS(inv["status"])
            if status == IS.QUEUED:
                self.storage.update_invite(conn, inv["invite_id"], status=IS.NOT_SENT.value)
            elif status in (IS.SENT, IS.NEEDS_MANAGER):
                self.storage.update_invite(conn, inv["invite_id"], status=IS.CLOSED.value)
            elif status == IS.ACCEPTED:
                self.storage.update_invite(conn, inv["invite_id"], status=(IS.BOOKED if booked else IS.CLOSED).value)

    def _still_open(self, opp: Opportunity) -> bool:
        """Fresh provider check before acting (stale-data guard). Raises ProviderUnavailable."""
        if opp.type == OpportunityType.MISSING_PLAYER:
            match = self.provider.get_match(opp.match_id)
            return match is not None and match.status == "confirmed" and match.open_seats > 0
        return not self.provider.bookings_at(opp.court_id, opp.start, opp.end)

    @staticmethod
    def _in_quiet_hours(policy: ClubPolicy, now: datetime) -> bool:
        start = time.fromisoformat(policy.contact.quiet_hours_start)
        end = time.fromisoformat(policy.contact.quiet_hours_end)
        return not (start <= now.time() < end)

    # ---- campaign start and sending ------------------------------------------------------------
    @_guarded
    def start_campaign(self, approval: CampaignApproval, now: datetime) -> str:
        profiles = self.profiles_fn(now)
        cid = approval.campaign_id
        with self.storage.connect() as conn:
            self.storage.insert_campaign(conn, campaign_id=cid, opportunity_id=approval.opportunity_id,
                                         opp_type=approval.opportunity_type.value, state=S.APPROVED.value,
                                         strategy=approval.strategy, approval=approval.model_dump(mode="json"), now=now)
            for seq, pid in enumerate(approval.approved_player_ids, start=1):
                self.storage.insert_invite(conn, invite_id=f"{cid}:{pid}", campaign_id=cid, player_id=pid, seq=seq,
                                           channel=profiles[pid].opt_in_channel)
            self.storage.log_event(
                conn, sim_ts=now, actor=f"manager:{approval.approved_by}", tool="approve_campaign",
                event_type="CAMPAIGN_APPROVED", policy_version=approval.policy_version, campaign_id=cid,
                opportunity_id=approval.opportunity_id, from_state=S.AWAITING_APPROVAL.value,
                to_state=S.APPROVED.value,
                detail={"approved_player_ids": approval.approved_player_ids, "max_sends": approval.max_sends,
                        "channels": approval.channels, "window_end": approval.window_end.isoformat(),
                        "expires_at": approval.expires_at.isoformat(), "strategy": approval.strategy,
                        "message_source": approval.message_source, "message_template": approval.message_template})
        self.dispatch(cid, now)
        return cid

    def dispatch_invite(self, campaign_id: str, player_id: str, now: datetime, *, actor: str = "code") -> DispatchResult:
        """Send one invitation if, and only if, every gate passes. Every outcome is logged."""
        policy = self.policy_fn()  # re-read right before a send (PRD section 6)
        campaign, approval, opp = self._load(campaign_id)
        invite_id = f"{campaign_id}:{player_id}"
        invite = self.storage.get_invite(invite_id)
        sends = sum(1 for i in self.storage.invites(campaign_id) if i["sent_at"])
        profile = self.profiles_fn(now).get(player_id)

        def blocked(reason: str, level: str) -> DispatchResult:
            with self.storage.connect() as conn:
                self._event(conn, now, "DUPLICATE_SUPPRESSED" if level == "duplicate" else "INVITE_BLOCKED", campaign,
                            actor=actor, tool="dispatch_invite",
                            detail={"player_id": player_id, "reason": reason, "level": level})
            return DispatchResult(False, player_id, reason, level)

        if invite and invite["sent_at"]:
            return blocked("invite already sent for this invite ID (idempotency check)", "duplicate")
        if State(campaign["state"]) in TERMINAL_STATES:
            return blocked(f"campaign is {campaign['state']}", "campaign")
        channel = invite["channel"] if invite else (profile.opt_in_channel if profile else None)
        violation = scope_violation(approval, player_id, now, sends, channel)
        if violation:
            player_level = "approved candidate" in violation or "channel" in violation
            return blocked(f"outside the approved scope: {violation}", "player" if player_level else "campaign")
        if self._in_quiet_hours(policy, now):
            return blocked(f"quiet hours: sends allowed {policy.contact.quiet_hours_start}-"
                           f"{policy.contact.quiet_hours_end} only", "campaign")
        if profile is None or profile.opt_in_status != "opted_in":
            return blocked("no current opt-in (consent re-checked at send time)", "player")
        if profile.opt_in_channel not in policy.consent.allowed_channels:
            return blocked(f"channel {profile.opt_in_channel} not allowed by policy", "player")
        cap = policy.contact.max_invites_per_player_per_7_days
        if profile.invites_last_7d >= cap:
            return blocked(f"contact cap reached ({profile.invites_last_7d}/{cap} in 7 days)", "player")
        try:
            if not self._still_open(opp):
                return blocked("opportunity is no longer open in the provider (stale-data guard)", "gone")
        except ProviderUnavailable:
            return blocked("provider unavailable: cannot confirm the opportunity is still open", "campaign")

        body = render(approval.message_template, profile.first_name)
        deadline = now + timedelta(minutes=policy.timing.reply_timeout_minutes)
        try:
            with self.storage.connect() as conn:
                message_id = self.messaging.send(conn, campaign_id=campaign_id, invite_id=invite_id,
                                                 player_id=player_id, channel=channel, kind="invite", body=body,
                                                 now=now)
                self.storage.update_invite(conn, invite_id, status=IS.SENT.value, sent_at=now.isoformat(),
                                           reply_deadline=deadline.isoformat())
                # If this audit write fails, the transaction rolls back and nothing counts as sent.
                self._event(conn, now, "INVITE_SENT", campaign, actor=actor, tool="dispatch_invite",
                            detail={"player_id": player_id, "name": profile.name, "message_id": message_id,
                                    "channel": channel, "reply_deadline": deadline.isoformat(),
                                    "send_number": sends + 1, "max_sends": approval.max_sends,
                                    "simulated": True})
        except sqlite3.IntegrityError:
            return blocked("duplicate send prevented by the unique invite ID", "duplicate")
        return DispatchResult(True, player_id, message_id=message_id)

    @_guarded
    def dispatch(self, campaign_id: str, now: datetime) -> list[DispatchResult]:
        campaign, approval, opp = self._load(campaign_id)
        state = State(campaign["state"])
        if state in TERMINAL_STATES:
            return []
        policy = self.policy_fn()
        invites = self.storage.invites(campaign_id)
        names = {pid: p.first_name for pid, p in self.profiles_fn(now).items()}
        results: list[DispatchResult] = []

        if approval.strategy == "sequential":
            if any(i["status"] in (IS.SENT.value, IS.ACCEPTED.value, IS.NEEDS_MANAGER.value) for i in invites):
                return []  # one candidate at a time
            if state not in (S.APPROVED, S.INVITING, S.AWAITING_REPLY, S.ESCALATED):
                return []
            with self.storage.connect() as conn:
                self._transition(conn, campaign, S.INVITING, now, "inviting the next approved candidate")
            for inv in (i for i in invites if i["status"] == IS.QUEUED.value):
                if sum(1 for i in self.storage.invites(campaign_id) if i["sent_at"]) >= approval.max_sends:
                    break
                res = self.dispatch_invite(campaign_id, inv["player_id"], now)
                results.append(res)
                if res.sent:
                    deadline = now + timedelta(minutes=policy.timing.reply_timeout_minutes)
                    with self.storage.connect() as conn:
                        self._transition(conn, campaign, S.AWAITING_REPLY, now,
                                         f"waiting for {names.get(res.player_id, res.player_id)} "
                                         f"(reply by {deadline:%H:%M})")
                    return results
                if res.level in ("player", "duplicate"):
                    with self.storage.connect() as conn:
                        self.storage.update_invite(conn, inv["invite_id"], status=IS.NOT_SENT.value)
                        self._event(conn, now, "INVITE_SKIPPED", campaign,
                                    detail={"player_id": inv["player_id"], "reason": res.reason})
                    continue
                with self.storage.connect() as conn:
                    self._transition(conn, campaign, S.ESCALATED, now, f"send blocked: {res.reason}")
                return results
            with self.storage.connect() as conn:
                self._transition(conn, campaign, S.ESCALATED, now,
                                 "approved candidates or send limit used up; a fresh approval is needed")
            return results

        # batch (empty court): one approved wave, sent together
        if state != S.APPROVED:
            return []
        with self.storage.connect() as conn:
            self._transition(conn, campaign, S.INVITING, now, "sending the approved first wave")
        sent, last_block = 0, ""
        for inv in [i for i in invites if i["status"] == IS.QUEUED.value][: approval.max_sends]:
            res = self.dispatch_invite(campaign_id, inv["player_id"], now)
            results.append(res)
            if res.sent:
                sent += 1
                continue
            last_block = res.reason
            if res.level in ("player", "duplicate"):
                with self.storage.connect() as conn:
                    self.storage.update_invite(conn, inv["invite_id"], status=IS.NOT_SENT.value)
                continue
            break
        need = policy.empty_court.min_group_size
        with self.storage.connect() as conn:
            if sent >= need:
                self._transition(conn, campaign, S.AWAITING_REPLY, now,
                                 f"{sent} invites sent; the first {need} clear yes replies get the seats")
            else:
                self._close_invites(conn, campaign_id)
                self._transition(conn, campaign, S.ESCALATED, now,
                                 f"only {sent} invite(s) could be sent, a court needs {need}"
                                 + (f" (last block: {last_block})" if last_block else ""))
        return results

    # ---- replies --------------------------------------------------------------------------------
    @_guarded
    def handle_reply(self, invite_id: str, text: str, now: datetime) -> ReplyOutcome:
        policy = self.policy_fn()
        invite = self.storage.get_invite(invite_id)
        if invite is None:
            raise ValueError(f"unknown invite {invite_id}")
        if not invite["sent_at"]:
            raise ValueError("This player was never sent an invite, so there is nothing to reply to.")
        campaign, approval, opp = self._load(invite["campaign_id"])
        profile = self.profiles_fn(now)[invite["player_id"]]
        text = (text or "").strip()[: policy.llm.max_reply_chars]
        before = campaign["state"]
        sent_body = next((m["body"] for m in self.storage.messages(campaign["campaign_id"], invite["player_id"])
                          if m["direction"] == "out" and m["kind"] == "invite"), "")
        with self.storage.connect() as conn:
            self.messaging.receive(conn, campaign_id=campaign["campaign_id"], invite_id=invite_id,
                                   player_id=invite["player_id"], channel=invite["channel"], body=text, now=now)
            self._event(conn, now, "REPLY_RECEIVED", campaign, actor=f"player:{invite['player_id']}",
                        tool="simulated_messaging", detail={"player_id": invite["player_id"], "text": text})

        llm_call = None
        if text.upper().rstrip(".! ") in {k.upper() for k in policy.consent.stop_keywords}:
            interp = ReplyInterpretation(intent=ReplyIntent.OPT_OUT, requires_manager=False, safe_to_progress=True,
                                         summary="Exact STOP keyword, handled by a fixed rule (no LLM call needed).")
            parser, actor = "rule: STOP keyword", "rules"
        else:
            context = {"club": policy.club.name, "court": opp.court_name, "date": day_label(opp.start),
                       "time": range_label(opp.start, opp.end), "invitation_text": sent_body}
            interp, llm_call = self.llm.parse_reply(text, context, policy.excerpt("escalation"),
                                                    policy.consent.stop_keywords)
            llm_call.update(sim_ts=now.isoformat(), campaign_id=campaign["campaign_id"], invite_id=invite_id)
            self.storage.log_llm_call(llm_call)
            parser, actor = self.llm.label, ("llm" if self.llm.live else "rules")
        with self.storage.connect() as conn:
            self._event(conn, now, "REPLY_PARSED", campaign, actor=actor, tool="parse_reply",
                        detail={"player_id": invite["player_id"], "parser": parser,
                                "interpretation": interp.model_dump(mode="json"),
                                "llm_ok": None if llm_call is None else bool(llm_call["ok"])})

        decision = self._apply_reply(campaign, approval, opp, invite, profile, interp, text, now, policy)
        after = self.storage.get_campaign(campaign["campaign_id"])["state"]
        return ReplyOutcome(invite_id=invite_id, player_id=invite["player_id"], text=text,
                            interpretation=interp.model_dump(mode="json"), parser=parser, decision=decision,
                            from_state=before, to_state=after, llm_call=llm_call)

    def _apply_reply(self, campaign: dict, approval: CampaignApproval, opp: Opportunity, invite: dict,
                     profile: PlayerProfile, interp: ReplyInterpretation, text: str, now: datetime,
                     policy: ClubPolicy) -> str:
        """advance_state(): Python decides what the typed reply means for this campaign."""
        status, state, intent = IS(invite["status"]), State(campaign["state"]), interp.intent
        name, cid, iid = profile.first_name, campaign["campaign_id"], invite["invite_id"]
        sequential = approval.strategy == "sequential"

        if intent == ReplyIntent.OPT_OUT:  # always honoured, whatever the campaign state
            with self.storage.connect() as conn:
                self.storage.set_consent(conn, profile.player_id, "opted_out", now, source=f"reply to {iid}")
                self.storage.update_invite(conn, iid, status=IS.OPTED_OUT.value, seat_no=None,
                                           replied_at=now.isoformat(), last_intent=intent.value)
                self.messaging.send(conn, campaign_id=cid, invite_id=iid, player_id=profile.player_id,
                                    channel=invite["channel"], kind="opt_out_ack",
                                    body=follow_up("opt_out_ack", first_name=name, policy=policy, opp=opp), now=now)
                self._event(conn, now, "CONSENT_REVOKED", campaign, tool="advance_state",
                            detail={"player_id": profile.player_id, "effect": "no further messages to this player"})
            if state in TERMINAL_STATES:
                return f"{name} opted out. Consent revoked; no further messages."
            if status == IS.ACCEPTED:
                return self._seat_released(cid, now, f"{name} opted out after accepting")
            if sequential and status in (IS.SENT, IS.NEEDS_MANAGER):
                self.dispatch(cid, now)
                return f"{name} opted out. Consent revoked, and Rally moved to the next approved candidate."
            if not sequential:
                self._check_batch(cid, now)
            return f"{name} opted out. Consent revoked; no further messages."

        if state in TERMINAL_STATES or status not in (IS.SENT, IS.NEEDS_MANAGER, IS.ACCEPTED, IS.WAITLISTED):
            with self.storage.connect() as conn:
                self.storage.update_invite(conn, iid, last_intent=intent.value)
                self._event(conn, now, "LATE_REPLY", campaign,
                            detail={"player_id": profile.player_id, "invite_status": status.value,
                                    "campaign_state": state.value, "intent": intent.value})
            return (f"Late reply (invite is {status.value}, campaign is {state.value}). Logged for the manager; "
                    "no automatic action.")

        if state == S.ESCALATED and not sequential and intent == ReplyIntent.ACCEPT:
            with self.storage.connect() as conn:
                self.storage.update_invite(conn, iid, last_intent=intent.value)
                self._event(conn, now, "REPLY_WHILE_ESCALATED", campaign,
                            detail={"player_id": profile.player_id, "intent": intent.value})
            return "Campaign is escalated, so this yes is logged for the manager; no automatic action."

        needs_manager = (intent.value in policy.escalation.intents_requiring_manager or interp.requires_manager
                         or (intent == ReplyIntent.ACCEPT and not interp.safe_to_progress))
        if intent == ReplyIntent.DECLINE:
            return self._decline(campaign, approval, invite, profile, now)
        if intent == ReplyIntent.ACCEPT and not needs_manager:
            if status == IS.ACCEPTED:
                return f"{name} already holds a seat; the duplicate yes changes nothing."
            if status == IS.WAITLISTED:
                return f"{name} is already on the waitlist."
            if opp.type == OpportunityType.MISSING_PLAYER:
                return self._accept_seat(campaign, opp, invite, profile, now, policy)
            return self._accept_group(campaign, opp, invite, profile, now, policy)
        return self._escalate(campaign, approval, invite, profile, interp, text, now)

    def _accept_seat(self, campaign: dict, opp: Opportunity, invite: dict, profile: PlayerProfile, now: datetime,
                     policy: ClubPolicy) -> str:
        cid, iid, name = campaign["campaign_id"], invite["invite_id"], profile.first_name
        try:
            match = self.provider.get_match(opp.match_id)
        except ProviderUnavailable:
            with self.storage.connect() as conn:
                self.storage.update_invite(conn, iid, status=IS.NEEDS_MANAGER.value, last_intent="ACCEPT")
                self._transition(conn, campaign, S.ESCALATED, now,
                                 f"{name} said yes, but the provider is unavailable so the seat cannot be rechecked")
            return "Provider unavailable: Rally could not recheck the seat. Escalated; no booking link sent."
        if match is None or match.open_seats == 0:
            with self.storage.connect() as conn:
                self.messaging.send(conn, campaign_id=cid, invite_id=iid, player_id=profile.player_id,
                                    channel=invite["channel"], kind="seat_gone",
                                    body=follow_up("seat_gone", first_name=name, policy=policy, opp=opp), now=now)
                self.storage.update_invite(conn, iid, status=IS.CLOSED.value, last_intent="ACCEPT")
                self._transition(conn, campaign, S.ESCALATED, now, "the seat was filled outside Rally before the yes")
            return "The seat was already filled elsewhere, so Rally apologised and escalated. No double booking."
        link = booking_link(policy, opp.opportunity_id, iid)
        hold = now + timedelta(minutes=policy.timing.booking_hold_minutes)
        with self.storage.connect() as conn:
            self.storage.update_invite(conn, iid, status=IS.ACCEPTED.value, seat_no=1, replied_at=now.isoformat(),
                                       last_intent="ACCEPT")
            self.messaging.send(conn, campaign_id=cid, invite_id=iid, player_id=profile.player_id,
                                channel=invite["channel"], kind="booking_link",
                                body=follow_up("booking_link_missing", first_name=name, policy=policy, opp=opp,
                                               link=link), now=now)
            self.storage.update_campaign(conn, cid, now, hold_deadline=hold.isoformat(), last_check=None)
            self._event(conn, now, "BOOKING_LINK_SENT", campaign,
                        detail={"player_id": profile.player_id, "link": link, "held_until": hold.isoformat()})
            self._transition(conn, campaign, S.AWAITING_BOOKING_VERIFICATION, now,
                             f"{name} accepted; waiting for the provider to show the booking (not confirmed yet)")
        return (f"Clear yes from {name}. Booking link sent. The seat is NOT confirmed until the provider shows a "
                "paid booking.")

    def _accept_group(self, campaign: dict, opp: Opportunity, invite: dict, profile: PlayerProfile, now: datetime,
                      policy: ClubPolicy) -> str:
        cid, iid, name = campaign["campaign_id"], invite["invite_id"], profile.first_name
        seats = policy.club.players_per_court
        held = [i for i in self.storage.invites(cid) if i["status"] == IS.ACCEPTED.value]
        if len(held) >= seats or State(campaign["state"]) in VERIFY_STATES:
            with self.storage.connect() as conn:
                self.storage.update_invite(conn, iid, status=IS.WAITLISTED.value, replied_at=now.isoformat(),
                                           last_intent="ACCEPT")
                self.messaging.send(conn, campaign_id=cid, invite_id=iid, player_id=profile.player_id,
                                    channel=invite["channel"], kind="waitlist",
                                    body=follow_up("waitlist", first_name=name, policy=policy, opp=opp), now=now)
                self._event(conn, now, "WAITLISTED", campaign,
                            detail={"player_id": profile.player_id, "reason": f"all {seats} seats already held"})
            return f"All {seats} seats were already held, so {name} was waitlisted. No seat is ever given twice."
        used = {i["seat_no"] for i in held}
        seat_no = min(n for n in range(1, seats + 1) if n not in used)
        with self.storage.connect() as conn:
            self.storage.update_invite(conn, iid, status=IS.ACCEPTED.value, seat_no=seat_no,
                                       replied_at=now.isoformat(), last_intent="ACCEPT")
            self._event(conn, now, "SEAT_HELD", campaign,
                        detail={"player_id": profile.player_id, "seat_no": seat_no, "held": len(held) + 1,
                                "seats": seats, "note": "provisional interest, not a booking"})
            if len(held) + 1 < seats:
                progress = f"{len(held) + 1}/{seats} firm yes so far (provisional, not a booking)"
                if campaign["state"] == S.PARTIAL_COMMITMENT.value:
                    self.storage.update_campaign(conn, cid, now, state_reason=progress)
                else:
                    self._transition(conn, campaign, S.PARTIAL_COMMITMENT, now, progress)
        if len(held) + 1 < seats:
            return f"Seat {seat_no} of {seats} held for {name} (provisional). Waiting for more replies."
        return self._group_complete(cid, opp, now, policy)

    def _group_complete(self, cid: str, opp: Opportunity, now: datetime, policy: ClubPolicy) -> str:
        campaign = self.storage.get_campaign(cid)
        held = [i for i in self.storage.invites(cid) if i["status"] == IS.ACCEPTED.value]
        profiles = self.profiles_fn(now)
        try:
            bookings = self.provider.bookings_at(opp.court_id, opp.start, opp.end)
        except ProviderUnavailable:
            with self.storage.connect() as conn:
                self._transition(conn, campaign, S.ESCALATED, now,
                                 "4 players said yes, but the provider is unavailable so the court cannot be rechecked")
            return "Provider unavailable at the recheck. Escalated; no booking links sent."
        if bookings:
            with self.storage.connect() as conn:
                for h in held:
                    p = profiles[h["player_id"]]
                    self.messaging.send(conn, campaign_id=cid, invite_id=h["invite_id"], player_id=p.player_id,
                                        channel=h["channel"], kind="court_gone",
                                        body=follow_up("court_gone", first_name=p.first_name, policy=policy, opp=opp),
                                        now=now)
                self._event(conn, now, "COURT_RECHECK_FAILED", campaign, tool="get_snapshot",
                            detail={"bookings": [b.match_id for b in bookings]})
                self._transition(conn, campaign, S.ESCALATED, now,
                                 "the court is no longer free (booked outside Rally); outreach stopped")
            return "Court recheck failed: someone else booked it. Players told; escalated to the manager."
        hold = now + timedelta(minutes=policy.timing.booking_hold_minutes)
        with self.storage.connect() as conn:
            self._event(conn, now, "COURT_RECHECKED", campaign, tool="get_snapshot", detail={"result": "still free"})
            for h in held:
                p = profiles[h["player_id"]]
                link = booking_link(policy, opp.opportunity_id, h["invite_id"])
                self.messaging.send(conn, campaign_id=cid, invite_id=h["invite_id"], player_id=p.player_id,
                                    channel=h["channel"], kind="booking_link",
                                    body=follow_up("booking_link_group", first_name=p.first_name, policy=policy,
                                                   opp=opp, link=link), now=now)
            self.storage.update_campaign(conn, cid, now, hold_deadline=hold.isoformat(), last_check=None)
            self._event(conn, now, "BOOKING_LINKS_SENT", campaign,
                        detail={"players": [h["player_id"] for h in held], "held_until": hold.isoformat()})
            self._transition(conn, campaign, S.AWAITING_BOOKING_VERIFICATION, now,
                             "4 firm yes and the court is still free; handed to the booking link. Not a sale until "
                             "the provider shows a paid booking")
        return ("4 of 4 seats held and the court rechecked. Booking links sent. This is NOT a sale until the "
                "provider verifies a paid booking.")

    def _decline(self, campaign: dict, approval: CampaignApproval, invite: dict, profile: PlayerProfile,
                 now: datetime) -> str:
        cid, iid, name = campaign["campaign_id"], invite["invite_id"], profile.first_name
        previous = IS(invite["status"])
        with self.storage.connect() as conn:
            self.storage.update_invite(conn, iid, status=IS.DECLINED.value, seat_no=None, replied_at=now.isoformat(),
                                       last_intent="DECLINE")
            self._event(conn, now, "REPLY_DECLINED", campaign, detail={"player_id": profile.player_id})
        if previous == IS.ACCEPTED:
            return self._seat_released(cid, now, f"{name} withdrew after accepting")
        if approval.strategy == "sequential":
            self.dispatch(cid, now)
            after = self.storage.get_campaign(cid)
            if after["state"] == S.ESCALATED.value:
                return f"{name} declined. No approved candidates left: {after['state_reason']}."
            return f"{name} declined. Rally invited the next approved candidate."
        self._check_batch(cid, now)
        return f"{name} declined."

    def _seat_released(self, cid: str, now: datetime, why: str) -> str:
        campaign = self.storage.get_campaign(cid)
        state = State(campaign["state"])
        held = [i for i in self.storage.invites(cid) if i["status"] == IS.ACCEPTED.value]
        with self.storage.connect() as conn:
            if state in VERIFY_STATES or campaign["strategy"] == "sequential":
                self._transition(conn, campaign, S.ESCALATED, now, f"{why}; the booking is no longer complete")
                return f"{why}. Escalated to the manager; nothing is confirmed."
            if state == S.PARTIAL_COMMITMENT and not held:
                self._transition(conn, campaign, S.AWAITING_REPLY, now, "no seats held any more")
        self._check_batch(cid, now)
        return f"{why}. Seat released for the next yes."

    def _escalate(self, campaign: dict, approval: CampaignApproval, invite: dict, profile: PlayerProfile,
                  interp: ReplyInterpretation, text: str, now: datetime) -> str:
        iid, name = invite["invite_id"], profile.first_name
        status, state = IS(invite["status"]), State(campaign["state"])
        quote = text if len(text) <= 60 else text[:57] + "..."
        with self.storage.connect() as conn:
            if status != IS.ACCEPTED:
                self.storage.update_invite(conn, iid, status=IS.NEEDS_MANAGER.value, replied_at=now.isoformat(),
                                           last_intent=interp.intent.value)
            self._event(conn, now, "ESCALATION_RAISED", campaign,
                        detail={"player_id": profile.player_id, "intent": interp.intent.value,
                                "alternative_time": interp.alternative_time, "summary": interp.summary,
                                "text": text})
            if approval.strategy == "sequential" and status == IS.SENT and state == S.AWAITING_REPLY:
                self._transition(conn, campaign, S.ESCALATED, now,
                                 f"{name}'s reply needs a manager ({interp.intent.value}): \"{quote}\"")
        if status == IS.ACCEPTED:
            return (f"{interp.intent.value} from {name}, who already holds a seat: flagged for the manager. "
                    "Nothing changes automatically; the booking still needs provider verification.")
        return (f"{interp.intent.value}: needs a manager. No booking link sent and nothing confirmed"
                + ("; outreach paused." if approval.strategy == "sequential" else "; other invites continue."))

    def _check_batch(self, cid: str, now: datetime) -> None:
        campaign = self.storage.get_campaign(cid)
        if campaign["strategy"] != "batch" or State(campaign["state"]) not in (S.AWAITING_REPLY, S.PARTIAL_COMMITMENT):
            return
        invites = self.storage.invites(cid)
        seats = self.policy_fn().club.players_per_court
        held = sum(1 for i in invites if i["status"] == IS.ACCEPTED.value)
        if held < seats and not any(i["status"] == IS.SENT.value for i in invites):
            with self.storage.connect() as conn:
                self._transition(conn, campaign, S.ESCALATED, now,
                                 f"too few players confirmed ({held}/{seats}) and no replies pending; no booking")

    # ---- time-based checks ----------------------------------------------------------------------
    @_guarded
    def tick(self, now: datetime) -> None:
        for campaign in self.storage.campaigns():
            if State(campaign["state"]) not in TERMINAL_STATES:
                self._tick_campaign(campaign["campaign_id"], now)

    def _tick_campaign(self, cid: str, now: datetime) -> None:
        campaign, approval, opp = self._load(cid)
        policy = self.policy_fn()
        if now >= opp.start:
            with self.storage.connect() as conn:
                self._close_invites(conn, cid)
                self._transition(conn, campaign, S.EXPIRED, now, "game start time reached without a verified booking")
            return
        invites = self.storage.invites(cid)
        timed_out = [i for i in invites if i["status"] == IS.SENT.value and _dt(i["reply_deadline"]) <= now]
        if timed_out:
            with self.storage.connect() as conn:
                for inv in timed_out:
                    self.storage.update_invite(conn, inv["invite_id"], status=IS.TIMED_OUT.value)
                    self._event(conn, now, "INVITE_TIMED_OUT", campaign,
                                detail={"player_id": inv["player_id"], "deadline": inv["reply_deadline"]})
            if approval.strategy == "sequential" and State(campaign["state"]) == S.AWAITING_REPLY:
                self.dispatch(cid, now)
            else:
                self._check_batch(cid, now)
            campaign = self.storage.get_campaign(cid)
        state = State(campaign["state"])

        if state in OUTREACH_STATES:  # stale-data guard: did the seat/court go elsewhere?
            try:
                still_open = self._still_open(opp)
            except ProviderUnavailable:
                still_open = None  # do not guess; sends are blocked separately while the provider is down
            if still_open is False:
                profiles = self.profiles_fn(now)
                with self.storage.connect() as conn:
                    if opp.type == OpportunityType.EMPTY_COURT:
                        for h in (i for i in self.storage.invites(cid, conn) if i["status"] == IS.ACCEPTED.value):
                            p = profiles[h["player_id"]]
                            self.messaging.send(conn, campaign_id=cid, invite_id=h["invite_id"],
                                                player_id=p.player_id, channel=h["channel"], kind="court_gone",
                                                body=follow_up("court_gone", first_name=p.first_name, policy=policy,
                                                               opp=opp), now=now)
                    self._close_invites(conn, cid)
                    self._event(conn, now, "STALE_OPPORTUNITY", campaign, tool="get_snapshot",
                                detail={"detail": "seat or court taken outside Rally"})
                    self._transition(conn, campaign, S.ESCALATED, now,
                                     "the seat/court was taken outside Rally; outreach stopped")
                return

        if state in VERIFY_STATES:
            self.verify(cid, now, actor="code (auto-check)")
            campaign = self.storage.get_campaign(cid)
            hold = _dt(campaign["hold_deadline"])
            if campaign["state"] == S.AWAITING_BOOKING_VERIFICATION.value and hold and now > hold:
                with self.storage.connect() as conn:
                    self._transition(conn, campaign, S.ESCALATED, now,
                                     "booking not completed within the hold window; nothing confirmed")
            return

        pending = any(i["status"] == IS.SENT.value for i in self.storage.invites(cid))
        if state in PRE_BOOKING_STATES and now > approval.window_end and not pending:
            with self.storage.connect() as conn:
                self._close_invites(conn, cid)
                self._transition(conn, campaign, S.EXPIRED, now, "outreach window ended without a completed booking")

    # ---- verification ---------------------------------------------------------------------------
    def _check_provider(self, opp: Opportunity, held: list[dict], policy: ClubPolicy) -> VerificationResult:
        required = policy.booking.required_payment_status
        try:
            if opp.type == OpportunityType.MISSING_PLAYER:
                if not held:
                    return VerificationResult(status="mismatch", detail="no accepted player to verify")
                pid = held[0]["player_id"]
                match = self.provider.get_match(opp.match_id)
                if match is None:
                    return VerificationResult(status="mismatch", detail=f"match {opp.match_id} no longer exists")
                if pid in match.player_ids:
                    if match.payment_status == required:
                        return VerificationResult(status="confirmed", booking_id=match.match_id,
                                                  detail=f"{pid} is listed in {match.match_id} and payment is "
                                                         f"{match.payment_status}")
                    return VerificationResult(status="pending", detail=f"{pid} is listed but payment is "
                                                                       f"{match.payment_status}")
                if match.open_seats == 0:
                    return VerificationResult(status="mismatch", detail="the seat was filled by someone else")
                return VerificationResult(status="pending", detail=f"{pid} has not completed the booking link yet")
            ids = {h["player_id"] for h in held}
            bookings = self.provider.bookings_at(opp.court_id, opp.start, opp.end)
            if not bookings:
                return VerificationResult(status="pending", detail="no booking for the court yet")
            ours = [b for b in bookings if ids and ids <= set(b.player_ids)]
            if ours:
                booking = ours[0]
                if booking.payment_status == required:
                    return VerificationResult(status="confirmed", booking_id=booking.match_id,
                                              detail=f"booking {booking.match_id} lists all {len(ids)} players and "
                                                     f"payment is {booking.payment_status}")
                return VerificationResult(status="pending", booking_id=booking.match_id,
                                          detail=f"booking {booking.match_id} exists but payment is "
                                                 f"{booking.payment_status}")
            return VerificationResult(status="mismatch",
                                      detail="court booked by someone else: " + ", ".join(b.match_id for b in bookings))
        except ProviderUnavailable as exc:
            return VerificationResult(status="unavailable", detail=str(exc))

    @_guarded
    def verify(self, cid: str, now: datetime, actor: str = "code") -> VerificationResult:
        """verify_booking(): only a provider-confirmed booking can produce CONFIRMED."""
        campaign, _, opp = self._load(cid)
        state = State(campaign["state"])
        if state not in VERIFY_STATES:
            return VerificationResult(status="pending", detail=f"nothing to verify in state {state.value}")
        held = [i for i in self.storage.invites(cid) if i["status"] == IS.ACCEPTED.value]
        result = self._check_provider(opp, held, self.policy_fn())
        with self.storage.connect() as conn:
            if campaign["last_check"] != result.status or result.status == "confirmed":
                self.storage.update_campaign(conn, cid, now, last_check=result.status)
                self._event(conn, now, "BOOKING_CHECKED", campaign, actor=actor, tool="verify_booking",
                            detail=result.model_dump())
            if result.status == "confirmed":
                self._close_invites(conn, cid, booked=True)
                self._transition(conn, campaign, S.CONFIRMED, now, f"provider verified: {result.detail}",
                                 tool="verify_booking")
            elif result.status == "unavailable":
                self._transition(conn, campaign, S.UNVERIFIED, now,
                                 "provider unavailable: the booking cannot be verified, so it is NOT confirmed",
                                 tool="verify_booking")
            elif result.status == "mismatch":
                self._transition(conn, campaign, S.ESCALATED, now, f"provider shows something else: {result.detail}",
                                 tool="verify_booking")
            elif state == S.UNVERIFIED:
                self._transition(conn, campaign, S.AWAITING_BOOKING_VERIFICATION, now,
                                 f"provider reachable again; {result.detail}", tool="verify_booking")
        return result

    # ---- manager actions ------------------------------------------------------------------------
    @_guarded
    def manager_stop(self, cid: str, now: datetime, manager: str) -> None:
        campaign = self.storage.get_campaign(cid)
        with self.storage.connect() as conn:
            self._close_invites(conn, cid)
            self._transition(conn, campaign, S.CANCELLED, now, f"stopped by {manager}; no further sends",
                             actor=f"manager:{manager}", tool="manager_stop")

    @_guarded
    def manager_resume(self, cid: str, now: datetime, manager: str) -> list[DispatchResult]:
        """After an escalation in sequential outreach: skip the unclear reply and invite the next candidate."""
        campaign = self.storage.get_campaign(cid)
        if campaign["state"] != S.ESCALATED.value or campaign["strategy"] != "sequential":
            raise ValueError("Only an escalated missing-player campaign can be resumed.")
        with self.storage.connect() as conn:
            for inv in self.storage.invites(cid, conn):
                if inv["status"] == IS.NEEDS_MANAGER.value:
                    self.storage.update_invite(conn, inv["invite_id"], status=IS.SKIPPED.value)
                    self._event(conn, now, "MANAGER_SKIPPED", campaign, actor=f"manager:{manager}",
                                tool="manager_resume", detail={"player_id": inv["player_id"]})
        return self.dispatch(cid, now)
