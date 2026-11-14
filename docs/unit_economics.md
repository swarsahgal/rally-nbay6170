# Rally unit economics: models, messaging, Playtomic and hosting

Running Rally for one club costs roughly **$18–$55 a month**. Hosting is the largest line; the LLM is
the smallest, even on the stronger model. Model costs below are measured; messaging, Playtomic and hosting are list prices checked
Nov 5, 2026 (sources at the end). Everything else is a labelled assumption.

## 1. Two model tiers on the same 10 tasks (measured)

Same 10 tasks (8 reply interpretations, including a prompt injection and a conditional yes, plus 2 invite
drafts), 3 runs per model, 120 calls in one run on Nov 14, 2026, through Rally's real LLM code
(`scripts/tier_benchmark.py`). Quality is scored 1–5 automatically against expected answers; "unsafe"
means `safe_to_progress=true` where it should be false.

| Model | Tier | Median latency | p90 latency | Avg tokens in / out | Cost per 100 runs | Mean quality (1–5) | Tasks fully correct | Unsafe outputs |
|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 | small | 1,539 ms | 2,772 ms | 1,247 / 80 | $0.017 | 4.33 | 8 / 10 | 0 |
| claude-sonnet-5-5 | mid | 1,632 ms | 2,374 ms | 1,346 / 79 | $0.348 | 4.97 | 9 / 10 | 0 |
| gpt-6-luna | small | 2,080 ms | 2,988 ms | 672 / 90 | $0.011 | 5.00 | 10 / 10 | 0 |
| gpt-6.1-sol | mid | 2,172 ms | 2,662 ms | 672 / 52 | $0.186 | 5.00 | 10 / 10 | 0 |

Raw files: `outputs/tier_benchmark_summary.md` (also per task) and `outputs/tier_benchmark_runs.csv` (every
call, with each model's output). Prices: `data/model_pricing.yaml`. Formula (PRD section 9):
`cost_per_100 = mean(input_tokens × input_rate + output_tokens × output_rate) × 100`.

Where the points were lost:
- **Haiku 5.5** scored 5.0 on all 8 reply-reading tasks and was the fastest on them, but scored 1.0 and 2.3
  on the two drafting tasks (T09, T10): 5 of 6 draft calls were cut off and Rally used its fixed template.
  This is our bug (failure F3): Rally allows 400 output tokens and Haiku spends part of that thinking. A
  separate check with 2,000 tokens returned valid drafts (`scripts/golden/check_draft_tokens.py`).
- **Sonnet 5.5** read “start at 7 instead?” as 07:00 in one of three runs (expected 19:00); it is flagged
  for the manager either way.

## 2. The trade-off we accept

Rule: zero unsafe outputs first, then mean quality ≥ 4.5 with at least 9 of 10 tasks correct, then the
faster model.

- **As the code stands, the pick is Claude Sonnet 5.5.** Haiku fails the quality bar (4.33, 8 of 10)
  because of the drafting bug. Sonnet, gpt-6-luna and gpt-6.1-sol all pass, and Sonnet is the fastest
  of them (1,632 ms median).
- **What we accept:** about 20× Haiku's cost per call. That is $0.35 instead of $0.02 per 100 calls, or
  about 2 cents more per empty-court campaign: small next to messaging cost and a $120 booking.
- **What we'd change next:** fix the 400-token limit and rerun. Haiku already scores 5.0 on every
  reply-reading task and is fastest there, so if its drafts pass after the fix it becomes the pick: faster
  and about 20× cheaper. gpt-6-luna stays the tested fallback; the code switches provider by model name.
- Safety does not depend on the model: Python, not the model, makes every state change, and an
  uncertain reading goes to the manager.

## 3. Cost per campaign

Call counts from the demo flows (missing player: 1 draft + ~2 replies; empty court: 1 draft + ~6 replies),
using the measured cost per call from this run.

| Cost line | Pricing basis | Missing player | Empty court |
|---|---|---|---|
| LLM, Claude Sonnet 5.5 (current pick) | measured per call | $0.010 | $0.024 |
| LLM, Claude Haiku 5.5 (after the fix) | measured per call | $0.0005 | $0.0012 |
| WhatsApp, invites as marketing templates | $0.025 per invite; replies free | $0.05 | $0.15 |
| WhatsApp, if Meta accepts invites as utility | $0.0034 per invite | $0.007 | $0.02 |
| SMS (Twilio, US long code) | ~$0.012 per outbound segment incl. carrier fee | ~$0.09 | ~$0.32 |
| Playtomic API | included in the club's Champion or Master plan | $0 | $0 |

## 4. Monthly fixed costs per deployment

| Item | Pilot | Production |
|---|---|---|
| Hosting (Render web service) | $7 | $25 |
| Database (Render Postgres) | $6 | $19 |
| SMS number (only if SMS) | $1.15 | $1.15 |

**Example month for one club** (assumption: 15 missing-player and 25 empty-court campaigns):
WhatsApp + either Claude model + pilot hosting ≈ **$18**; SMS + Sonnet + production hosting ≈ **$55**.

## 5. Open questions before a pilot

- **WhatsApp in the US:** several sources report Meta isn't delivering marketing templates to US numbers;
  Meta's docs neither confirm nor deny. A US pilot may need SMS or utility-approved templates.
- **Playtomic:** the API is read-only; a third-party tool needs Playtomic Connect certification (no fee
  published).

## Sources (checked Nov 5, 2026)

- Anthropic API pricing: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI API pricing: https://developers.openai.com/api/docs/pricing
- Meta WhatsApp Business Platform pricing: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing
- North America WhatsApp rates via secondary sources quoting Meta's rate card, e.g. https://formbeep.pages.dev/whatsapp-api-pricing/
- Twilio US SMS pricing: https://www.twilio.com/en-us/sms/pricing/us
- Playtomic API guide: https://helpmanager.playtomic.com/hc/en-gb/articles/38836515997073 and pricing: https://playtomic.com/pricing
- Render pricing: https://render.com/pricing
