"""Failure-analysis check: does Rally's live invite drafting fit in its output-token limit?

Rally's live drafter asks claude-haiku-5-5 for a structured draft with max_tokens=400 (rally/llm.py,
_structured). This script asks for the same two drafts (M17 and E21) at 400 and at 2000 tokens, records
the stop reason, output tokens, content block types and whether a valid draft came back, and saves the
result to outputs/golden/draft_token_check.json. It does not change Rally.

    python scripts/golden/check_draft_tokens.py      # needs ANTHROPIC_API_KEY
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import anthropic  # noqa: E402
import anthropic.resources.messages.messages as mm  # noqa: E402

from rally.engine import RallyEngine  # noqa: E402
from rally.llm import DRAFT_SYSTEM  # noqa: E402
from rally.messaging import invite_facts, validate_invite_text  # noqa: E402
from rally.schemas import DraftedInvite  # noqa: E402

MODEL = "claude-haiku-5-5"
captured: dict = {}
_orig = mm.parse_response


def _spy(*args, **kwargs):
    resp = kwargs.get("response") or args[0]
    captured.update(stop_reason=resp.stop_reason, output_tokens=resp.usage.output_tokens,
                    blocks=[b.type for b in resp.content],
                    text=next((b.text for b in resp.content if b.type == "text"), ""))
    return _orig(*args, **kwargs)


mm.parse_response = _spy


def main() -> None:
    e = RallyEngine(runtime_dir=Path(tempfile.mkdtemp()) / "rt", llm_mode="offline")
    e.refresh()
    client = anthropic.Anthropic()
    results = []
    for opp_id in ("MP-M-OPEN-1730-C2", "EC-20261110-1430-C1"):
        opp = e.opportunity(opp_id)
        _, sl = e.evaluate(opp)
        facts = invite_facts(opp, e.policy(), e.now(), sl.group_level)
        for max_tokens in (400, 2000):
            captured.clear()
            try:
                r = client.messages.parse(model=MODEL, max_tokens=max_tokens, system=DRAFT_SYSTEM,
                                          messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
                                          output_format=DraftedInvite)
                draft, error = r.parsed_output.message, ""
            except Exception as exc:  # noqa: BLE001
                draft, error = "", f"{type(exc).__name__}"
            problems = validate_invite_text(draft, facts) if draft else ["no draft returned"]
            row = {"opportunity": opp_id, "max_tokens": max_tokens, **captured, "draft": draft, "error": error,
                   "rally_validation_problems": problems, "usable_by_rally": not problems}
            results.append(row)
            print(f"{opp_id} max_tokens={max_tokens}: stop={row.get('stop_reason')} out={row.get('output_tokens')} "
                  f"blocks={row.get('blocks')} usable={row['usable_by_rally']}")
    out = ROOT / "outputs" / "golden" / "draft_token_check.json"
    out.write_text(json.dumps({"model": MODEL, "run_at": datetime.now().isoformat(timespec="seconds"),
                               "results": results}, indent=2, ensure_ascii=False))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
