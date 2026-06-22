"""Compare two stored predictors' evaluations (US4, FR-015).

Reads `model_versions.metrics_summary` (which already holds overall + by_fold), so
no extra table is required for same-condition comparison; the normalized
`eval_runs` table is deferred (FR-015 "必要なら") until richer querying is needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from horseracing_db.models import ModelVersion
from sqlalchemy.orm import Session

_LABELS = ("win", "top2", "top3")
_METRICS = ("log_loss", "brier", "auc", "ndcg", "ece")


@dataclass(frozen=True)
class Comparison:
    model_a: str
    model_b: str
    same_scheme: bool
    diffs: dict[str, dict[str, float | None]]  # label -> metric -> (a - b)


def compare(session: Session, model_a: str, model_b: str) -> Comparison:
    a = session.get(ModelVersion, model_a)
    b = session.get(ModelVersion, model_b)
    if a is None or b is None:
        raise ValueError("both model_versions must exist")
    ea = a.metrics_summary["eval"]
    eb = b.metrics_summary["eval"]
    same = ea.get("scheme") == eb.get("scheme") and ea.get("valid_years") == eb.get("valid_years")

    diffs: dict[str, dict[str, float | None]] = {}
    for label in _LABELS:
        oa = ea["overall"].get(label, {})
        ob = eb["overall"].get(label, {})
        diffs[label] = {}
        for m in _METRICS:
            va, vb = oa.get(m), ob.get(m)
            diffs[label][m] = (va - vb) if (va is not None and vb is not None) else None
    return Comparison(model_a=model_a, model_b=model_b, same_scheme=same, diffs=diffs)
