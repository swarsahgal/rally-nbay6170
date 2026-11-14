# RALLY

## Product Requirements Document (PRD) v1.0

**Cornell Tech \| NBAY 6170 \| HW2 due November 10, 2026**  
**Status:** Product decisions confirmed; detailed operating thresholds provisional  
**Artifact owner:** Five-person capstone team (names and role assignments to be entered)

> **Product promise:** Rally helps padel clubs reduce unused court capacity by finding suitable players and coordinating bookings. The prototype demonstrates two workflows using simulated data; no real bookings, club revenue, or customer demand are claimed.

## 1. Decision register: confirmed by the team

| ID  | Decision                                                                | Consequence for HW2                                                |
|-----|-------------------------------------------------------------------------|--------------------------------------------------------------------|
| D01 | Support missing-player and empty-court workflows together               | Two opportunity types; one reusable coordination engine            |
| D02 | Detect opportunities automatically; get approval before outreach        | Detection runs without a user click; sending does not              |
| D03 | Club manager approves all invitations                                   | One manager approval dashboard; must respect private-match consent |
| D04 | Simulated data initially; connector for real sheets/Playtomic later     | Mock provider implements a stable interface                        |
| D05 | Python eligibility and ranking; LLM for conversation; ML later          | No fictional ML predictions presented as real                      |
| D06 | Sequential invitations for replacement; small batches for empty courts  | One engine, two outreach strategies                                |
| D07 | Simulated messaging first; adapter for later WhatsApp                   | No live phone messaging in HW2                                     |
| D08 | Routine follow-ups after approval; escalate exceptions; verify booking  | Explicit approvals, stop rules and provider-confirmed states       |
| D09 | Technical eval, illustrative economics, real pilot validation separated | No ROI claims from simulated outcomes                              |
| D10 | Python, Streamlit, SQLite, Pydantic, pytest, LLM API, GitHub            | Minimal code-first stack; no MCP/A2A/RAG needed for MVP            |

These are decisions, not a promise that every implementation detail is settled. The team must approve the provisional thresholds in Section 8.

## 2. Problem, customer and scope

**Primary paying customer (hypothesis):** Club general manager or operations lead. **Other users:** club-approved match organizer and opted-in player. **Target club (hypothesis):** a small or mid-sized padel venue using Playtomic or a similar booking system. Pilot subscription idea of \$199-\$299/month is an *untested price hypothesis*, not validated willingness to pay.

**Problem A: Missing player.** A club-managed open match is underfilled or a participant drops out. Organizer time is wasted, and the game may be cancelled. A successful replacement can protect the match, but may not add incremental club revenue if the booking would remain paid.

**Problem B: Empty court.** A bookable off-peak court remains unsold. Rally tries to form a viable group of four willing players. A completed new booking could generate incremental contribution, but only relative to the club's normal fill rate and costs.

**In scope for HW2:** two simulated triggers, club-policy knowledge, consent/eligibility rules, candidate rankings, approval dashboard, simulated player replies with a real LLM parser, messages, timeouts, booking-link handoff, mocked verification, event logs, and 20 golden evaluations.

**Out of scope:** actual payments, automatic Playtomic edits, real WhatsApp sends, live customer onboarding, dynamic pricing, trained acceptance model, contextual bandits, cross-club marketplace, full-scale deployment.

## 3. Main user journeys and acceptance criteria

### A. Missing-player match

1.  Mock booking adapter reports a club-managed open match with one missing player.
2.  Rally checks current match status, lead time, club policy and player opt-ins.
3.  Rally returns an explainable candidate shortlist in ranked order.
4.  Manager approves a *specific* list, outreach window and invitation cap.
5.  Rally invites the top approved candidate; on decline/timeout, advances to the next approved candidate.
6.  LLM parses reply into a strict schema; Python makes the resulting state transition.
7.  On clear acceptance, Rally provides the approved booking link; it waits for provider verification.
8.  Only a verified booking produces `CONFIRMED`; all steps are logged.

**Pass condition:** The entire journey works with mock data; no invitation occurs before approval; stale/ambiguous input does not produce false confirmation.

### B. Empty off-peak court

1.  Mock court schedule lists opening hours and saleable 90-minute slots; booking snapshot identifies a still-empty slot.
2.  Rally checks that the slot is available and sufficiently far in the future.
3.  Rally finds a compatible group and presents a manager-approved first batch (provisional default: at most six candidates).
4.  Players receive approved invitations; Rally tracks each response and avoids excessive outreach.
5.  Rally collects sufficient firm interest, rechecks court availability, and hands players to the approved booking process.
6.  A booking is complete only when the mock booking provider reports a valid booking with the required players/payment state defined for the demo.
7.  If the court is taken, responses conflict, or too few players confirm, Rally stops or escalates.

**Pass condition:** The group coordination reaches a verified booking on the positive fixture and correctly fails on partial/stale/unverified fixtures. Four positive messages alone must not count as a sale.

## 4. System boundaries and workflow states

**Core sequence:** `Opportunity → Eligibility → Ranking → Manager approval → Outreach → Reply interpretation → Booking verification → Outcome log`.

**Approvals:** Manager authorizes one outreach campaign: opportunity ID, approved candidate IDs, maximum sends, permitted channels, allowed period and expiration. A new candidate outside that approval needs fresh approval. A manager stop cancels future sends.

**State examples:** `DETECTED`, `AWAITING_APPROVAL`, `APPROVED`, `INVITING`, `AWAITING_REPLY`, `PARTIAL_COMMITMENT`, `AWAITING_BOOKING_VERIFICATION`, `CONFIRMED`, `EXPIRED`, `CANCELLED`, `ESCALATED`, `UNVERIFIED`.

**Hard invariants:**

- No message to a player without current valid opt-in and approved campaign scope.
- Never edit price, time, booking or payment data based solely on LLM text.
- No `CONFIRMED` state without a booking provider verification event.
- Never allocate one seat to two candidates; avoid duplicate sends using an event/invite ID.
- Treat player text and external data as untrusted input, not executable instructions.
- Under an unavailable/stale provider status, stop or escalate rather than guess.
- Only intervene in club-managed or explicitly authorized match types; private matches are excluded by default.

## 5. Tools: simple contracts, not one large agent

| Tool / module            | Input                                    | Output                                           | Who decides / failure behavior                   |
|--------------------------|------------------------------------------|--------------------------------------------------|--------------------------------------------------|
| `get_snapshot()`         | club ID and date window                  | current courts, matches, roster                  | Code; unavailable→stop/escalate                  |
| `detect_opportunities()` | snapshot + club schedule + policy        | list of missing-seat / unsold-slot opportunities | Code; none→no action                             |
| `filter_eligible()`      | opportunity + roster + policy            | eligible player IDs, exclusion reasons           | Code; zero→escalate                              |
| `rank_candidates()`      | eligible players + preferences           | shortlist + transparent feature scores           | Code; tie→stable tie break                       |
| `approve_campaign()`     | opportunity + shortlist + manager action | approved IDs, limits, timestamp                  | Human; no approval→no sends                      |
| `dispatch_invite()`      | approved campaign + player + message     | simulated message ID                             | Code; dedupe + limit checks                      |
| `parse_reply()`          | one reply + minimal invitation context   | typed reply intent + fields                      | LLM with Pydantic validation; invalid→unclear    |
| `advance_state()`        | event + prior state + policy             | next state + pending action                      | Code; invalid transition→error log               |
| `verify_booking()`       | booking/match ID + provider snapshot     | confirmed / pending / unavailable                | Code; unavailable≠confirmed                      |
| `log_event()`            | structured event                         | SQLite append-only record                        | Code; failed audit write blocks high-risk action |

A tool's permission is different from the LLM's ability to *suggest* that tool. The LLM cannot grant itself new permissions.

## 6. Data, knowledge and memory

**Simulated datasets:** `clubs`, `courts`, `court_hours`, `slots`, `matches`, `players`, `player_preferences`, `campaigns`, `invites`, `messages`, `booking_events`. Fixtures contain only fictional participants.

**One required wired-in knowledge source:** versioned `club_policy.yaml`, loaded when an opportunity is evaluated and when a message is about to be sent. It defines eligibility, skill bands, opt-in, contact caps, quiet hours, approval rules, booking constraints and escalation behavior. Show its policy version in the UI and trace. **This is context/knowledge**, not a RAG system. No need for embeddings or a vector database for one small rules file.

**SQLite is durable storage:** retain campaigns, per-invite states, approval scope and events; do not rely on Streamlit Session State for persistent truth. Streamlit reruns on interaction and session state is scoped to the browser session (official docs: <https://docs.streamlit.io/develop/concepts/architecture/session-state>).

**LLM input:** only the specific invite/reply, exact match details, allowable template facts and relevant policy excerpt. Never pass the full club database or entire log to the model.

**LLM reply output schema (example):** `intent` from `ACCEPT / DECLINE / ALTERNATIVE_TIME / TENTATIVE / OPT_OUT / QUESTION / UNKNOWN`; optional `alternative_time`; `requires_manager`; `safe_to_progress`. The code must validate and interpret these values, not trust the model's explanation. Claude's API supports schema-constrained JSON structured outputs (<https://platform.claude.com/docs/en/build-with-claude/structured-outputs>).

## 7. UI and demo storyboard

**Manager dashboard:** two tabs, `Missing players` and `Empty courts`; each shows opportunity, why detected, eligible shortlist and a controlled `Approve this outreach` action. A separate `Activity and traces` tab displays every state change, tool output and failure.

**Simulated player chat:** select one fictional candidate, see the outgoing approved message, type or choose a reply, see the LLM's structured interpretation and next state. For a full demo, advance simulated booking status through a manager-controlled fixture button such as `Simulate provider confirmation`. Clearly label all simulated events.

**Three live demo sequences:** (1) missing fourth player ends in provider-confirmed replacement; (2) empty court ends in verified group booking; (3) safety failure such as STOP, ambiguous reply, stale court or attempted bypass of approval stops safely.

## 8. Provisional product rules to settle before coding

These are sample settings in `Rally_club_policy_example.yaml`, **not yet approved by the team or a club**. The PRD does not silently treat them as facts.

| Question               | Provisional demonstration setting | Why it needs confirmation                     |
|------------------------|-----------------------------------|-----------------------------------------------|
| Contact consent        | Explicit per-player opt-in        | Source, wording and channel scope             |
| Eligible match type    | Club-managed open matches only    | Private organizers may not authorize outreach |
| Quiet hours            | 9:00 AM–9:00 PM local             | Club preference and messaging requirements    |
| Invitation cap         | 2 per player per 7 days           | Avoid fatigue; test realistic cadence         |
| Minimum lead time      | 45 minutes                        | Is there time to complete booking?            |
| Skill tolerance        | ±0.3 around match level           | Ranking vs hard exclusion must be agreed      |
| Reply timeout          | 20 minutes (configurable)         | Depends on lead time and player behavior      |
| Empty-court first wave | Up to 6 pre-approved candidates   | Four seats and over-acceptance handling       |
| Booking confirmation   | Mock provider verified status     | Real read-only provider limits future actions |

**Additional unresolved requirements:** How players declare availability; how an empty court's bookable slots are determined from opening hours; how groups are formed for four-person compatibility (not just four independent high scores); how timezones are handled; how payment and cancellation rules affect ROI; how organizer consent is granted for private matches; how real data access is authorized; how a manager can retract campaign approval; and who can view stored player behavior.

## 9. Evaluation plan: HW2 contract

**Golden dataset:** 20 manually specified cases (10 missing-player, 8 empty-court, 2 cross-workflow). The companion CSV contains inputs, expected actions, forbidden actions, severity and the selected 10 language-heavy cases for cross-model judging and model-tier comparisons. If a case passes in the simulator, that is **simulated correctness on that case**, not production accuracy.

**Every case:** team runs it, saves actual output and trace, assigns a 1–5 quality score and records what went wrong. One possible consistent rubric:

- **5:** correct decision, valid state, policy followed, clear message, no unsupported facts.
- **4:** correct/safe outcome with minor wording or recoverable usability issue.
- **3:** safe but incomplete; human intervention needed where the product should handle it.
- **2:** major incorrect action or missed outcome, but no unsafe contact/false claim.
- **1:** safety violation, missing approval/consent, double booking, or false confirmation; also use for complete failure.

**Judge 10 cases with a different model:** give the same input, expected behavior, actual trace and the 1–5 rubric; compare its score with the team score. Report exact-match count/10 and within-one-point count/10 plus any meaningful disagreements. Human team scoring remains the reference, not unquestioned ground truth.

**Two model tiers on the same 10 cases:** run identical inputs and schema with small/fast and stronger models; save task-by-task output quality, total latency, input/output tokens and cost. Report median latency and cost per 100 equivalent runs. Formula: `avg_cost_per_run = sum(input_tokens × input_rate + output_tokens × output_rate) / N`; `cost_per_100 = avg_cost_per_run × 100`. Record the actual pricing page/date when computed; no invented model prices. The 10 model-heavy cases are flagged `yes` in the CSV.

**Other strict tests:** policy-gate success, invalid state transitions, opt-outs, duplicate events, booking verification and empty-court availability. These use code assertions, not LLM grading. Report 0 critical violations as a *target*, not an achieved result.

**Required failure analysis:** identify the top three observed failure patterns and fixes based on traces, e.g. tentative reply misread as yes, stale booking data, approval scope exceeded. Do not predeclare these as the actual findings before testing.

**AI-use log:** record tools/models used to build and test, assistance accepted/rejected, human checks and generated code changes.

## 10. Business economics and future evidence

**Two separate business scorecards:**

- **Empty court:** new paid booking / eligible unsold slot; incremental gross revenue and contribution after discounts, booking fees and variable costs; compare against no-intervention group.
- **Missing player:** replacement completed, time to replacement, cancellation avoided, booking value retained *only when demonstrably at risk*, and organizer time saved. An already-paid match may add \$0 incremental club revenue.

**Illustrative example only:** at \$80 per hour for 90 minutes, gross court revenue per newly booked slot is \$120. Five truly incremental bookings per month would be \$600 gross, **not** \$600 profit and **not** automatically \$600 created by Rally. `\$199-\$299/month` subscription and `+10 percentage-point fill-rate lift` are pilot hypotheses, not established outcomes.

**Future field validation:** Randomize comparable eligible opportunities to club's normal process vs Rally; stratify by workflow and time of day. Log actual paid bookings, net contribution, cancellations, message fatigue and opt-outs. Randomization should avoid interference between opportunities that compete for the same members.

## 11. Risks and mitigations

| Risk                                     | Why it matters                                        | Mitigation / proof before launch                                    |
|------------------------------------------|-------------------------------------------------------|---------------------------------------------------------------------|
| No usable player opt-ins/availability    | Ranking looks good but cannot contact anyone          | Consent-first roster and preferences; design-partner validation     |
| No incremental revenue from replacements | Claimed ROI misleading                                | Separate cancellations saved from incremental paid slots            |
| Booking status stale / external races    | Wrong seat or court offered                           | Fresh check before send/confirmation; idempotency; failure state    |
| Privacy or unauthorized outreach         | Trust and legal/contract exposure                     | Club-managed scope, explicit consent, channel caps, opt-out gates   |
| Prompt injection / hallucination         | Model could misinterpret a reply or invent terms      | Minimal context, strict schema, code-enforced permissions, tests    |
| Oversubscription in four-player group    | People promised unavailable slots                     | Treat interest as provisional; provider verification and waitlist   |
| Platform dependence                      | Playtomic API read-only, tier-based access, data gaps | Mock connector; no write assumption; request club access separately |
| No historical outcomes                   | ML uplift/acceptance estimates not valid              | Rules first; no synthetic-ML performance claims                     |

Official Playtomic source (updated October 14, 2026): <https://helpmanager.playtomic.com/hc/en-gb/articles/38836515997073-Playtomic-API-Complete-Guide> . Its API is read-only; bookings and some player details are exposed, while dedicated player-data endpoints are a planned addition. Future live integration must follow club authorization and platform terms.

## 12. Build plan for HW2 (November 5–10)

| Date       | Deliverable                                                                                                      | Definition of done                                                     |
|------------|------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| Thu Nov 5  | Confirm five-person ownership; manual walkthrough of 1 missing-seat + 1 empty-court case; freeze policy defaults | Team agrees exact happy paths and hard invariants; GitHub repo created |
| Fri Nov 6  | Fictional data fixtures; loaded policy source; `detect/filter/rank`; SQLite schema                               | Both opportunity types detected; no-approval/no-consent tests pass     |
| Sat Nov 7  | Approval and messaging simulator; state transitions; mock booking verification                                   | Both paths run end to end **without** LLM; event traces persist        |
| Sun Nov 8  | LLM draft/parse with Pydantic schema, cost/token logging                                                         | At least 10 language-heavy cases run; ambiguity/STOP handled safely    |
| Mon Nov 9  | All 20 cases scored by team; 10 independent model-judged; two-tier comparison; top three failures; economics     | Evidence tables and reproducible logs in repo; no invented metrics     |
| Tue Nov 10 | Final bug fixes, demo recording/rehearsal, HW2 submission                                                        | App starts from README; all artifacts included; AI-use log complete    |

If behind schedule: do **not** abandon either workflow. Keep both using the same coordination engine, but reduce UI polish and external services. A fake provider + visible actual LLM calls + credible evaluation is higher priority than real messaging.

## 13. Suggested five-person ownership

| Role (assign a name)           | Owns                                                                                | Deliverable                                    |
|--------------------------------|-------------------------------------------------------------------------------------|------------------------------------------------|
| Product / workflow lead        | scope, manual scenarios, PRD, acceptance criteria, manager actions                  | final approved requirements + demo narrative   |
| Data / integration lead        | mock fixtures, schedule logic, club-policy loader, connector interface              | stable synthetic dataset + policy version      |
| Backend / technical integrator | ranking, eligibility, SQLite state engine, idempotency, GitHub merges               | reliable shared workflow and tests             |
| LLM / interface lead           | structured reply parser, messaging adapter, Streamlit screens                       | simulated conversation and manager dashboard   |
| Evaluation / economics lead    | 20 golden cases, scoring rubric, second-model comparison, cost/latency, ROI caveats | reproducible evaluation report + pricing table |

**Suggested role for you:** evaluation/economics lead with ownership of the decision and failure rubric, working closely with the backend lead. This uses your analytics experience and directly demonstrates the lesson from Brightdesk. Names/assignments are *suggestions*, not confirmed team decisions.

## 14. Beginner's map of the stack

- **Python:** rules and workflow code; the central program.
- **Streamlit:** browser dashboard written in Python; not the source of truth for bookings.
- **SQLite:** small database file that remembers approvals, invites, states and traces between clicks.
- **Pydantic:** defines and checks the permitted fields from LLM replies, such as `intent` and `alternative_time`.
- **pytest:** executes automated checks and reports failed rules.
- **LLM API:** a paid model call for drafting messages and turning replies into typed events; keep API key in `.env`, never GitHub.
- **GitHub:** one shared codebase, version history and pull requests so five people don't overwrite each other.
- **Claude Code / Cursor (optional):** coding assistants; they help write code but the team reviews and tests it.

**Proposed repo structure:**

    rally/
      app.py                        # Streamlit entry point
      README.md                     # how to run/demo
      .env.example                  # no actual secrets
      requirements.txt
      data/
        players.csv
        courts.csv
        slots.csv
        matches.csv
        club_policy.yaml            # knowledge source
      fixtures/
        golden_cases.csv
        scenarios/                  # one JSON fixture per case
      rally/
        providers.py                # mock booking + future adapter boundary
        eligibility.py
        ranking.py
        approvals.py
        coordinator.py              # state transitions
        messaging.py                # simulation adapter + LLM calls
        storage.py                  # SQLite persistence
        schemas.py                  # Pydantic input/output contracts
        metrics.py
      tests/
        test_policy.py
        test_workflows.py
        test_llm_cases.py
      outputs/
        eval_results.csv
        model_tier_comparison.csv
        traces.jsonl
      docs/
        PRD.md
        ai_use_log.md
        demo_script.md

**First coding request to an assistant:** "Build only `schemas.py`, `providers.py` and a mock fixture so one missing-player opportunity can be loaded and printed. Do not add UI, LLM, or network calls. Include two pytest checks: opted-out users are excluded and no campaign sends without approval. Explain every file before adding more." Continue one small component at a time.

## 15. Acceptance gate before submission

- [ ] Two working, reproducible workflows using fictional data.
- [ ] Versioned club-policy knowledge source is actually loaded and used.
- [ ] Manager approval gates outbound messages.
- [ ] LLM calls actually happen for both reply parsing and message drafting (unless a case intentionally needs no LLM).
- [ ] Typed response validation, timeouts, opt-out handling and booking verification shown.
- [ ] Every decision/action logged with policy version and tool outcome.
- [ ] 20 golden cases, expected outputs and 1–5 team scores.
- [ ] 10 cases independently judged by another model with agreement analysis.
- [ ] Same 10 tasks run on two model tiers; cost, latency and quality reported.
- [ ] Three *observed* failure patterns and fixes documented.
- [ ] Economic scenarios labeled assumptions; no synthetic revenue presented as actual.
- [ ] Repo README, reproducible demo and AI-use log included.
