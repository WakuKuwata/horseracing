"""T030 (SC-001/FR-007a): under features-019, BOTH lgbm-064-f02acc (features-018) and lgbm-063
(features-017) stay servable via the non-transitive dual compat pin; ANY same-version column-subset
artifact is NOT_SERVABLE (fail-closed).

Measures each model's real metadata.feature_hash (never trusts the literal, analyze V1), asserts it
equals the registry pin, and that the servability gate accepts it while rejecting wrong/subset hashes.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from horseracing_features.registry import (
    COMPATIBLE_PRIOR_FEATURE_VERSIONS,
    is_feature_version_servable,
)

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_M064 = _ROOT / "artifacts/model_versions/lgbm-064-f02acc/metadata.json"
_M063 = _ROOT / "artifacts/model_versions/lgbm-063/metadata.json"


def test_019_pins_both_018_and_017_directly_non_transitive():
    pins = COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]
    assert set(pins) == {"features-018", "features-017"}  # both DIRECT, no transit


@pytest.mark.skipif(not _M064.exists(), reason="lgbm-064-f02acc artifact not present")
def test_018_pin_matches_measured_lgbm064_hash():
    measured = json.loads(_M064.read_text())["feature_hash"]
    assert measured == COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]["features-018"]
    assert is_feature_version_servable("features-018", measured, "features-019")


@pytest.mark.skipif(not _M063.exists(), reason="lgbm-063 artifact not present")
def test_017_pin_matches_measured_lgbm063_hash():
    measured = json.loads(_M063.read_text())["feature_hash"]
    assert measured == COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]["features-017"]
    assert is_feature_version_servable("features-017", measured, "features-019")


def test_same_version_subset_is_not_servable_fail_closed():
    # a features-019 recipe-drop candidate (F03 replacement, or unadopted-F04/F05 dropped) has a
    # DIFFERENT hash than the current schema and is NOT a prior version -> fail closed (FR-007a).
    assert not is_feature_version_servable("features-019", "any-subset-hash", "features-019")
    # wrong hash for a pinned prior version also fails closed
    assert not is_feature_version_servable("features-018", "deadbeef", "features-019")
    # a non-declared prior version (016) is not servable under 019
    assert not is_feature_version_servable("features-016", "whatever", "features-019")
