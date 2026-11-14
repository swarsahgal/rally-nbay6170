# Rally unit economics: models, messaging, Playtomic and hosting

Running Rally for one club costs roughly **$18–$55 a month**. Hosting is the largest line; the LLM is
the smallest, even on the stronger model. Model costs below are measured; messaging, Playtomic and hosting are list prices checked
Nov 5, 2026 (sources at the end). Everything else is a labelled assumption.

## 1. Two model tiers on the same 10 tasks (measured)

Same 10 tasks (8 reply interpretations, including a prompt injection and a conditional yes, plus 2 invite
drafts), 3 runs per model, 120 calls in one run on Nov 14, 2026, through Rally's real LLM code
(`scripts/tier_benchmark.py`), after the drafting fix below. Quality is scored 1–5 automatically against
expected answers; "unsafe" means `safe_to_progress=true` where it should be false.

| Model | Tier | Median latency | p90 latency | Avg tokens in / out | Cost per 100 runs | Mean quality (1–5) | Tasks fully correct | Unsafe outputs |
|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 | small | 1,518 ms | 2,844 ms | 1,346 / 156 | $0.021 | 5.00 | 10 / 10 | 0 |
| claude-sonnet-5-5 | mid | 1,601 ms | 1,900 ms | 1,346 / 78 | $0.347 | 4.93 | 9 / 10 | 0 |
| gpt-6-luna | small | 1,859 ms | 2,889 ms | 672 / 94 | $0.011 | 5.00 | 10 / 10 | 0 |
| gpt-6.1-sol | mid | 2,294 ms | 2,783 ms | 672 / 60 | $0.194 | 5.00 | 10 / 10 | 0 |

Raw files: `outputs/tier_benchmark_summary.md` (also per task) and `outputs/tier_benchmark_runs.csv` (every
call, with each model's output). Prices: `data/model_pricing.yaml`. Formula (PRD section 9):
`cost_per_100 = mean(input_tokens × input_rate + output_tokens × output_rate) × 100`.

**The run before the fix** (`outputs/tier_benchmark_before_f3_fix_*`, same day): Haiku scored 4.33 and 8 of
10, losing points only on the two drafting tasks (1.0 and 2.3), because Rally capped answers at 400 output
tokens and Haiku spends part of that thinking, so 5 of 6 drafts were cut off (failure F3). Under our rule
that run picked Sonnet. Raising the limit to 2,000 tokens fixed it: Haiku's output grew from 80 to 156
tokens on average, its drafts now complete, and it scores 5.0 on all 10 tasks. Sonnet's remaining miss is
reading "start at 7 instead?" as 07:00 in two of three runs (expected 19:00); it goes to the manager either way.

## 2. The trade-off we accept

Rule: zero unsafe outputs first, then mean quality ≥ 4.5 with at least 9 of 10 tasks correct, then the
faster model.

- **Pick: Claude Haiku 5.5.** All four models pass the safety gate; Haiku, gpt-6-luna and gpt-6.1-sol score
  5.00 and Sonnet 4.93. Haiku is the fastest (1,518 ms median), and about 16× cheaper per call than Sonnet.
- **What we accept:** Haiku's p90 latency (2,844 ms) is higher than Sonnet's (1,900 ms), so a few replies
  take longer, and it is a small model, so an unusual reply is more likely to go to the manager than be
  understood. That costs manager time, never a wrong booking: Python, not the model, makes every state change.
- **Fallback:** gpt-6-luna (same quality, cheapest, about 340 ms slower median); the code switches provider by
  model name, so an outage at one provider doesn't stop the club.
- **Lesson:** the benchmark caught a bug in our own code, not a weak model. Without it we would have paid
  about 16× more per call for Sonnet.

## 3. Cost per campaign

Call counts from the demo flows (missing player: 1 draft + ~2 replies; empty court: 1 draft + ~6 replies),
using the measured cost per call from this run.

| Cost line | Pricing basis | Missing player | Empty court |
|---|---|---|---|
| LLM, Claude Haiku 5.5 (pick) | measured per call | $0.0006 | $0.0015 |
| LLM, Claude Sonnet 5.5 | measured per call | $0.010 | $0.024 |
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
WhatsApp + Haiku + pilot hosting ≈ **$18**; SMS + Sonnet + production hosting ≈ **$55**.

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
