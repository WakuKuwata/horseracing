# 142 research commands

Run from repository root. Existing 135/140/141 outputs and production are unchanged. New capture is deferred.

```sh
# Meaningful temporal, financial, objective and serialization checks.
training/.venv/bin/python -m pytest scripts/tests/test_roi_improvement_run_142.py scripts/tests/test_roi_improvement_report_142.py scripts/tests/test_roi_improvement_policies_142.py scripts/tests/test_roi_improvement_models_142.py -q

# Already frozen; verify rather than re-freeze.
training/.venv/bin/python scripts/roi_explore/roi_improvement_142/run.py verify

# Completed policy comparison. Refuses to overwrite existing output.
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 training/.venv/bin/python scripts/roi_explore/roi_improvement_142/run.py policies

# Fixed six-candidate screen (2016–2018 only).
training/.venv/bin/python scripts/roi_explore/roi_improvement_142/experiment.py screen

# 15 seeds × 8 years; two workers, one thread each.
training/.venv/bin/python scripts/roi_explore/roi_improvement_142/experiment.py evaluate

# Complete fits may be resumed after exact identity/hash checks; incomplete fits fail closed.
training/.venv/bin/python scripts/roi_explore/roi_improvement_142/experiment.py evaluate --resume

# Mean probabilities across all seeds, then score the ensemble.
training/.venv/bin/python scripts/roi_explore/roi_improvement_142/report.py
```

The main settlement is `won × stored odds`, not certified historical decision-time returns. The local archived official-win-payout file has zero races before 2026-06-27; official settlement for this window is unavailable and is never filled with invented zeros. Every historical period was previously explored; no deployment or adoption verdict follows from this research.
