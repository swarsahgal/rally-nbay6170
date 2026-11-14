"""Policy source, occupancy-derived schedule, detection and eligibility rules."""

import pytest
import yaml
from pydantic import ValidationError

from conftest import EC_LATE, EC_MAIN, EC_OTHER, MP_MAIN, MP_SAFETY, get_view
from rally.occupancy import OccupancyProfile
from rally.policy import DATA_DIR, load_policy
from rally.schemas import OpportunityType


def test_policy_loads_versioned_and_provisional():
    policy = load_policy()
    assert policy.policy_version.startswith("0.1.0")
    assert policy.status == "provisional"
    assert policy.sha256  # every trace can point back to the exact file


def test_policy_rejects_weights_that_do_not_sum_to_one(tmp_path):
    raw = yaml.safe_load((DATA_DIR / "club_policy.yaml").read_text())
    raw["ranking"]["weights"]["skill_fit"] = 0.9
    bad = tmp_path / "club_policy.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError):
        load_policy(bad)


def test_occupancy_profile_matches_spreadsheet():
    profile = OccupancyProfile.from_csv(DATA_DIR / "occupancy_profile.csv")
    assert len(profile.pct) == 168
    assert profile.at(2, 19) == pytest.approx(87.59)          # Tue 7pm, from the spreadsheet
    hours = profile.opening_hours(load_policy().schedule.open_hour_min_occupancy_pct)
    assert set(hours.values()) == {(7, 24)}                  # open 07:00-24:00 every day


def test_off_peak_slots_come_from_occupancy(engine):
    ec = {v.opp.opportunity_id: v.opp for v in engine.views(OpportunityType.EMPTY_COURT)}
    assert set(ec) == {EC_OTHER, EC_MAIN, EC_LATE}
    assert ec[EC_MAIN].typical_occupancy_pct == pytest.approx(20.0)
    assert all(o.typical_occupancy_pct < 40 for o in ec.values())


def test_private_match_and_short_lead_time_are_not_actionable(engine):
    codes = {n.ref: n.reason_code for n in engine.not_actionable}
    assert codes["M-PRIV-1900-C5"] == "PRIVATE_MATCH_EXCLUDED"
    assert codes["M-OPEN-1130-C4"] == "LEAD_TIME_TOO_SHORT"
    mp = {v.opp.opportunity_id for v in engine.views(OpportunityType.MISSING_PLAYER)}
    assert mp == {MP_MAIN, MP_SAFETY}


def test_consent_cap_conflict_and_dropout_exclusions(engine):
    view = get_view(engine, MP_MAIN)
    reasons = {e.player_id: e.reason_code for e in view.eligibility.exclusions}
    assert reasons["P011"] == "NO_OPT_IN"
    assert reasons["P012"] == "OPTED_OUT"
    assert reasons["P010"] == "CONTACT_CAP"
    assert reasons["P013"] == "SCHEDULE_CONFLICT"
    assert reasons["P004"] == "DROPPED_OUT"
    assert reasons["P001"] == "IN_MATCH"
    assert reasons["P014"] == "SKILL_OUT_OF_RANGE"
    shortlisted = {c.player_id for c in view.shortlist.candidates}
    assert not shortlisted & set(reasons)


def test_skill_tolerance_edge_is_inclusive(engine):
    view = get_view(engine, MP_MAIN)   # level 3.5 +/- 0.3
    assert "P033" in view.eligibility.eligible_ids  # level 3.2, exactly on the edge


def test_ranking_is_explainable_and_stable(engine):
    a = [c.player_id for c in get_view(engine, MP_MAIN).shortlist.candidates]
    b = [c.player_id for c in get_view(engine, MP_MAIN).shortlist.candidates]
    assert a == b and a[0] == "P005"
    top = get_view(engine, MP_MAIN).shortlist.candidates[0]
    assert set(top.features) == {"skill_fit", "time_fit", "social", "activity", "freshness"}
    assert top.explanation


def test_empty_court_group_is_level_compatible(engine):
    sl = get_view(engine, EC_MAIN).shortlist
    levels = [c.level for c in sl.candidates]
    assert len(levels) == 6
    assert max(levels) - min(levels) <= 2 * load_policy().eligibility.skill_tolerance + 1e-9
    assert sl.group_note


def test_zero_opportunities_need_no_action(tmp_path, monkeypatch):
    # With the provider down at startup there is no snapshot, so nothing is detected or ranked.
    from rally.engine import RallyEngine

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    e = RallyEngine(runtime_dir=tmp_path / "rt", llm_mode="offline")
    e.provider.set_outage(True)
    assert e.refresh() == "unavailable"
    assert e.views(OpportunityType.EMPTY_COURT) == []
    assert e.storage.count_messages() == 0
