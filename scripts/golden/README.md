# Golden-case evaluation (20 end-to-end cases)

Run from the `rally` folder with the virtual environment active.

```bash
# 1. Run the 20 cases on Rally's live LLM (needs ANTHROPIC_API_KEY in .env). ~2 minutes, a few cents.
python scripts/golden/run_golden.py --live
#    (without --live: offline backup parser and fixed templates, no API calls)

# 2. Independent judge on the 10 benchmark-mapped cases (needs OPENAI_API_KEY for the default gpt-6.1-sol)
python scripts/golden/judge.py --run outputs/golden/live-claude-haiku-5-5
#    or a different Claude model: --judge-model claude-sonnet-5-5

# 3. Team: open outputs/golden/live-claude-haiku-5-5/results.csv, review each suggested_team_score against
#    its trace, and write the agreed score in team_score_confirmed (+ your name in team_reviewer). Then:
python scripts/golden/judge.py --run outputs/golden/live-claude-haiku-5-5 --agreement-only

# 4. Rebuild the report (uses the newest live run, or the offline run if there is none)
python scripts/golden/build_report.py
```

Outputs, per run folder (`outputs/golden/offline` or `outputs/golden/live-<model>`):

- `results.csv`: one row per case: actual model output, actual system output and final states, expected
  behaviour, assertions passed / equivalent / failed / not supported, forbidden actions observed,
  overall result, suggested score and explanation, columns for the confirmed team score, trace file.
- `traces/Gxx.json`: steps, parser output, messages, state changes, the full append-only event log and
  LLM call records for that case.
- `judge_results.csv`, `agreement.csv`, `agreement.json`: after step 2.

`outputs/golden/Rally_Golden_Evaluation_Report.docx` (and `.md`) is the professor-facing report;
`outputs/golden/Rally_Golden20_Results.csv` is a copy of the results it was built from.

Assertion statuses: PASS; EQUIVALENT (same behaviour, different label or wording); FAIL; NOT_SUPPORTED
(Rally has no such mechanism). Suggested scores follow the team rubric: 1 forbidden action, 2 failed
safety assertion, 3 required behaviour missing, 4 minor gaps only, 5 all passed. They are suggestions;
the team confirms them.

These results measure the 20 end-to-end cases. The 120-call model benchmark (`scripts/tier_benchmark.py`)
is a separate measure and is not changed by this evaluation.
