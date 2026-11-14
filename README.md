# Rally: HW2 demo (NBAY 6170)

**Live app:** https://rally-nbay6170.streamlit.app · **Evaluator guide:** [EVALUATION.md](EVALUATION.md)

Rally helps a padel club fill unused court capacity. It **detects** two kinds of opportunity
automatically, **ranks** suitable players with transparent rules, waits for a **manager to approve**
a specific outreach, **coordinates** replies (Claude reads them into a strict schema; Python decides
what happens), and counts a booking only when the **booking provider verifies** it.

Everything is simulated: fictional players, a mock booking provider, simulated SMS/WhatsApp, simulated
payments. The one real input is the club's **occupancy spreadsheet**, which drives opening hours,
the slot grid, which slots count as off-peak, and where empty courts appear.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # paste ANTHROPIC_API_KEY to use Claude (required for the graded run)
streamlit run app.py
```

Without a key the app still runs: simple keyword rules stand in for the LLM, and the UI says so
everywhere. The PRD acceptance gate needs real LLM calls, so record the demo with a key.

Other entry points:

```bash
pytest                                   # 57 invariant/workflow/parser tests (offline)
pytest -m live                           # 10 live Claude parser cases (needs ANTHROPIC_API_KEY)
python scripts/run_demo_sequences.py     # the three PRD demo sequences, headless, printed as a trace
python scripts/run_demo_sequences.py --live
python scripts/build_fixtures.py         # rebuild data/ from the occupancy spreadsheet
python scripts/tier_benchmark.py         # unit economics: 10 tasks x Claude + OpenAI tiers (needs the keys)
python scripts/tier_benchmark.py --estimate   # projected cost only, no API calls
```

`docs/demo_script.md` is a click-by-click script for the live demo.

## What the demo shows (PRD section 7)

| Tab | What's there |
|---|---|
| **Missing players** | Club-managed open matches with an open seat. Why each was detected, a ranked shortlist with per-feature scores, who was excluded and why, and the approval form (players, max invitations, window, channels, exact message). After approval: invite status, manager actions, simulated provider events, safety drills. |
| **Empty courts** | Off-peak slots with an empty court. Rally picks a level-compatible group (not four independent high scores) and sends a first wave of up to 6. |
| **Player chat** | Reply as any invited player (quick replies or free text). Shows Claude's structured interpretation (`intent`, `alternative_time`, `requires_manager`, `safe_to_progress`), the Python decision, and the state change. |
| **Activity and traces** | Every state change, tool output and failure, with policy version. Append-only (SQLite triggers). JSONL/CSV export, plus a token/latency log of every LLM call. |
| **Club data** | Occupancy heatmap from the spreadsheet, the derived court sheet, the policy file, and illustrative economics (labelled as assumptions). |

Sidebar: simulated clock (+5/+20/+60 min, jump), parser and model choice, provider outage toggle,
reset.

## How the occupancy spreadsheet is used

`data/raw/occupancy_untitled-2.xlsx` has 168 rows: hour (00-23) × ISO weekday (1 = Mon) × a value.
The third column is headed "Hour" but holds values from 0 to 89, so it is read as **typical court
occupancy %**. `scripts/build_fixtures.py` checks it (168 rows, no duplicates, 0-100) and writes:

- `occupancy_profile.csv`: the cleaned profile.
- `court_hours.csv`: an hour is open if typical occupancy ≥ 0.5%, which gives 07:00–24:00 every day.
- `slots.csv`: 90-minute slots on a fixed grid from opening (07:00, 08:30 … 22:00): 11 per court per
  day. Each slot gets a minute-weighted typical occupancy. It is **off-peak** if below 40%.
- `matches.csv`: the mock booking sheet for the demo days. Each slot is booked to its typical
  occupancy (6 courts × occupancy, rounded), plus five hand-written demo matches.

On the demo day (Tuesday), 13:00 (27%), 14:30 (20%) and 22:00 (5%) are off-peak, so those are the
only empty-court opportunities. Peak slots such as 19:00 (81%) are left to normal demand.

## PRD tools → code

| PRD tool | Module | Failure behaviour |
|---|---|---|
| `get_snapshot()` | `rally/providers.py` (`MockBookingProvider`) | outage → `ProviderUnavailable` → stop/escalate |
| `detect_opportunities()` | `rally/detection.py` | private match / short lead time → logged as not actionable |
| `filter_eligible()` | `rally/eligibility.py` | every exclusion has a reason code; zero → escalate |
| `rank_candidates()` | `rally/ranking.py` | stable tie-break (score, level distance, id) |
| `approve_campaign()` | `rally/approvals.py` | no approval → no sends; scope checked before every send |
| `dispatch_invite()` | `rally/coordinator.py` | dedupe, cap, consent, quiet hours, window, fresh provider check |
| `parse_reply()` | `rally/llm.py` | Claude `messages.parse` + Pydantic; any failure → `UNKNOWN` |
| `advance_state()` | `rally/coordinator.py` | invalid transition → rejected and logged |
| `verify_booking()` | `rally/coordinator.py` | unavailable → `UNVERIFIED`, never `CONFIRMED` |
| `log_event()` | `rally/storage.py` | append-only; a failed audit write rolls back the send |

`rally/engine.py` wires these together for the UI, tests and scripts. `data/club_policy.yaml` is the
single knowledge source: it is loaded when an opportunity is evaluated and again right before every
send, and its version is stamped on every event.

## Hard invariants and where they're enforced

| Invariant (PRD section 4) | Enforced by | Test |
|---|---|---|
| No message without opt-in and approved scope | `dispatch_invite` gates + `scope_violation` | `test_unapproved_player_is_blocked_and_logged`, `test_consent_is_rechecked_at_send_time` |
| No price/time/booking/payment change from LLM text | No such tool exists; the LLM returns only a typed label | `test_code_does_not_trust_a_flagged_accept` |
| No `CONFIRMED` without provider verification | state machine: only `verify_booking` reaches `CONFIRMED` | `test_missing_player_needs_provider_verification`, `test_four_yes_replies_are_not_a_sale`, `test_invalid_transition_is_rejected_and_logged` |
| One seat never goes to two candidates; no duplicate sends | SQLite unique indexes on seat and on invite send | `test_no_seat_is_allocated_twice`, `test_duplicate_send_is_suppressed` |
| Player text is untrusted input | delimited prompt, schema output, code-side guards | prompt-injection case in `test_llm_cases.py` |
| Stale/unavailable provider → stop or escalate | fresh checks before send, at 4th seat, on every tick | `test_stale_court_stops_outreach`, `test_provider_outage_means_unverified_not_confirmed` |
| Club-managed matches only; private excluded | `eligibility.match_types_allowed` | `test_private_match_and_short_lead_time_are_not_actionable` |

## Choices made where the PRD leaves things open

These are demo settings, written down so the team can confirm or change them (PRD section 8):

- **Slot grid:** fixed 90-minute grid from opening time; off-peak means below 40% typical occupancy.
- **Availability:** players declare coarse time bands at opt-in (e.g. weekday evenings, 5–9pm).
  Ranking rewards a match, but doesn't require one.
- **Skill:** hard filter at ±0.3 around the match level (`skill_mode: hard_filter`; `rank_only` is
  also supported).
- **Group formation:** for empty courts, Rally picks the ±0.3 level band whose best first wave scores
  highest, then ranks inside it. Social history (who has played together) is a feature.
- **Lead time:** the PRD's single 45-minute value is used for both workflows.
- **Price in messages:** $80/hour × 1.5 h ÷ 4 = $30/player, the PRD's illustrative rate.
- **Players with an unanswered invite** elsewhere are excluded, so two campaigns don't compete
  for the same person.
- **Timezone:** America/New_York for everything; the demo clock starts Tue Nov 10, 10:50 AM.
- **Ambiguous replies:** in sequential outreach, an unclear reply pauses the campaign (`ESCALATED`)
  until the manager skips, or the player later sends a clear yes or no.

## LLM details

- Model default: `claude-haiku-5-5` (fast tier). Switch in the sidebar or set `RALLY_MODEL` for the
  live tests; compare with `claude-sonnet-5-5` for the two-tier run.
- Parsing uses structured outputs through `client.messages.parse(..., output_format=ReplyInterpretation)`.
  The model sees only the invitation it is answering, the reply, and a policy excerpt; never the
  database or the log.
- Drafting writes one invite template per opportunity. Code checks it (placeholder, time, court, opt-out
  line, no links, no other price) and falls back to a fixed template if any check fails. The manager
  approves the exact text.
- An exact `STOP` is handled by a fixed rule, with no LLM call.
- Every call is logged with latency and tokens (Activity tab → CSV). Cost is left to the evaluation
  step, using the real pricing page on the day it is computed.

## Unit economics (two model tiers)

`scripts/tier_benchmark.py` runs the same 10 tasks (8 reply interpretations incl. prompt injection and a
conditional yes, plus 2 invite drafts) through Rally's real LLM code on `claude-haiku-5-5`,
`claude-sonnet-5-5`, `gpt-6-luna` and `gpt-6.1-sol` (models without a key in `.env` are skipped), 3 runs each. It records latency, tokens and cost (prices in `data/model_pricing.yaml`,
from the official pricing page) and scores quality 1–5 automatically, flagging any unsafe output
(`safe_to_progress=true` when it should not be). Results: `outputs/tier_benchmark_summary.md` + a CSV of
every call. `--mock` tests the pipeline with no key; its numbers are not results.

## Evaluation

`EVALUATION.md` is the short guide for evaluators: how to verify the code without an API key, where
each part lives, the golden-case results (`fixtures/golden_cases.csv`, `scripts/golden/`,
`outputs/golden/`) and the known open issues.

## Repo layout

```
app.py                     Streamlit UI (manager dashboard + simulated player chat)
data/                      policy, fictional players, occupancy profile, generated slots/matches
  raw/                     the original occupancy spreadsheet
rally/                     engine: schemas, policy, providers, detection, eligibility, ranking,
                           approvals, coordinator, messaging, llm, storage, engine
scripts/                   build_fixtures.py, run_demo_sequences.py, tier_benchmark.py
  golden/                  20 golden cases: run_golden.py, judge.py, build_report.py
fixtures/                  golden_cases.csv (the 20-case evaluation spec)
outputs/golden/            golden-case results, traces, judge scores, evaluation report
tests/                     pytest: policy/detection, workflows/invariants, reply parsing
docs/                      PRD.md, demo_script.md, ai_use_log.md
EVALUATION.md              short guide for evaluators
runtime/                   SQLite files (created at run time, git-ignored)
```
