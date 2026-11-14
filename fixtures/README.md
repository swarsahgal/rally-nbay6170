# fixtures/

- `golden_cases.csv`: the team's 20 golden evaluation cases (Rally_Golden20_Submission_Data.csv): setup,
  inputs, expected model and system output, pass assertions, forbidden outputs, severity, and the 10 cases
  mapped to benchmark tasks T01-T10 for independent judging.
- `scripts/golden/run_golden.py` executes these cases against Rally's real workflow code; see
  `scripts/golden/README.md`.
- The three live demo sequences (PRD section 7) are scripted in `scripts/run_demo_sequences.py`, and the
  hard invariants are covered by `tests/`.

All demo data is generated from `data/`: fictional players plus the club occupancy profile.
