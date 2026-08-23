"""Feature 098: bind canonical race_class spelling to the features-023 registry version."""

from __future__ import annotations

import hashlib

from horseracing_features.registry import (
    COMPATIBLE_PRIOR_FEATURE_VERSIONS,
    FEATURE_VERSION,
    RACE_CLASS_REPRESENTATION,
    is_feature_version_servable,
    model_input_features,
)

FEATURES_021_HASH = "663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a"


def _feature_hash(feature_cols: list[str]) -> str:
    """Mirror training.artifacts.feature_hash without making features depend on training."""
    return hashlib.sha256("|".join(feature_cols).encode()).hexdigest()


def test_registry_declares_features023_canonical_representation_and_compat_pin():
    assert FEATURE_VERSION == "features-023"
    assert RACE_CLASS_REPRESENTATION == "canonical-v1"
    assert (
        COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-023"]["features-021"]
        == FEATURES_021_HASH
    )
    assert "features-022" not in COMPATIBLE_PRIOR_FEATURE_VERSIONS


def test_features023_keeps_the_features021_model_input_columns_and_order():
    assert _feature_hash(model_input_features()) == FEATURES_021_HASH


def test_features021_is_servable_only_with_its_pinned_hash():
    assert is_feature_version_servable("features-021", FEATURES_021_HASH)
    assert not is_feature_version_servable("features-021", "not-the-pinned-hash")
