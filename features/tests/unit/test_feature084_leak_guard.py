"""T006 (084): top-3 chaos display axes never re-enter model features (憲法 II).

Feature 084 surfaces market-derived top-3 composition probabilities for DISPLAY only. These
read-only values must not become registry entries, materialized columns, or model inputs. Also
confirms the feature schema version is unchanged and 084's additive migration is the schema head.
"""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import (
    FEATURE_VERSION,
    REGISTRY,
    materialized_columns,
    model_input_features,
)

_ROOT = Path(__file__).resolve().parents[3]
# Specific 084 display-axis names. NOT "popularity" — existing as-of popularity features are
# legitimate model inputs; the leak concern is the top-3 chaos display axes only.
_FORBIDDEN_084 = (
    "chaos_band", "p_s_ge_20", "himo_are", "total_collapse",
    "expected_top3_popularity_sum",
)


def test_chaos_axes_not_in_feature_registry():
    feats = {f.lower() for f in REGISTRY}
    for token in _FORBIDDEN_084:
        assert not any(token in f for f in feats), f"084 display axis '{token}' in registry"


def test_chaos_axes_not_in_materialized_columns():
    cols = {c.lower() for c in materialized_columns()}
    for token in _FORBIDDEN_084:
        assert not any(token in c for c in cols), f"084 display axis '{token}' in materialized cols"


def test_chaos_axes_not_in_model_recipe():
    feats = {f.lower() for f in model_input_features()}
    for token in _FORBIDDEN_084:
        assert not any(token in f for f in feats), f"084 display axis '{token}' leaked into recipe"


def test_084_schema_contract():
    assert FEATURE_VERSION == "features-022"  # 097 early_mid_pace (021 = 091 prev_weight; 019/020 burned)
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0016_"), versions[-1]
