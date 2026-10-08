"""Benchmark harness.

The corpora live alongside the harness:

  - `corpus/synthea_full/`: 100 valid Synthea-generated resources, the
    corpus the published results use.
  - `corpus/synthea_valid/`: 6 hand-curated resources, kept as a smoke
    fixture.
  - `corpus/synthea_mutated/`: programmatically mutated copies, with
    ground-truth pairings back to the valid version.

`mutate.py` produces the mutated corpus; `run.py` runs the tool against the
mutated corpus and emits a leaderboard JSON.
"""
