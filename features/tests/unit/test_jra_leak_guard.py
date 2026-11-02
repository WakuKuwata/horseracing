"""Feature 176: track conditions and the dual-source provenance never enter the feature pipeline.

``jra_track_conditions`` (含水率・クッション値・馬場状態) is display/research data (FR-021) and the two
provenance tables only say which route wrote a value (INV-P4). Neither may be read by features,
nor appear as a registered column. Structural guard, same shape as the decision-quotes guard; the
dynamic counterpart (rows in these tables do not move the parquet) is
``features/tests/integration/test_forbidden_tables_do_not_move_features.py``.
"""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import REGISTRY, materialized_columns, model_input_features

_ROOT = Path(__file__).resolve().parents[3]
_FEATURES_SRC = _ROOT / "features" / "src" / "horseracing_features"
_FORBIDDEN = (
    "jra_track_conditions",
    "JraTrackCondition",
    "cushion",
    "moisture",
    "race_field_provenance",
    "RaceFieldProvenance",
    "race_horse_field_provenance",
    "RaceHorseFieldProvenance",
)


def test_no_features_source_references_jra_tables():
    offenders = []
    for path in sorted(_FEATURES_SRC.rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {t}" for t in _FORBIDDEN if t in body]
    assert not offenders, f"176 tables leaked into the feature pipeline: {offenders}"


def test_no_feature_columns_named_after_them():
    names = {f.lower() for f in REGISTRY}
    names |= {c.lower() for c in materialized_columns()}
    names |= {c.lower() for c in model_input_features()}
    hits = [n for n in names if any(t in n for t in ("cushion", "moisture", "provenance", "track_condition"))]
    assert not hits, hits
