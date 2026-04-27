"""Benchmark harness.

Two corpora live alongside the harness:

  - `corpus/synthea_valid/`: gold valid Synthea-generated resources.
  - `corpus/synthea_mutated/`: programmatically mutated copies, with
    ground-truth pairings back to the valid version.
  - `corpus/wild_sample/`: real broken FHIR drawn from public sources,
    manually screened for PHI. No ground truth.

`mutate.py` produces the mutated corpus; `run.py` runs the tool against the
mutated corpus and emits a leaderboard JSON.
"""
