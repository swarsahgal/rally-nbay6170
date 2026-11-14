# Guide for evaluators (human or AI agent)

The rubric-by-rubric map is in `START_HERE.md`; this page has the detail.

Rally is a Python prototype for NBAY 6170 (Team 3). It finds unsold padel courts and open match
seats, ranks players with fixed rules, waits for a manager's approval, reads player replies with
Claude into a strict schema, and counts a booking only when the (mock) booking provider verifies it.
`README.md` has the full design; this page is the short path to checking it.

A hosted copy runs at https://rally-nbay6170.streamlit.app (it may take about 30 seconds to wake up;
visitors share one demo database, so use "Reset demo data" in the sidebar first).

## Verify in about 2 minutes, no API key needed

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                                        # 57 pass, 10 live tests skipped without a key
python scripts/run_demo_sequences.py          # the three PRD demo sequences, printed as a trace
python scripts/golden/run_golden.py           # the 20 golden cases, offline rules parser
```

Without `ANTHROPIC_API_KEY`, a keyword-rules parser and fixed templates stand in for Claude; every
output says which parser it used. With a key in `.env` (copy `.env.example`), add `--live` to the
last two commands, and `pytest -m live` runs the 10 live parser cases. `streamlit run app.py` opens
the manager dashboard used in the demo video.

## Where to look

| Question | Where |
|---|---|
| Overall flow | `rally/engine.py` (wires the steps), `rally/coordinator.py` (state machine, sends, verification) |
| PRD tool → code map | `README.md`, section "PRD tools → code" |
| Hard safety invariants and their tests | `README.md`, section "Hard invariants and where they're enforced"; `tests/test_workflows.py` |
| LLM use (parsing and drafting, structured outputs) | `rally/llm.py`, `rally/schemas.py`; cases in `tests/test_llm_cases.py` |
| Club rules (single source, versioned) | `data/club_policy.yaml`, loaded by `rally/policy.py` |
| Real input data | `data/raw/occupancy_untitled-2.xlsx` → `scripts/build_fixtures.py` → `data/*.csv` |
| Append-only audit log | `rally/storage.py` (SQLite triggers block updates and deletes) |

## Evaluation evidence already in the repo

- **20 golden end-to-end cases:** spec in `fixtures/golden_cases.csv`; runner `scripts/golden/run_golden.py`.
  Each case runs on a fresh seeded engine through Rally's real functions and checks the case's own
  assertions. Results and a full trace per case:
  `outputs/golden/live-claude-haiku-5-5/` (live Claude run) and `outputs/golden/offline/`.
  Live run: 13 pass, 5 partial, 2 fail, no forbidden action in any case.
- **Independent judge:** `scripts/golden/judge.py` scored the 10 language-heavy cases with gpt-6.1-sol, a
  different model and provider from Rally's Haiku 5.5 (`judge_results.csv`, `agreement.json` in the live run
  folder): 7 of 10 match the team's score exactly, 9 of 10 within one point. The pre-fix run was also judged
  by gpt-6.1-sol and by Claude Sonnet 5.5, which agreed with each other on every case (files in
  `live-claude-haiku-5-5-before-f3-fix/`).
- **Report:** `outputs/golden/Rally_Golden_Evaluation_Report.docx` (and `.md`).
- **Model and cost comparison:** `scripts/tier_benchmark.py` (10 tasks × 3 runs × 4 models); prices
  in `data/model_pricing.yaml`.

## Known open issues (found by the golden cases, not yet fixed)

1. **Empty-court outreach does not move on to approved backups** (golden case G16). After two yes and
   two no, Rally escalates to the manager instead of inviting the remaining approved players.
   Safe, but a court can stay unsold.
2. ~~Live invite drafts were often cut off~~ **(fixed and retested Nov 14).** `claude-haiku-5-5` spent part
   of a 400-token output limit thinking, so 14 of 21 live drafts were truncated and Rally used its fixed
   template. The limit is now 2,000 (`rally/llm.py`, `MAX_OUTPUT_TOKENS`) with regression checks; on the
   rerun 21 of 21 drafts completed. Before/after evidence: `outputs/golden/live-claude-haiku-5-5-before-f3-fix/`
   vs `outputs/golden/live-claude-haiku-5-5/`, and `outputs/tier_benchmark_before_f3_fix_*` vs
   `outputs/tier_benchmark_*`.
3. **Empty-court invites don't state the condition** (G14): neither the template ("the first 4 to reply
   YES get the spots") nor the live Haiku draft says the game only happens once four players confirm and
   the booking is paid.
4. Minor, all safe: a send requested before approval raises an error instead of being logged (G08);
   no retry limit while the provider is down (G12); no message IDs for deduplicating repeated
   replies (G18); invites state a $30 price from `club_policy.yaml`, which the golden fixtures assume
   is not supplied (G10, G14).

A conditional yes ("I'm in if Daniela is playing") was once parsed as a firm yes by the offline
parser. That is fixed and covered by a regression test and golden case G09.

## What is simulated

Players, the club, messages (SMS/WhatsApp), the booking provider and payments are all simulated. The
only real input is the club's occupancy spreadsheet. No real person is contacted.
