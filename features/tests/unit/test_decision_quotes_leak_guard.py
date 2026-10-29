"""Feature 174: the near-post decision records never enter the feature pipeline (憲法 II).

decision_quotes freezes a pre-race quote and the predictions made from it. The guard is
structural, like the purchase-records guard: no features source may reference the table or its
ORM class, and no registered feature column may be named after it.
"""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import REGISTRY, materialized_columns, model_input_features

_ROOT = Path(__file__).resolve().parents[3]
_FEATURES_SRC = _ROOT / "features" / "src" / "horseracing_features"
_FORBIDDEN = ("decision_quotes", "DecisionQuote", "decision_quote", "near_post",
              "decision_quote_scoring", "near_post_score", "artifacts/near_post")


def test_no_features_source_references_decision_quotes():
    offenders = []
    for path in sorted(_FEATURES_SRC.rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {t}" for t in _FORBIDDEN if t in body]
    assert not offenders, f"decision quotes leaked into the feature pipeline: {offenders}"


def test_no_decision_quote_named_feature_columns():
    names = {f.lower() for f in REGISTRY}
    names |= {c.lower() for c in materialized_columns()}
    names |= {c.lower() for c in model_input_features()}
    hits = [n for n in names if "decision" in n or "near_post" in n or "quote_score" in n]
    assert not hits, hits
