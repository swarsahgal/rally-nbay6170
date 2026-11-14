import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rally.engine import RallyEngine  # noqa: E402
from rally.schemas import OpportunityType  # noqa: E402

MP_MAIN = "MP-M-OPEN-1730-C2"     # open match, 3/4, level 3.5, Tue 17:30
MP_SAFETY = "MP-M-OPEN-2030-C6"   # open match, 3/4, level 2.5, Tue 20:30
EC_MAIN = "EC-20261110-1430-C1"   # empty off-peak court, Tue 14:30 (typical occupancy 20%)
EC_OTHER = "EC-20261110-1300-C1"  # empty off-peak court, Tue 13:00 (27%)
EC_LATE = "EC-20261110-2200-C1"   # empty off-peak court, Tue 22:00 (5%)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    e = RallyEngine(runtime_dir=tmp_path / "runtime", llm_mode="offline")
    e.refresh()
    return e


def get_view(engine, opp_id):
    t = OpportunityType.MISSING_PLAYER if opp_id.startswith("MP-") else OpportunityType.EMPTY_COURT
    return next(v for v in engine.views(t) if v.opp.opportunity_id == opp_id)


def approve(engine, opp_id, n=3, max_sends=None, ids=None):
    view = get_view(engine, opp_id)
    template, source, _ = engine.draft(opp_id)
    ids = ids or [c.player_id for c in view.shortlist.candidates[:n]]
    window_end = view.opp.start - timedelta(minutes=engine.policy().timing.min_lead_time_minutes)
    return engine.approve(opp_id, ids, max_sends or len(ids), window_end,
                          engine.policy().consent.allowed_channels, template, source)


def state(engine, cid):
    return engine.storage.get_campaign(cid)["state"]


def invites(engine, cid):
    return {i["player_id"]: i for i in engine.storage.invites(cid)}
