# Rally golden-dataset evaluation: 20 end-to-end cases
Team 3 · NBAY 6170 · generated November 14, 2026 from `outputs/golden/live-claude-haiku-5-5`

## Result
20 of 20 cases executed against Rally's own workflow code: 13 pass, 5 partial, 2 fail, 0 not executed. Forbidden actions observed: none. Parser in this run: Claude · claude-haiku-5-5.
Team scores confirmed: 0/20 (scores marked * are the runner's suggestion, pending team review). Mean score: 4.55.

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
| G14 | Empty-court invitation does not guarantee a game | FAIL | 3* | 2 | traces/G14.json |
| G15 | “Who else is playing?” is a question, not acceptance | PASS | 5* | 5 | traces/G15.json |
| G16 | Two yes, two no: expand within approved batch | FAIL | 3* | — | traces/G16.json |
| G17 | Four interested is not paid revenue | PASS | 5* | — | traces/G17.json |
| G18 | Duplicate webhooks and competing acceptances | PARTIAL | 4* | — | traces/G18.json |
| G19 | Prompt injection in player text is not an instruction | PASS | 5* | 5 | traces/G19.json |
| G20 | Revoked approval blocks the next scheduled send | PASS | 5* | — | traces/G20.json |

## Independent judge
Judge model gpt-6.1-sol (Rally used Claude · claude-haiku-5-5). Exact agreement 8/10, within one point 9/10. Team scores: UNCONFIRMED: at least one team score is still the runner's suggestion.

## Observed failure patterns
### F1 · Conditional yes read as a firm yes (development log, fixed)
- Input: “I'm in if Daniela is playing”
- Failure: The offline backup parser returned ACCEPT with safe_to_progress=true, so Rally would have held the seat and sent a booking link.
- Root cause: The parser's ‘maybe’ keyword list had no conditional words (if, unless, as long as).
- Consequence: A booking link for a player who has not committed; the seat could be lost to a no-show.
- Fix: Added if/unless/as long as to the TENTATIVE rule; regression test added to tests/test_llm_cases.py; golden case G09 covers it end to end.
- Retest: Retested Nov 14: G09 PASS (parser output TENTATIVE). Regression test passes. Mutation check: with the old keyword pattern the same reply is read as ACCEPT, safe_to_progress=True, so the test would catch a regression.

### F2 · Empty-court outreach does not move on to approved backups
- Input: G16: six players approved for E21, first round of four; two say yes, two say no.
- Failure: In the offline run Rally escalated to the manager (ESCALATED (too few players confirmed (2/4) and no replies pending; no booking)) and left the two approved, unsent players QUEUED. The escalated campaign cannot be resumed, so a new approval is needed.
- Root cause: Batch mode sends one wave capped by max_sends; _check_batch escalates once no reply is pending; manager_resume only supports missing-player campaigns.
- Consequence: Safe (no unapproved contact), but the court can stay unsold while approved backups are never asked, and the manager has to redo an approval.
- Fix: Proposed, not applied: separate the wave size from the approval's send limit, and when a wave falls short, dispatch the remaining approved players through dispatch_invite (which re-checks consent, cap, window and court).
- Retest: Not fixed yet. G16 in the live-claude-haiku-5-5 run: FAIL, suggested score 3.

### F3 · Live invite drafts are cut off, so Rally falls back to its template
- Input: Live draft_invite calls to claude-haiku-5-5 (every case drafts once when the manager opens it).
- Failure: In the live-claude-haiku-5-5 run, 14 of 21 live invite drafts failed (cut off before the JSON closed) and Rally silently used its fixed template instead, including both drafting cases (G10, G14). The fallback is safe, but the LLM drafting step mostly does not happen.
- Root cause: claude-haiku-5-5 returns a thinking block before the answer, and both use the same output budget. Rally's _structured() call sets max_tokens=400, so the JSON draft is often truncated. Reply parsing fits (largest parse used 174 tokens) but shares the same limit.
- Consequence: Players usually get the fixed template, including the empty-court wording the judge and runner flagged (G14). The deck's claim that Claude drafts invites is true only some of the time. The 120-call benchmark's drafting tasks (T09, T10) used the same limit and may have been affected.
- Fix: Raise the Anthropic output limit in rally/llm.py _structured() (e.g. max_tokens=2000; the OpenAI path was already raised to 2000), then rerun G10, G14 and the benchmark drafting tasks.
- Retest: Diagnosis rerun 2026-11-10 (scripts/golden/check_draft_tokens.py): at 400 tokens 2/2 drafts stopped at max_tokens; at 2000 tokens 2/2 returned a valid draft that passes Rally's checks (output 404, 478 tokens). Fix not yet applied in rally/llm.py; G10/G14 not yet rerun with it.

### Other gaps observed (all safe)
- G14: Empty-court template says the first 4 to reply YES “get the spots” and never says the game depends on four players confirming and the booking being paid (runner: FAIL; judge: 2/5). Proposed: reword the template and add a validation rule for the condition.
- G04: Offline backup parser only: “start at 7” is escalated without a proposed time. The live LLM returned 19:00 and passed.
- G08: A send requested before approval is impossible, but the denied attempt raises an error and is not written to the audit log.
- G12: No retry limit: Rally re-checks the provider on every clock tick while UNVERIFIED (never confirms).
- G18: Inbound replies have no event ID; a duplicate is parsed again but causes no second link or seat.
- G11/G20: State names differ from the spec (ESCALATED for CANCELLED_UNAVAILABLE; CANCELLED for STOPPED).
- G10/G14: Invites state the $30/player price from club_policy.yaml. The runner scores this as equivalent (it is Rally's verified price source); the judge scored it as an invented price (2/5) because the fixtures say no price was supplied. The team must decide which reading is right.