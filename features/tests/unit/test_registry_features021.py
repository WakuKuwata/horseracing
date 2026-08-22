"""Feature 091 T038: registration faults that only surface AFTER the version bump.

A column can be wired, tested, and still be missing from the model's inputs — registered twice,
registered in no group, or ordered non-deterministically. None of that shows up in the block's own
unit tests, and by the time a multi-hour fit reveals it the cost is already paid.
"""

from __future__ import annotations

from horseracing_features.registry import (
    COMPATIBLE_PRIOR_FEATURE_VERSIONS,
    FEATURE_GROUPS,
    FEATURE_VERSION,
    REGISTRY,
    materialized_columns,
    model_input_features,
)

PREV_WEIGHT = "prev_weight"


def test_prev_weight_is_registered_exactly_once_as_a_model_input():
    cols = model_input_features()
    assert cols.count(PREV_WEIGHT) == 1, "registered zero times or duplicated"
    assert PREV_WEIGHT in REGISTRY


def test_prev_weight_belongs_to_exactly_one_group():
    assert FEATURE_GROUPS[PREV_WEIGHT] == "weight_history"
    members = [c for c, g in FEATURE_GROUPS.items() if g == "weight_history"]
    assert members == [PREV_WEIGHT], f"unexpected group members: {members}"


def test_prev_weight_is_materialized_not_static():
    """It is an as-of column, so it must ride in the parquet, not be recomputed per build."""
    assert PREV_WEIGHT in materialized_columns()


def test_column_order_is_deterministic():
    """A non-deterministic order would change feature_hash between processes and break the
    compat pin in a way no single-process test can see."""
    import hashlib
    import json

    def _hash(cols):
        return hashlib.sha256(json.dumps(cols, sort_keys=False).encode()).hexdigest()

    assert model_input_features() == model_input_features()
    assert _hash(model_input_features()) == _hash(model_input_features())


def test_feature_version_and_compat_pin():
    assert FEATURE_VERSION == "features-022"
    # 019 was burned by 070's revert and must never be reintroduced
    assert "features-019" not in COMPATIBLE_PRIOR_FEATURE_VERSIONS
    assert set(COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-021"]) == {"features-018"}
    # Feature 097 bumped to 022 on top; 021 stays pinned there (lgbm-094 lineage)
    assert "features-021" in COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-022"]


def test_prev_weight_declares_pre_entry_availability():
    """The whole point: it is known before the entry sheet, unlike same-day `weight`."""
    assert REGISTRY[PREV_WEIGHT].timing.value == "pre_entry"
    assert REGISTRY["weight"].timing.value == "post_weight"


def test_freshness_and_availability_columns_that_replace_dedicated_ones_exist():
    """research D1 dropped weight_age_days / has_prev_weight because these already carry the
    information. If either disappeared, the 1-column design would silently lose it."""
    cols = model_input_features()
    assert "days_since_last" in cols
    assert "has_past_race" in cols
