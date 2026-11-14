"""RallyEngine: the single facade the Streamlit app, the demo script and the tests talk to.

It wires the policy, mock provider, SQLite storage, LLM service and coordinator together, and owns
the simulated clock. All state lives in SQLite under runtime/, so a page refresh loses nothing.
"""

from __future__ import annotations

import csv
import os
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .approvals import ApprovalError, approve_campaign
from .coordinator import Coordinator, DispatchResult, ReplyOutcome
from .detection import detect_opportunities
from .eligibility import filter_eligible
from .llm import LLMService
from .messaging import SimulatedMessagingAdapter, invite_facts, template_invite, validate_invite_text
from .occupancy import OccupancyProfile, slot_grid
from .policy import ROOT_DIR, ClubPolicy, load_policy
from .providers import MockBookingProvider, ProviderUnavailable
from .ranking import rank_candidates
from .schemas import (TERMINAL_STATES, CampaignApproval, EligibilityResult, InviteStatus, NotActionable,
                      Opportunity, OpportunityType, PlayerProfile, Shortlist, Snapshot, State)
from .storage import Storage

__all__ = ["RallyEngine", "OpportunityView", "ApprovalError"]


def _split(value: str | None) -> list[str]:
    return [v for v in (value or "").split(";") if v]


@dataclass
class OpportunityView:
    opp: Opportunity
    state: State
    state_reason: str
    campaign: dict | None
    approval: CampaignApproval | None
    invites: list[dict]
    can_approve: bool
    eligibility: EligibilityResult | None = None
    shortlist: Shortlist | None = None
    previous: list[dict] = field(default_factory=list)


class RallyEngine:
    MANAGER = "Demo manager"

    def __init__(self, root: str | Path = ROOT_DIR, runtime_dir: str | Path | None = None, llm_mode: str = "auto",
                 model: str | None = None):
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.runtime_dir = Path(runtime_dir or os.environ.get("RALLY_RUNTIME_DIR") or self.root / "runtime")
        self.policy_path = self.data_dir / "club_policy.yaml"
        self._llm_mode, self._llm_model = llm_mode, model
        self._open()

    # ---- setup ------------------------------------------------------------------------------------
    def _open(self) -> None:
        policy = self.policy()
        self.tz = ZoneInfo(policy.club.timezone)
        self.storage = Storage(self.runtime_dir / "rally.db")
        self.provider = MockBookingProvider(self.runtime_dir / "mock_provider.db", self.data_dir)
        self.occupancy = OccupancyProfile.from_csv(self.data_dir / "occupancy_profile.csv")
        self.messaging = SimulatedMessagingAdapter(self.storage)
        self.llm = LLMService(self._llm_mode, self._llm_model, default_model=policy.llm.default_model)
        self.coordinator = Coordinator(self.storage, self.provider, self.messaging, self.llm, self.policy,
                                       self.profiles, self.opportunity)
        self._players = self._load_players()
        self._snapshot: Snapshot | None = None
        self._skipped: list[NotActionable] = []
        self._seed()

    def _load_players(self) -> dict[str, dict]:
        with open(self.data_dir / "players.csv", newline="", encoding="utf-8") as f:
            players = {r["player_id"]: r for r in csv.DictReader(f)}
        with open(self.data_dir / "player_preferences.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                players[r["player_id"]].update(r)
        return players

    def _seed(self) -> None:
        if self.storage.get_meta("sim_now"):
            return
        policy = self.policy()
        start = datetime.fromisoformat(policy.demo.start_time).replace(tzinfo=self.tz)
        self.storage.set_meta("sim_now", start.isoformat())
        self.storage.set_meta("provider_status", "ok")
        prior = [(pid, start - timedelta(days=int(d)))
                 for pid, row in self._players.items() for d in _split(row.get("prior_invites_days_ago"))]
        self.storage.seed_prior_invites(prior)
        with self.storage.connect() as conn:
            self.storage.log_event(conn, sim_ts=start, actor="system", tool="reset", event_type="DEMO_STARTED",
                                   policy_version=policy.policy_version,
                                   detail={"club": policy.club.name, "policy_sha256": policy.sha256,
                                           "data": "fictional players; occupancy profile from the club spreadsheet"})

    def reset(self) -> None:
        shutil.rmtree(self.runtime_dir, ignore_errors=True)
        self._open()

    def set_llm(self, mode: str, model: str | None) -> None:
        self._llm_mode, self._llm_model = mode, model
        self.llm.configure(mode, model)

    # ---- basics -----------------------------------------------------------------------------------
    def policy(self) -> ClubPolicy:
        return load_policy(self.policy_path)

    def now(self) -> datetime:
        return datetime.fromisoformat(self.storage.get_meta("sim_now"))

    def _log(self, event_type: str, *, actor: str, tool: str, detail: dict, campaign_id: str | None = None,
             opportunity_id: str | None = None, from_state: str | None = None, to_state: str | None = None) -> None:
        with self.storage.connect() as conn:
            self.storage.log_event(conn, sim_ts=self.now(), actor=actor, tool=tool, event_type=event_type,
                                   policy_version=self.policy().policy_version, detail=detail,
                                   campaign_id=campaign_id, opportunity_id=opportunity_id, from_state=from_state,
                                   to_state=to_state)

    def advance(self, minutes: int, reason: str = "manager advanced the simulated clock") -> None:
        before = self.now()
        after = before + timedelta(minutes=minutes)
        self.storage.set_meta("sim_now", after.isoformat())
        self._log("CLOCK_ADVANCED", actor="system", tool="sim_clock",
                  detail={"from": before.isoformat(), "to": after.isoformat(), "reason": reason})
        self.refresh()

    def profiles(self, now: datetime | None = None) -> dict[str, PlayerProfile]:
        now = now or self.now()
        overrides = self.storage.consent_overrides()
        times = self.storage.invite_times()
        pending: dict[str, str] = {}
        for c in self.storage.campaigns():
            if State(c["state"]) in TERMINAL_STATES:
                continue
            for inv in self.storage.invites(c["campaign_id"]):
                if inv["status"] in (InviteStatus.SENT.value, InviteStatus.NEEDS_MANAGER.value,
                                     InviteStatus.ACCEPTED.value):
                    pending[inv["player_id"]] = c["campaign_id"]
        out = {}
        for pid, r in self._players.items():
            status = overrides[pid]["opt_in_status"] if pid in overrides else r["opt_in_status"]
            recent = [t for t in times.get(pid, []) if now - timedelta(days=7) < t <= now]
            out[pid] = PlayerProfile(
                player_id=pid, name=r["name"], first_name=r["first_name"], level=float(r["level"]), phone=r["phone"],
                opt_in_status=status, opt_in_channel=r["opt_in_channel"] or None,
                opt_in_source=r["opt_in_source"] or None, opt_in_at=r["opt_in_at"] or None,
                preferred_bands=_split(r.get("preferred_bands")), played_with=_split(r.get("played_with")),
                last_played_days_ago=int(r["last_played_days_ago"]) if r.get("last_played_days_ago") else None,
                invites_last_7d=len(recent), pending_invite_campaign=pending.get(pid))
        return out

    def opportunity(self, opp_id: str) -> Opportunity:
        row = self.storage.get_opportunity(opp_id)
        if row is None:
            raise KeyError(opp_id)
        return Opportunity.model_validate(row["payload"])

    # ---- detection loop (runs on every page refresh; never sends) ----------------------------------
    def refresh(self) -> str:
        now, policy = self.now(), self.policy()
        self.coordinator.tick(now)
        try:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            snap = self.provider.get_snapshot(policy.club.club_id, day_start,
                                              now + timedelta(hours=policy.timing.detection_horizon_hours + 3), now)
            status = "ok"
        except ProviderUnavailable:
            snap, status = None, "unavailable"
        if (self.storage.get_meta("provider_status") or "ok") != status:
            self.storage.set_meta("provider_status", status)
            self._log("PROVIDER_STATUS", actor="provider", tool="get_snapshot",
                      detail={"status": status, "effect": "detection paused; sends, approvals and confirmations "
                                                          "blocked" if status != "ok" else "back to normal"})
        if snap is None:
            return status
        self._snapshot = snap
        opps, self._skipped = detect_opportunities(snap, self.occupancy, policy, now)
        existing = {row["opportunity_id"]: row for row in self.storage.opportunities()}
        live = {c["opportunity_id"] for c in self.storage.campaigns() if State(c["state"]) not in TERMINAL_STATES}
        new_opps = []
        with self.storage.connect() as conn:
            for opp in opps:
                if opp.opportunity_id in existing:
                    opp.detected_at = datetime.fromisoformat(existing[opp.opportunity_id]["first_detected_at"])
                if self.storage.upsert_opportunity(conn, opp.opportunity_id, opp.type.value,
                                                   opp.model_dump(mode="json"), now):
                    new_opps.append(opp)
                    self.storage.log_event(conn, sim_ts=now, actor="code", tool="detect_opportunities",
                                           event_type="OPPORTUNITY_DETECTED", policy_version=policy.policy_version,
                                           opportunity_id=opp.opportunity_id, to_state=State.DETECTED.value,
                                           detail={"type": opp.type.value, "court": opp.court_name,
                                                   "start": opp.start.isoformat(), "reasons": opp.reasons})
            seen = {o.opportunity_id for o in opps}
            for opp_id, row in existing.items():
                if row["active"] and opp_id not in seen and opp_id not in live:
                    self.storage.deactivate_opportunity(conn, opp_id)
                    self.storage.log_event(conn, sim_ts=now, actor="code", tool="detect_opportunities",
                                           event_type="OPPORTUNITY_CLEARED", policy_version=policy.policy_version,
                                           opportunity_id=opp_id,
                                           detail={"reason": "no longer detected (booked, filled, started, or "
                                                             "outside the detection window)"})
        for opp in new_opps:
            elig, sl = self.evaluate(opp)
            excluded = Counter(e.reason_code for e in elig.exclusions)
            self._log("SHORTLIST_READY", actor="code", tool="filter_eligible+rank_candidates",
                      opportunity_id=opp.opportunity_id, from_state=State.DETECTED.value,
                      to_state=(State.AWAITING_APPROVAL if sl.candidates else State.ESCALATED).value,
                      detail={"eligible": len(elig.eligible_ids), "excluded_by_reason": dict(excluded),
                              "top": [f"{c.name} ({c.total})" for c in sl.candidates[:6]],
                              "group": sl.group_note, "escalation": sl.escalation,
                              "note": "nothing is sent until a manager approves"})
        return status

    @property
    def provider_status(self) -> str:
        return self.storage.get_meta("provider_status") or "ok"

    @property
    def not_actionable(self) -> list[NotActionable]:
        return self._skipped

    @property
    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    def evaluate(self, opp: Opportunity) -> tuple[EligibilityResult, Shortlist]:
        policy, profiles = self.policy(), self.profiles()
        snap = self._snapshot
        if snap is None:
            raise ProviderUnavailable("no provider snapshot available")
        elig = filter_eligible(opp, list(profiles.values()), snap, policy)
        names = {pid: p.name for pid, p in profiles.items()}
        shortlist = rank_candidates(opp, [profiles[pid] for pid in elig.eligible_ids], policy, names)
        return elig, shortlist

    # ---- views for the UI --------------------------------------------------------------------------
    def views(self, opp_type: OpportunityType) -> list[OpportunityView]:
        by_opp: dict[str, list[dict]] = {}
        for c in self.storage.campaigns():
            by_opp.setdefault(c["opportunity_id"], []).append(c)
        provider_ok = self.provider_status == "ok" and self._snapshot is not None
        views = []
        for row in self.storage.opportunities():
            if row["type"] != opp_type.value:
                continue
            opp = Opportunity.model_validate(row["payload"])
            camps = by_opp.get(opp.opportunity_id, [])
            latest = camps[-1] if camps else None
            live = latest is not None and State(latest["state"]) not in TERMINAL_STATES
            if not row["active"] and latest is None:
                continue
            can_approve = bool(row["active"]) and not live and provider_ok
            view = OpportunityView(
                opp=opp, state=State.AWAITING_APPROVAL, state_reason="", campaign=latest,
                approval=CampaignApproval.model_validate(latest["approval"]) if latest else None,
                invites=self.storage.invites(latest["campaign_id"]) if latest else [], can_approve=can_approve,
                previous=camps[:-1])
            if can_approve:
                view.eligibility, view.shortlist = self.evaluate(opp)
                if latest is None:
                    view.state = State.AWAITING_APPROVAL if view.shortlist.candidates else State.ESCALATED
                    view.state_reason = view.shortlist.escalation or (
                        "Ranked shortlist ready. Nothing is sent until a manager approves.")
                else:
                    view.state, view.state_reason = State(latest["state"]), latest["state_reason"] or ""
            elif latest is not None:
                view.state, view.state_reason = State(latest["state"]), latest["state_reason"] or ""
            else:
                view.state, view.state_reason = State.DETECTED, "Provider unavailable: shortlist paused."
            views.append(view)
        return sorted(views, key=lambda v: (v.state in TERMINAL_STATES and not v.can_approve, v.opp.start))

    # ---- manager actions ---------------------------------------------------------------------------
    def draft(self, opp_id: str) -> tuple[str, str, dict]:
        """The invite template a manager reviews. Drafted once per opportunity (Claude in live mode)."""
        now, policy = self.now(), self.policy()
        opp = self.opportunity(opp_id)
        _, shortlist = self.evaluate(opp)
        facts = invite_facts(opp, policy, now, shortlist.group_level)
        stored = self.storage.get_draft(opp_id)
        if stored and not validate_invite_text(stored["template"], facts) and (
                self.llm.live == stored["source"].startswith("llm")):
            return stored["template"], stored["source"], facts
        text, source, record = self.llm.draft_invite(facts, template_invite(opp, facts))
        if self.llm.live:
            record.update(sim_ts=now.isoformat())
            self.storage.log_llm_call(record)
        self.storage.set_draft(opp_id, text, source, now)
        self._log("DRAFT_CREATED", actor="llm" if self.llm.live else "rules", tool="draft_invite",
                  opportunity_id=opp_id, detail={"source": source, "template": text})
        return text, source, facts

    def approve(self, opp_id: str, selected_ids: list[str], max_sends: int, window_end: datetime,
                channels: list[str], template: str, source: str) -> str:
        if self.provider.health() != "ok":
            raise ApprovalError("Provider unavailable: approvals are blocked until a fresh snapshot is available.")
        now, policy = self.now(), self.policy()
        opp = self.opportunity(opp_id)
        _, shortlist = self.evaluate(opp)
        facts = invite_facts(opp, policy, now, shortlist.group_level)
        approval = approve_campaign(
            opp=opp, shortlist=shortlist, selected_ids=selected_ids, max_sends=max_sends, window_end=window_end,
            channels=channels, message_template=template, message_source=source, facts=facts, manager=self.MANAGER,
            policy=policy, now=now, attempt=len(self.storage.campaigns(opp_id)) + 1)
        cid = self.coordinator.start_campaign(approval, now)
        self.refresh()
        return cid

    def reply(self, invite_id: str, text: str) -> ReplyOutcome:
        outcome = self.coordinator.handle_reply(invite_id, text, self.now())
        self.advance(self.policy().demo.minutes_per_player_reply, reason="simulated time between replies")
        return outcome

    def stop(self, campaign_id: str) -> None:
        self.coordinator.manager_stop(campaign_id, self.now(), self.MANAGER)
        self.refresh()

    def resume(self, campaign_id: str) -> list[DispatchResult]:
        results = self.coordinator.manager_resume(campaign_id, self.now(), self.MANAGER)
        self.refresh()
        return results

    def check_booking(self, campaign_id: str):
        result = self.coordinator.verify(campaign_id, self.now(), actor=f"manager:{self.MANAGER}")
        self.refresh()
        return result

    # ---- provider fixtures (the manager plays the provider's part; all SIMULATED) ------------------
    def simulate_provider_confirmation(self, campaign_id: str, payment_status: str = "paid") -> str:
        campaign, _, opp = self.coordinator._load(campaign_id)
        held = [i["player_id"] for i in self.storage.invites(campaign_id) if i["status"] == InviteStatus.ACCEPTED.value]
        if not held:
            raise ValueError("No accepted players yet, so there is no booking to complete.")
        if opp.type == OpportunityType.MISSING_PLAYER:
            self.provider.simulate_seat_filled(opp.match_id, held[0], payment_status)
            what = f"{held[0]} completed the booking link for {opp.match_id}"
        else:
            booking_id = self.provider.simulate_group_booking(opp.court_id, opp.start, opp.end, held, payment_status)
            what = f"group booking {booking_id} created for {', '.join(held)}"
        self._log("PROVIDER_FIXTURE", actor="manager (simulated provider)", tool="mock_provider",
                  campaign_id=campaign_id, opportunity_id=opp.opportunity_id,
                  detail={"fixture": "booking completed", "what": what, "payment_status": payment_status,
                          "simulated": True})
        self.advance(3, reason="simulated time to complete the booking link")
        return self.storage.get_campaign(campaign_id)["state"]

    def simulate_taken_elsewhere(self, opp_id: str) -> None:
        opp = self.opportunity(opp_id)
        if opp.type == OpportunityType.MISSING_PLAYER:
            self.provider.simulate_seat_taken_elsewhere(opp.match_id)
            what = f"open seat in {opp.match_id} filled at the front desk"
        else:
            booking_id = self.provider.simulate_external_booking(opp.court_id, opp.start, opp.end)
            what = f"{opp.court_name} booked directly by another customer ({booking_id})"
        self._log("PROVIDER_FIXTURE", actor="manager (simulated provider)", tool="mock_provider",
                  opportunity_id=opp_id, detail={"fixture": "taken outside Rally", "what": what, "simulated": True})
        self.refresh()

    def set_provider_outage(self, on: bool) -> None:
        self.provider.set_outage(on)
        self._log("PROVIDER_FIXTURE", actor="manager (simulated provider)", tool="mock_provider",
                  detail={"fixture": "outage on" if on else "outage off", "simulated": True})
        self.refresh()

    # ---- safety drills (demo sequence 3) -----------------------------------------------------------
    def drill_unapproved_send(self, campaign_id: str) -> DispatchResult:
        _, approval, opp = self.coordinator._load(campaign_id)
        profiles = self.profiles()
        outsiders = [pid for pid, p in sorted(profiles.items()) if pid not in approval.approved_player_ids
                     and pid not in opp.present_player_ids and p.opt_in_status == "opted_in"]
        target = outsiders[0]
        self._log("SAFETY_DRILL", actor=f"manager:{self.MANAGER}", tool="dispatch_invite", campaign_id=campaign_id,
                  opportunity_id=opp.opportunity_id,
                  detail={"drill": "try to message a player outside the approved list", "player_id": target})
        return self.coordinator.dispatch_invite(campaign_id, target, self.now(), actor="manager:drill")

    def drill_duplicate_send(self, campaign_id: str) -> DispatchResult:
        sent = [i for i in self.storage.invites(campaign_id) if i["sent_at"]]
        if not sent:
            raise ValueError("No invite has been sent yet.")
        self._log("SAFETY_DRILL", actor=f"manager:{self.MANAGER}", tool="dispatch_invite", campaign_id=campaign_id,
                  detail={"drill": "re-send an invite that was already sent", "player_id": sent[0]["player_id"]})
        return self.coordinator.dispatch_invite(campaign_id, sent[0]["player_id"], self.now(),
                                                actor="manager:drill")

    # ---- read models for the UI --------------------------------------------------------------------
    def court_grid(self, day) -> list[dict]:
        """Every saleable slot x court for one day, as the last provider snapshot shows it."""
        policy = self.policy()
        hours = self.occupancy.opening_hours(policy.schedule.open_hour_min_occupancy_pct)
        open_h, close_h = hours[day.isoweekday()]
        matches = self.provider.all_matches()
        rows = []
        now = self.now()
        for start, end in slot_grid(day, open_h, close_h, policy.schedule.slot_minutes, self.tz):
            typical = self.occupancy.slot_occupancy(start, end)
            for court in range(1, policy.club.court_count + 1):
                cid = f"C{court}"
                m = next((m for m in matches if m.court_id == cid and m.start < end and start < m.end), None)
                if m is None:
                    status = "Empty (off-peak)" if typical < policy.schedule.off_peak_max_occupancy_pct else "Empty"
                elif m.kind == "club_open":
                    status = f"Open match {len(m.player_ids)}/{m.capacity}"
                elif m.created_by == "rally_handoff":
                    status = "Booked via Rally"
                else:
                    status = "Booked"
                rows.append({"slot": start.strftime("%H:%M"), "start": start, "court": f"Court {court}",
                             "status": status, "typical_occupancy_pct": typical, "past": end <= now,
                             "booking": m.match_id if m else ""})
        return rows
