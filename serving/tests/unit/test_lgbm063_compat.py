"""T015 (SC-002): lgbm-063 (features-017) stays servable under features-018 via the compat pin.

F02 is additive, so features-018 pins features-017's EXACT trained hash. This test MEASURES
lgbm-063's metadata.feature_hash (never trusts the literal, analyze V1), asserts it equals the
registry pin, and that the servability gate accepts it while rejecting a wrong hash (fail-closed).
"""

from __future__ import annotations

import json
import pathlib

import pytest
from horseracing_features.registry import (
    COMPATIBLE_PRIOR_FEATURE_VERSIONS,
    is_feature_version_servable,
)

_META = pathlib.Path(__file__).resolve().parents[3] / "artifacts/model_versions/lgbm-063/metadata.json"


@pytest.mark.skipif(not _META.exists(), reason="lgbm-063 artifact not present")
def test_features018_pin_matches_measured_lgbm063_hash():
    measured = json.loads(_META.read_text())["feature_hash"]
    pinned = COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]["features-017"]
    assert measured == pinned, "compat pin must equal lgbm-063's real trained hash (SC-002)"


@pytest.mark.skipif(not _META.exists(), reason="lgbm-063 artifact not present")
def test_lgbm063_servable_under_018_wrong_hash_fail_closed():
    measured = json.loads(_META.read_text())["feature_hash"]
    assert is_feature_version_servable("features-017", measured, "features-018")
    assert not is_feature_version_servable("features-017", "deadbeef", "features-018")
    # a non-declared prior version is not servable under 018
    assert not is_feature_version_servable("features-016", measured, "features-018")


def test_018_compat_map_only_declares_017():
    assert set(COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]) == {"features-017"}
