"""Messaging: simulated channel adapter, message templates and draft validation (decision D07).

No real SMS or WhatsApp message is ever sent. The only placeholder allowed in an approved template
is {first_name}; every other fact is fixed at approval time, so the LLM cannot change it later.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime

from .fmt import day_label, level_label, range_label, when_label
from .policy import ClubPolicy
from .schemas import Opportunity, OpportunityType
from .storage import Storage

MAX_SMS_CHARS = 320


def price_per_player(policy: ClubPolicy) -> int:
    hours = policy.schedule.slot_minutes / 60
    return round(policy.club.court_price_per_hour_usd * hours / policy.club.players_per_court)


def invite_facts(opp: Opportunity, policy: ClubPolicy, now: datetime, group_level: float | None) -> dict:
    """The only facts an invitation may contain (PRD: 'allowable template facts')."""
    tol = policy.eligibility.skill_tolerance
    level = opp.level_target if opp.type == OpportunityType.MISSING_PLAYER else group_level
    level_text = (level_label(level) if opp.type == OpportunityType.MISSING_PLAYER or level is None
                  else f"{level - tol:.1f}–{level + tol:.1f}")
    return {
        "club": policy.club.name,
        "court": opp.court_name,
        "when": when_label(opp.start, opp.end, now),
        "date": day_label(opp.start),
        "time": range_label(opp.start, opp.end),
        "level": level_text,
        "seats": opp.seats_needed,
        "game_type": "open match with one spot left" if opp.type == OpportunityType.MISSING_PLAYER
                     else "new game for four",
        "price_per_player_usd": price_per_player(policy),
        "reply_options": "YES to join, NO to pass",
        "opt_out_line": "Reply STOP to opt out.",
    }


def template_invite(opp: Opportunity, facts: dict) -> str:
    if opp.type == OpportunityType.MISSING_PLAYER:
        return (f"Hi {{first_name}}, it's {facts['club']}. Our open match {facts['when']} on {facts['court']} "
                f"(level {facts['level']}) has 1 spot left. Want it? Reply YES to join or NO to pass. "
                f"${facts['price_per_player_usd']}/player. Reply STOP to opt out.")
    return (f"Hi {{first_name}}, it's {facts['club']}. We're putting together a level {facts['level']} game "
            f"{facts['when']} on {facts['court']}. The first 4 to reply YES get the spots, "
            f"${facts['price_per_player_usd']}/player. Reply NO to pass. Reply STOP to opt out.")


def validate_invite_text(text: str, facts: dict) -> list[str]:
    """Code-side checks on any drafted or edited invitation. An empty list means it passed."""
    problems = []
    if len(text) > MAX_SMS_CHARS:
        problems.append(f"too long ({len(text)} > {MAX_SMS_CHARS} characters)")
    if "{first_name}" not in text:
        problems.append("missing the {first_name} placeholder")
    other = set(re.findall(r"\{[^}]*\}", text)) - {"{first_name}"}
    if other:
        problems.append(f"unknown placeholder(s): {', '.join(sorted(other))}")
    if "STOP" not in text:
        problems.append("missing the 'Reply STOP to opt out' line")
    if facts["time"].split("–")[0] not in text:
        problems.append(f"missing the start time ({facts['time'].split('–')[0]})")
    if facts["court"] not in text:
        problems.append(f"missing the court ({facts['court']})")
    if re.search(r"https?://|www\.", text, re.I):
        problems.append("contains a link (booking links are only sent after a clear yes)")
    prices = {int(p) for p in re.findall(r"\$\s?(\d+)", text)}
    if prices - {facts["price_per_player_usd"]}:
        problems.append(f"mentions a price other than ${facts['price_per_player_usd']}/player")
    return problems


def render(template: str, first_name: str) -> str:
    return template.replace("{first_name}", first_name)


def booking_link(policy: ClubPolicy, opportunity_id: str, invite_id: str) -> str:
    token = uuid.uuid5(uuid.NAMESPACE_URL, invite_id).hex[:10]
    return f"{policy.booking.booking_link_base}/{opportunity_id}?t={token}"


def follow_up(kind: str, *, first_name: str, policy: ClubPolicy, opp: Opportunity, link: str = "") -> str:
    when = f"{day_label(opp.start)} {range_label(opp.start, opp.end)}"
    texts = {
        "booking_link_missing": (f"You're in, {first_name}! Complete your booking for {when} on {opp.court_name} "
                                 f"here: {link} (simulated link). Your spot is confirmed once the booking shows as "
                                 f"paid; it is held for {policy.timing.booking_hold_minutes} min."),
        "booking_link_group": (f"Great news, {first_name}: your group of 4 is set for {when} on {opp.court_name}. "
                               f"Book and pay your share here: {link} (simulated link). The court is held for "
                               f"{policy.timing.booking_hold_minutes} min."),
        "waitlist": (f"Thanks {first_name}! The 4 spots for {when} just filled, so you're on the waitlist. "
                     "We'll message you if a spot opens."),
        "seat_gone": f"Sorry {first_name}, that spot for {when} was just filled. Thanks for the quick reply!",
        "court_gone": (f"Sorry {first_name}, {opp.court_name} for {when} was just booked by another customer, "
                       "so this game is off. No charge."),
        "opt_out_ack": f"You're unsubscribed from {policy.club.name} Rally invites. You won't get more of these.",
    }
    return texts[kind]


class SimulatedMessagingAdapter:
    """Writes outgoing/incoming messages to SQLite instead of a phone network."""

    name = "simulated"

    def __init__(self, storage: Storage):
        self.storage = storage

    def send(self, conn: sqlite3.Connection, *, campaign_id: str | None, invite_id: str | None, player_id: str,
             channel: str | None, kind: str, body: str, now: datetime) -> str:
        message_id = f"MSG-{uuid.uuid4().hex[:10]}"
        self.storage.insert_message(conn, message_id=message_id, campaign_id=campaign_id, invite_id=invite_id,
                                    player_id=player_id, direction="out", kind=kind, channel=channel, body=body,
                                    now=now)
        return message_id

    def receive(self, conn: sqlite3.Connection, *, campaign_id: str | None, invite_id: str | None, player_id: str,
                channel: str | None, body: str, now: datetime) -> str:
        message_id = f"MSG-{uuid.uuid4().hex[:10]}"
        self.storage.insert_message(conn, message_id=message_id, campaign_id=campaign_id, invite_id=invite_id,
                                    player_id=player_id, direction="in", kind="reply", channel=channel, body=body,
                                    now=now)
        return message_id


class WhatsAppAdapter:  # pragma: no cover - future adapter boundary
    name = "whatsapp"

    def __init__(self, *_, **__):
        raise NotImplementedError("Live WhatsApp messaging is out of scope for HW2 (PRD D07).")
