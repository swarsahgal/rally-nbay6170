# AI-use log (PRD section 15)

Record the tools and models used to build and test Rally, what was accepted or rejected, the human
checks, and the code changes they produced.

| Date | Who | Tool / model | What it did | Accepted / changed / rejected | Human check |
|---|---|---|---|---|---|
| 2026-11-05 | (team member) | Claude (claude.ai) | Generated the first demo repo from PRD v1.0 and the club occupancy spreadsheet: data pipeline, engine, Streamlit UI, tests, README and demo script | _to fill in after team review_ | _e.g. reviewed invariants, ran `pytest` and `scripts/run_demo_sequences.py`, walked the three demo sequences_ |
| | | | | | |

Notes:

- The live Claude parser path (`pytest -m live`) was not run when the repo was generated (no API key
  in that environment). Run it with a key and log the result here.
- Model prices used for the cost comparison must come from the official pricing page on the day of
  the calculation (record the URL and date).
