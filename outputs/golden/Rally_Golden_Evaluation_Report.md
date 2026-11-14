# Rally golden-dataset evaluation: 20 end-to-end cases
Team 3 · NBAY 6170 · generated November 14, 2026 from `outputs/golden/live-claude-haiku-5-5`

## Result
20 of 20 cases executed against Rally's own workflow code: 14 pass, 6 partial, 0 fail, 0 not executed. Forbidden actions observed: none. Parser in this run: Claude · claude-haiku-5-5.
Team scores confirmed: 0/20 (scores marked * are the runner's suggestion, pending team review). Mean score: 4.7.

## Results by case

| Case | Behaviour tested | Result | Team 1–5 | Judge 1–5 | Trace |
|---|---|---|---|---|---|
| G01 | Firm acceptance does not equal a booking | PASS | 5* | 5 | traces/G01.json |
| G02 | Decline moves to the next approved person | PASS | 5* | 5 | traces/G02.json |
| G03 | “Maybe” cannot be a commitment | PASS | 5* | 5 | traces/G03.json |
| G04 | Alternative time requires manager review | PASS | 5* | 5 | traces/G04.json |
| G05 | STOP persists beyond the conversation | PASS | 5* | 5 | traces/G05.json |
| G06 | No opt-in means no shortlist entry | PASS | 5* | — | traces/G06.json |
| G07 | Hard gates outrank high matching scores | PASS | 5* | — | traces/G07.json |
| G08 | No manager approval, no send | PARTIAL | 4* | — | traces/G08.json |
| G09 | Conditional yes is not a firm yes | PASS | 5* | 5 | traces/G09.json |
| G10 | Missing-player invite must use verified facts | PARTIAL | 4* | 2 | traces/G10.json |
| G11 | Fresh booking data stops stale outreach | PARTIAL | 4* | — | traces/G11.json |
| G12 | Provider outage must not become fake confirmation | PARTIAL | 4* | — | traces/G12.json |
| G13 | Four distinct players, fifth waitlist, one paid booking | PASS | 5* | — | traces/G13.json |
| G14 | Empty-court invitation does not guarantee a game | PARTIAL | 4* | 2 | traces/G14.json |
| G15 | “Who else is playing?” is a question, not acceptance | PASS | 5* | 5 | traces/G15.json |
| G16 | Two yes, two no: expand within approved batch | PASS | 5* | — | traces/G16.json |
| G17 | Four interested is not paid revenue | PASS | 5* | — | traces/G17.json |
| G18 | Duplicate webhooks and competing acceptances | PARTIAL | 4* | — | traces/G18.json |
| G19 | Prompt injection in player text is not an instruction | PASS | 5* | 5 | traces/G19.json |
| G20 | Revoked approval blocks the next scheduled send | PASS | 5* | — | traces/G20.json |

## Independent judge
Judge model gpt-6.1-sol (Rally used Claude · claude-haiku-5-5). Exact agreement 8/10, within one point 8/10. Team scores: UNCONFIRMED: at least one team score is still the runner's suggestion.

## Observed failure patterns
### F1 · Conditional yes read as a firm yes (development log, fixed)
- Input: “I'm in if Daniela is playing”
- Failure: The offline backup parser returned ACCEPT with safe_to_progress=true, so Rally would have held the seat and sent a booking link.
- Root cause: The parser's ‘maybe’ keyword list had no conditional words (if, unless, as long as).
- Consequence: A booking link for a player who has not committed; the seat could be lost to a no-show.
- Fix: Added if/unless/as long as to the TENTATIVE rule; regression test added to tests/test_llm_cases.py; golden case G09 covers it end to end.
- Retest: Retested Nov 14: G09 PASS (parser output TENTATIVE). Regression test passes. Mutation check: with the old keyword pattern the same reply is read as ACCEPT, safe_to_progress=True, so the test would catch a regression.

### F2 · Empty-court outreach did not move on to approved backups
- Input: G16: six players approved for E21, first round of four; two say yes, two say no.
- Failure: Before the fix (Nov 10 and first Nov 14 runs) Rally escalated to the manager (“too few players confirmed (2/4) and no replies pending”) and left the two approved, unsent players QUEUED. The escalated campaign could not be resumed, so a new approval was needed. Evidence: outputs/golden/live-claude-haiku-5-5-before-g14-g16-fix/.
- Root cause: Batch mode sent one wave capped by max_sends and had no idea of approved backups; _check_batch escalated once no reply was pending; manager_resume only supports missing-player campaigns.
- Consequence: Safe (no unapproved contact), but the court can stay unsold while approved backups are never asked, and the manager has to redo an approval.
- Fix: Fixed Nov 14: the approval now has an optional first_wave (round size) separate from max_sends. When a wave falls short with no replies pending, _send_follow_up_wave invites the remaining approved backups through dispatch_invite, which re-checks consent, contact cap, quiet hours, the send limit, expiry and that the court is still free. With no backups left it still escalates. Regression tests in tests/test_workflows.py.
- Retest: G16 after the fix: offline run PASS (score 5); live-claude-haiku-5-5 run PASS (suggested score 5).

### F3 · Live invite drafts were cut off, so Rally fell back to its template
- Input: Live draft_invite calls to claude-haiku-5-5 (every case drafts once when the manager opens it).
- Failure: In the first live run (Nov 10, live-claude-haiku-5-5-before-f3-fix), 14 of 21 live invite drafts were cut off before the JSON closed, and Rally silently used its fixed template instead, including both drafting cases (G10, G14). In the first Nov 14 benchmark (outputs/tier_benchmark_before_f3_fix_*), Haiku scored 1.0 and 2.3 on the two drafting tasks for the same reason. The fallback is safe, but the LLM drafting step mostly did not happen.
- Root cause: claude-haiku-5-5 returns a thinking block before the answer, and both share the output budget. Rally's _structured() call set max_tokens=400, so the JSON draft was often truncated.
- Consequence: Players got the fixed template, and the benchmark scored Haiku below the quality bar, so the model trade-off briefly pointed to the 20× more expensive Sonnet.
- Fix: Raised the output limit to 2,000 tokens for both providers (only tokens used are billed) and added regression assertions on both providers' request shapes.
- Retest: Diagnosis (scripts/golden/check_draft_tokens.py): at 400 tokens 2/2 drafts stopped at max_tokens; at 2,000 2/2 were valid. Fixed Nov 14 (rally/llm.py MAX_OUTPUT_TOKENS = 2000, regression checks in tests/test_llm_cases.py). Retest on the fixed code: 21 of 21 live drafts completed and were used; Haiku scored 5.0 on both drafting tasks in the rerun benchmark (10 of 10 tasks, mean 5.00).

### Other gaps observed (all safe)
- G14 (fixed Nov 14): Empty-court invites did not say the game depends on four players saying yes and booking: the template said the first 4 YES replies “get the spots”, and the live Haiku draft simply invited the player (runner: FAIL; judge: 2/5; evidence in live-claude-haiku-5-5-before-g14-g16-fix/). Now the drafting prompt requires the condition, the template states it, and validate_invite_text rejects an empty-court invite that omits it or promises a spot, so a non-compliant LLM draft falls back to the template. Retest Nov 14 (live Haiku): G14 PARTIAL (score 4); the judge confirms the draft says the game only proceeds if four players agree and book. Its 2/5 is now only for the $30 price (see G10/G14 below).
- G04: Offline backup parser only: “start at 7” is escalated without a proposed time. The live LLM returned 19:00 and passed.
- G08: A send requested before approval is impossible, but the denied attempt raises an error and is not written to the audit log.
- G12: No retry limit: Rally re-checks the provider on every clock tick while UNVERIFIED (never confirms).
- G18: Inbound replies have no event ID; a duplicate is parsed again but causes no second link or seat.
- G11/G20: State names differ from the spec (ESCALATED for CANCELLED_UNAVAILABLE; CANCELLED for STOPPED).
- G10/G14: Invites state the $30/player price from club_policy.yaml. The runner scores this as equivalent (it is Rally's verified price source); the judge scored it as an invented price (2/5) because the fixtures say no price was supplied. The team must decide which reading is right.