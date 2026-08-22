"""Feature 097 (T009): registration faults that only surface AFTER the version bump.

Mirrors test_registry_features021 (091): a column can be wired and tested and still be missing
from the model's inputs — registered twice, in no group, out of the parquet, or ordered
non-deterministically — and a multi-hour fit is the first place it shows.
"""

from __future__ import annotations

import hashlib
import json

from horseracing_features.registry import (
    COMPATIBLE_PRIOR_FEATURE_VERSIONS,
    FEATURE_GROUPS,
    FEATURE_VERSION,
    REGISTRY,
    materialized_columns,
    model_input_features,
)

COLS = ("asof_rel_early_mid_avg", "asof_rel_early_mid_best")
GROUP = "early_mid_pace"

#: lgbm-094-cap900 metadata.feature_hash, MEASURED 2026-08-22 (specs/097-early-mid-pace/
#: evidence-preflight.md) — the value the compat pin must carry verbatim.
LGBM_094_HASH = "663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a"


def test_each_column_registered_exactly_once_as_model_input():
    cols = model_input_features()
    for c in COLS:
        assert cols.count(c) == 1, c
        assert c in REGISTRY


def test_group_membership_is_exactly_the_two_columns():
    members = [c for c, g in FEATURE_GROUPS.items() if g == GROUP]
    assert members == list(COLS), members
    for c in COLS:
        assert FEATURE_GROUPS[c] == GROUP


def test_columns_are_materialized_as_of_not_static():
    for c in COLS:
        assert c in materialized_columns()


def test_pre_entry_timing_like_the_other_pace_columns():
    for c in COLS:
        assert REGISTRY[c].timing.value == "pre_entry"
        assert REGISTRY[c].source == REGISTRY["rel_last3f_avg"].source


def test_column_order_is_deterministic():
    def _hash(cols):
        return hashlib.sha256(json.dumps(cols, sort_keys=False).encode()).hexdigest()
    assert _hash(model_input_features()) == _hash(model_input_features())


def test_feature_version_and_compat_pin():
    assert FEATURE_VERSION == "features-022"
    assert "features-019" not in COMPATIBLE_PRIOR_FEATURE_VERSIONS   # burned (070)
    assert "features-020" not in COMPATIBLE_PRIOR_FEATURE_VERSIONS   # burned (088)
    pins = COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-022"]
    assert set(pins) == {"features-021", "features-018"}
    assert pins["features-021"] == LGBM_094_HASH


def test_existing_first3f_columns_are_untouched():
    """INV-EM2: the dying axis stays registered; 097 adds, never replaces."""
    for c in ("asof_rel_first3f_avg", "asof_rel_first3f_best", "asof_pace_balance_avg"):
        assert c in model_input_features()
