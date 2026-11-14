"""Hard invariants from PRD section 4, checked with code assertions (no LLM grading)."""

import sqlite3
from datetime import timedelta

import pytest

from conftest import EC_LATE, EC_MAIN, EC_OTHER, MP_MAIN, MP_SAFETY, approve, get_view, invites, state
from rally.approvals import ApprovalError
from rally.coordinator import InvalidTransition
from rally.schemas import OpportunityType, State


def test_nothing_is_sent_before_approval(engine):
    engine.advance(30)
    assert engine.storage.count_messages() == 0
    assert all(v.state in (State.AWAITING_APPROVAL, State.ESCALATED)
               for t in OpportunityType for v in engine.views(t))


def test_sequential_outreach_sends_one_at_a_time(engine):
    cid = approve(engine, MP_MAIN, n=3)
    sent = [p for p, i in invites(engine, cid).items() if i["sent_at"]]
    assert sent == ["P005"]
    assert state(engine, cid) == "AWAITING_REPLY"


def test_unapproved_player_is_blocked_and_logged(engine):
    cid = approve(engine, MP_MAIN, n=2)
    before = engine.storage.count_messages("out")
    result = engine.drill_unapproved_send(cid)
    assert not result.sent and "approved candidate list" in result.reason
    assert engine.storage.count_messages("out") == before
    assert any(e["event_type"] == "INVITE_BLOCKED" for e in engine.storage.events(campaign_id=cid))


def test_duplicate_send_is_suppressed(engine):
    cid = approve(engine, MP_MAIN, n=2)
    before = engine.storage.count_messages("out")
    result = engine.drill_duplicate_send(cid)
    assert not result.sent and result.level == "duplicate"
    assert engine.storage.count_messages("out") == before


def test_missing_player_needs_provider_verification(engine):
    cid = approve(engine, MP_MAIN, n=3)
    engine.reply(f"{cid}:P005", "Sorry, can't make it")
    assert invites(engine, cid)["P006"]["status"] == "SENT"          # moved to the next approved candidate
    engine.reply(f"{cid}:P006", "Yes, I'm in!")
    assert state(engine, cid) == "AWAITING_BOOKING_VERIFICATION"      # a yes is not a confirmation
    assert any(m["kind"] == "booking_link" for m in engine.storage.messages(cid, "P006"))
    engine.simulate_provider_confirmation(cid)
    assert state(engine, cid) == "CONFIRMED"
    confirm = [e for e in engine.storage.events(campaign_id=cid) if e["to_state"] == "CONFIRMED"]
    assert confirm and confirm[0]["tool"] == "verify_booking"


def test_timeout_moves_to_next_candidate(engine):
    cid = approve(engine, MP_MAIN, n=3)
    engine.advance(engine.policy().timing.reply_timeout_minutes + 1)
    inv = invites(engine, cid)
    assert inv["P005"]["status"] == "TIMED_OUT"
    assert inv["P006"]["status"] == "SENT"


def test_approved_list_exhausted_escalates(engine):
    cid = approve(engine, MP_MAIN, n=1)
    engine.reply(f"{cid}:P005", "no thanks")
    assert state(engine, cid) == "ESCALATED"


def test_four_yes_replies_are_not_a_sale(engine):
    cid = approve(engine, EC_MAIN, n=6)
    for pid in list(invites(engine, cid))[:4]:
        engine.reply(f"{cid}:{pid}", "yes")
    assert state(engine, cid) == "AWAITING_BOOKING_VERIFICATION"
    assert not any(e["to_state"] == "CONFIRMED" for e in engine.storage.events(campaign_id=cid))


def test_no_seat_is_allocated_twice(engine):
    cid = approve(engine, EC_MAIN, n=6)
    for pid in invites(engine, cid):
        engine.reply(f"{cid}:{pid}", "yes")
    inv = invites(engine, cid).values()
    seats = [i["seat_no"] for i in inv if i["status"] == "ACCEPTED"]
    assert sorted(seats) == [1, 2, 3, 4]
    assert sum(1 for i in inv if i["status"] == "WAITLISTED") == 2
    with pytest.raises(sqlite3.IntegrityError):  # the database itself refuses a second holder of seat 1
        with engine.storage.connect() as conn:
            conn.execute("UPDATE invites SET seat_no = 1 WHERE campaign_id = ? AND status = 'WAITLISTED'", (cid,))


def test_payment_pending_is_not_confirmed(engine):
    cid = approve(engine, EC_MAIN, n=6)
    for pid in list(invites(engine, cid))[:4]:
        engine.reply(f"{cid}:{pid}", "yes")
    engine.simulate_provider_confirmation(cid, payment_status="pending")
    assert state(engine, cid) == "AWAITING_BOOKING_VERIFICATION"
    engine.simulate_provider_confirmation(cid, payment_status="paid")
    assert state(engine, cid) == "CONFIRMED"


def test_provider_outage_means_unverified_not_confirmed(engine):
    cid = approve(engine, MP_MAIN, n=1)
    engine.reply(f"{cid}:P005", "yes")
    engine.provider.simulate_seat_filled("M-OPEN-1730-C2", "P005", "paid")
    engine.set_provider_outage(True)
    assert state(engine, cid) == "UNVERIFIED"
    engine.set_provider_outage(False)
    assert state(engine, cid) == "CONFIRMED"


def test_stale_court_stops_outreach(engine):
    cid = approve(engine, EC_MAIN, n=6)
    first = list(invites(engine, cid))[0]
    engine.reply(f"{cid}:{first}", "yes")
    engine.simulate_taken_elsewhere(EC_MAIN)
    assert state(engine, cid) == "ESCALATED"
    assert any(m["kind"] == "court_gone" for m in engine.storage.messages(cid, first))
    assert not any(m["kind"] == "booking_link" for m in engine.storage.messages(cid))


def test_seat_filled_elsewhere_stops_outreach(engine):
    cid = approve(engine, MP_MAIN, n=3)
    engine.simulate_taken_elsewhere(MP_MAIN)
    assert state(engine, cid) == "ESCALATED"
    engine.advance(25)
    assert sum(1 for i in invites(engine, cid).values() if i["sent_at"]) == 1  # nobody else was messaged


def test_stop_revokes_consent_everywhere(engine):
    cid = approve(engine, MP_SAFETY, n=3)
    engine.reply(f"{cid}:P024", "STOP")
    assert engine.profiles()["P024"].opt_in_status == "opted_out"
    assert invites(engine, cid)["P026"]["status"] == "SENT"   # outreach continues with the next player
    for t in OpportunityType:
        for view in engine.views(t):
            if view.eligibility:
                reasons = {e.player_id: e.reason_code for e in view.eligibility.exclusions}
                assert reasons.get("P024") in ("OPTED_OUT", None) and "P024" not in view.eligibility.eligible_ids
    outbound = [m["kind"] for m in engine.storage.messages(player_id="P024") if m["direction"] == "out"]
    assert outbound == ["invite", "opt_out_ack"]


def test_consent_is_rechecked_at_send_time(engine):
    cid = approve(engine, MP_SAFETY, n=3)        # P024 invited first; P026 and P025 queued
    with engine.storage.connect() as conn:       # P026 opts out at the front desk before their turn
        engine.storage.set_consent(conn, "P026", "opted_out", engine.now(), source="front desk (test)")
    engine.reply(f"{cid}:P024", "no thanks")
    inv = invites(engine, cid)
    assert inv["P026"]["status"] == "NOT_SENT" and inv["P026"]["sent_at"] is None
    assert inv["P025"]["status"] == "SENT"


def test_ambiguous_reply_escalates_without_booking_link(engine):
    cid = approve(engine, MP_SAFETY, n=3)
    engine.reply(f"{cid}:P024", "maybe, depends on work")
    assert state(engine, cid) == "ESCALATED"
    assert not any(m["kind"] == "booking_link" for m in engine.storage.messages(cid))
    engine.resume(cid)
    assert invites(engine, cid)["P024"]["status"] == "SKIPPED"
    assert invites(engine, cid)["P026"]["status"] == "SENT"


def test_quiet_hours_block_sends(engine):
    engine.advance(10 * 60 + 15)   # 21:05, after quiet hours start
    view = get_view(engine, EC_LATE)
    template, source, _ = engine.draft(EC_LATE)
    ids = [c.player_id for c in view.shortlist.candidates[:6]]
    cid = engine.approve(EC_LATE, ids, 6, view.opp.start - timedelta(minutes=45),
                         engine.policy().consent.allowed_channels, template, source)
    assert engine.storage.count_messages("out") == 0
    assert state(engine, cid) in ("ESCALATED", "EXPIRED")
    assert any("quiet hours" in str(e["detail"]) for e in engine.storage.events(campaign_id=cid))


def test_manager_stop_cancels_future_sends(engine):
    cid = approve(engine, MP_MAIN, n=3)
    engine.stop(cid)
    engine.advance(30)
    assert state(engine, cid) == "CANCELLED"
    assert sum(1 for i in invites(engine, cid).values() if i["sent_at"]) == 1


def test_approval_scope_is_validated(engine):
    view = get_view(engine, MP_MAIN)
    template, source, _ = engine.draft(MP_MAIN)
    ids = [view.shortlist.candidates[0].player_id]
    with pytest.raises(ApprovalError):   # window ends too close to the game
        engine.approve(MP_MAIN, ids, 1, view.opp.start - timedelta(minutes=10), ["simulated_sms"], template, source)
    with pytest.raises(ApprovalError):   # P010 is excluded (contact cap), so it cannot be approved
        engine.approve(MP_MAIN, ["P010"], 1, view.opp.start - timedelta(minutes=45), ["simulated_sms"], template,
                       source)
    with pytest.raises(ApprovalError):   # edited message with a different price
        engine.approve(MP_MAIN, ids, 1, view.opp.start - timedelta(minutes=45), ["simulated_sms"],
                       template.replace("$30", "$10"), "manager_edited")


def test_pending_invite_excludes_player_from_other_games(engine):
    cid = approve(engine, EC_MAIN, n=6)
    pending = set(invites(engine, cid))
    other = get_view(engine, EC_OTHER)
    reasons = {e.player_id: e.reason_code for e in other.eligibility.exclusions}
    assert all(reasons.get(p) == "PENDING_INVITE" for p in pending)


def test_events_are_append_only(engine):
    with pytest.raises(sqlite3.DatabaseError):
        with engine.storage.connect() as conn:
            conn.execute("UPDATE events SET event_type = 'X'")
    with pytest.raises(sqlite3.DatabaseError):
        with engine.storage.connect() as conn:
            conn.execute("DELETE FROM events")


def test_invalid_transition_is_rejected_and_logged(engine, monkeypatch):
    cid = approve(engine, MP_MAIN, n=1)
    coord = engine.coordinator

    def skip_verification(campaign_id, now):
        campaign = engine.storage.get_campaign(campaign_id)
        with engine.storage.connect() as conn:
            coord._transition(conn, campaign, State.CONFIRMED, now, "test: try to skip booking verification")

    monkeypatch.setattr(coord, "_tick_campaign", skip_verification)
    with pytest.raises(InvalidTransition):
        coord.tick(engine.now())
    assert state(engine, cid) == "AWAITING_REPLY"
    logged = [e for e in engine.storage.events(campaign_id=cid) if e["event_type"] == "INVALID_TRANSITION"]
    assert logged and logged[0]["to_state"] == "CONFIRMED"


def test_every_event_carries_policy_version(engine):
    approve(engine, MP_MAIN, n=1)
    version = engine.policy().policy_version
    assert all(e["policy_version"] == version for e in engine.storage.events())


def test_short_batch_invites_approved_backups_without_new_approval(engine):
    """Golden G16: 2 yes + 2 no in the first wave; the two approved backups are invited next."""
    cid = approve(engine, EC_MAIN, n=6, first_wave=4)
    order = list(invites(engine, cid))
    assert [p for p in order if invites(engine, cid)[p]["sent_at"]] == order[:4]
    for pid, text in zip(order[:4], ["yes", "yes", "no", "no"]):
        engine.reply(f"{cid}:{pid}", text)
    inv = invites(engine, cid)
    assert all(inv[p]["sent_at"] for p in order[4:])
    assert sum(i["status"] == "ACCEPTED" for i in inv.values()) == 2
    assert state(engine, cid) != "ESCALATED"
    assert {m["player_id"] for m in engine.storage.messages(campaign_id=cid)} <= set(order)


def test_short_batch_with_no_backups_still_escalates(engine):
    cid = approve(engine, EC_MAIN, n=4)
    for pid, text in zip(invites(engine, cid), ["yes", "yes", "no", "no"]):
        engine.reply(f"{cid}:{pid}", text)
    assert state(engine, cid) == "ESCALATED"


def test_first_wave_must_fit_the_approval(engine):
    with pytest.raises(ApprovalError):
        approve(engine, EC_MAIN, n=6, max_sends=4, first_wave=5)
    with pytest.raises(ApprovalError):
        approve(engine, MP_MAIN, n=3, first_wave=2)


def test_empty_court_invite_must_state_the_booking_condition(engine):
    """Golden G14: no 'you get the spot' wording, and the 4-players-and-booking condition is required."""
    from rally.messaging import validate_invite_text
    template, _, facts = engine.draft(EC_MAIN)
    assert validate_invite_text(template, facts) == []
    assert "only if 4 players" in template
    promise = template.replace("It goes ahead only if 4 players say YES and book; we'll send a booking link then. ",
                               "The first 4 to reply YES get the spots. ")
    problems = validate_invite_text(promise, facts)
    assert any("promises a spot" in p for p in problems)
    assert any("only goes ahead if 4" in p for p in problems)
