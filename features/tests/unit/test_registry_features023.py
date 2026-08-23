"""Feature 098 (REJECTED, reverted): the canonical race_class representation stays UNWIRED.

The measurement (specs/098-race-class-spelling/verdict.json) found no measurable cost of the
spelling split under retraining (pooled +0.0004, CI straddling zero), so the registry stays on
features-021 with the raw representation. features-022 (097) and features-023 (098) are burned.
"""

from __future__ import annotations

import hashlib

from horseracing_features.race_class_canon import REPRESENTATIONS
from horseracing_features.registry import (
    COMPATIBLE_PRIOR_FEATURE_VERSIONS,
    FEATURE_VERSION,
    RACE_CLASS_REPRESENTATION,
    model_input_features,
)

FEATURES_021_HASH = "663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a"


def _feature_hash(feature_cols: list[str]) -> str:
    """Mirror training.artifacts.feature_hash without making features depend on training."""
    return hashlib.sha256("|".join(feature_cols).encode()).hexdigest()


def test_registry_is_reverted_to_features021_raw_representation():
    assert FEATURE_VERSION == "features-021"
    assert RACE_CLASS_REPRESENTATION == "raw"
    assert RACE_CLASS_REPRESENTATION in REPRESENTATIONS
    assert "features-022" not in COMPATIBLE_PRIOR_FEATURE_VERSIONS  # burned by 097
    assert "features-023" not in COMPATIBLE_PRIOR_FEATURE_VERSIONS  # burned by 098


def test_model_input_columns_match_the_active_model_hash():
    assert _feature_hash(model_input_features()) == FEATURES_021_HASH
