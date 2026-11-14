# START HERE · Rally (NBAY 6170, Team 3)

Rally fills empty off-peak padel courts and missing seats in open matches. It detects and ranks
automatically, a manager approves every invitation, Claude reads player replies into a strict schema,
Python makes every decision, and only a provider-verified paid booking counts.

- **Live app (no login):** https://rally-nbay6170.streamlit.app. It may take about 30 seconds to wake.
  Click "Reset demo data" in the sidebar first.
- **Code:** https://github.com/swarsahgal/rally-nbay6170
- **Demo video:** `Rally_demo.mp4`, uploaded with this submission.

## Rubric item → where to find it

| Rubric item | Evidence | Files |
|---|---|---|
| **Working prototype** | Public app with a no-login demo path. Without an API key it runs on built-in rules; real recorded Claude outputs are in the live-run traces. 57 automated tests. | Live app (above) · `app.py` · `rally/` · `tests/` · `outputs/golden/live-claude-haiku-5-5/traces/` · `scripts/run_demo_sequences.py` |
| **Simplified workflow** | detect → filter → rank → manager approval → dispatch → parse reply (LLM) → advance state (code) → verify booking (provider) → append-only log | `docs/PRD.md` · `README.md` ("PRD tools → code", "Hard invariants") · `rally/engine.py` · `rally/coordinator.py` |
| **Knowledge source** | One versioned club policy file, loaded at evaluation and again before every send, its version stamped on every event; the club's real occupancy spreadsheet drives hours, slots and off-peak | `data/club_policy.yaml` · `data/raw/occupancy_untitled-2.xlsx` · `scripts/build_fixtures.py` · `data/*.csv` |
| **Eval pack** | 20 golden cases with expected outputs and forbidden actions; per-case results, assertions and traces; per-case team scores; two independent judges (gpt-6.1-sol, Claude Sonnet 5.5) with agreement; three observed failure patterns | `fixtures/golden_cases.csv` · `outputs/golden/live-claude-haiku-5-5/results.csv` (columns `team_score_confirmed`, `team_reviewer`) · `.../judge_results.csv` · `.../agreement.json` · `outputs/golden/Rally_Golden_Evaluation_Report.docx` · `scripts/golden/` |
| **Unit economics** | Same 10 tasks on 4 models (2 tiers × 2 providers), 120 calls: latency, cost per 100 runs, quality, unsafe outputs, and the trade-off we accept; cost per campaign and per month | `docs/unit_economics.md` · `outputs/tier_benchmark_summary.md` · `outputs/tier_benchmark_runs.csv` · `scripts/tier_benchmark.py` · `data/model_pricing.yaml` |
| **Commercial case** | Price hypothesis, cost to serve, gross margin per club, club break-even in bookings, what does not count as revenue, pilot design | `docs/commercial_case.md` |

## Verify in about 2 minutes (no API key needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                                   # 57 pass, 10 live tests skipped without a key
python scripts/golden/run_golden.py      # the 20 golden cases on the offline parser
streamlit run app.py                     # the same app as the live link
```

More detail for evaluators, including known open issues: `EVALUATION.md`.
