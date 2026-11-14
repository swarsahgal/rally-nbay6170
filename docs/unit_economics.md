# Rally unit economics: models, messaging, Playtomic and hosting

Running Rally for one club costs roughly **$18–$55 a month**. Hosting is the largest line; the LLM is
the smallest. Model costs below are measured; messaging, Playtomic and hosting are list prices checked
Nov 5, 2026 (sources at the end). Everything else is a labelled assumption.

## 1. Two model tiers on the same 10 tasks (measured)

Same 10 tasks (8 reply interpretations, including a prompt injection and a conditional yes, plus 2 invite
drafts), 3 runs per model, 120 calls, through Rally's real LLM code (`scripts/tier_benchmark.py`). Quality
is scored 1–5 automatically against expected labels; "unsafe" means `safe_to_progress=true` where it
should be false.

| Model | Tier | Median latency | p90 latency | Avg tokens in / out | Cost per 100 runs | Mean quality (1–5) | Tasks fully correct | Unsafe outputs |
|---|---|---|---|---|---|---|---|---|
| claude-haiku-5-5 | small | 1,344 ms | 2,729 ms | 1,286 / 103 | $0.018 | 4.60 | 9 / 10 | 0 |
| claude-sonnet-5-5 | mid | 1,556 ms | 2,212 ms | 1,346 / 87 | $0.357 | 4.93 | 9 / 10 | 0 |
| gpt-6-luna | small | 1,688 ms | 2,495 ms | 672 / 94 | $0.011 | 5.00 | 10 / 10 | 0 |
| gpt-6.1-sol | mid | 2,123 ms | 2,784 ms | 672 / 63 | $0.197 | 5.00 | 10 / 10 | 0 |

Raw files: `outputs/tier_benchmark_summary.md` and `outputs/tier_benchmark_runs.csv` (every call).
Prices: `data/model_pricing.yaml`. Formula (PRD section 9): `cost_per_100 = mean(input_tokens × input_rate +
output_tokens × output_rate) × 100`.

**Caveat found later (golden-case run, failure F3):** Rally's Claude calls allow 400 output tokens, and
Haiku 5.5 spends part of that on thinking, so drafts are often cut off. The two drafting tasks (T09, T10)
may understate Haiku's drafting quality; rerun after raising the limit.

## 2. The trade-off we accept

Rule: zero unsafe outputs first, then mean quality ≥ 4.5 with at least 9 of 10 tasks correct, then the
faster model. All four pass, so speed decides: **Claude Haiku 5.5** (fastest, 1,344 ms median).

What we accept: lower measured quality (4.60, against 4.93 for Sonnet and 5.00 for both GPT models),
which means a few more replies go to the manager. It never means a wrong booking, because Python, not the
model, makes every state change. gpt-6-luna is the tested fallback; the code switches provider by model
name.

## 3. Cost per campaign

Call counts from the demo flows (missing player: 1 draft + ~2 replies; empty court: 1 draft + ~6 replies),
using the measured cost per call.

| Cost line | Pricing basis | Missing player | Empty court |
|---|---|---|---|
| LLM, small tier (Haiku 5.5) | measured per call | $0.0005 | $0.0013 |
| LLM, mid tier (Sonnet 5.5) | measured per call | $0.011 | $0.025 |
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
