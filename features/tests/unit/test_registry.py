"""US2 (SC-004): metadata declared + enforced."""

from __future__ import annotations

import pytest

from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.registry import (
    IDENTIFIER_COLUMNS,
    REGISTRY,
    AvailabilityTiming,
    FeatureMeta,
    FeatureSchemaError,
    MissingPolicy,
    model_input_features,
    validate_columns,
)
from tests._frames import make_frames

_SPECS = [
    {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [{"horse_id": "H1", "finish_order": 1}]},
    {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [{"horse_id": "H1", "finish_order": 1}]},
]


def test_all_matrix_columns_registered():
    fm = assemble_feature_matrix(make_frames(_SPECS))
    for col in fm.columns:
        assert col in REGISTRY or col in IDENTIFIER_COLUMNS


def test_unregistered_column_fails_fast():
    with pytest.raises(FeatureSchemaError):
        validate_columns(["venue_code", "made_up_feature"])


def test_result_time_odds_popularity_not_features():
    assert "odds" not in REGISTRY and "popularity" not in REGISTRY
    assert "odds" not in model_input_features()
    assert "popularity" not in model_input_features()


def test_post_result_excluded_from_model_input():
    # MVP has no real post_result column; exercise the exclusion via a synthetic entry.
    REGISTRY["_synthetic_post"] = FeatureMeta("test", AvailabilityTiming.POST_RESULT, MissingPolicy.NULL)
    try:
        assert "_synthetic_post" not in model_input_features()
    finally:
        del REGISTRY["_synthetic_post"]


def test_identifiers_not_model_features():
    feats = model_input_features()
    assert "race_id" not in feats and "horse_id" not in feats
