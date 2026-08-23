"""F02-skip projected build: skipping an optional LEAF block must be byte-identical on every column
it keeps, drop exactly the skipped block's columns, and never short a model's ``wanted`` columns.
"""

from __future__ import annotations

import numpy as np
import pytest
from pandas.testing import assert_frame_equal

from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.materialize import (
    _OPTIONAL_LEAF_BLOCKS,
    build_asof_features,
    skip_blocks_for_wanted,
)
from horseracing_features.pm_core_strength import PM_CORE_STRENGTH_COLUMNS
from horseracing_features.registry import FeatureSchemaError, materialized_columns
from horseracing_features.schema import ALL_COLUMNS
from tests._frames import make_frames


def _frames():
    specs = []
    for i in range(8):
        specs.append({
            "race_id": f"R{i:02d}", "race_date": f"2020-0{(i % 8) + 1}-05",
            "horses": [
                {"horse_id": "a", "finish_order": (i % 4) + 1, "odds": 1.5 + (i % 3)},
                {"horse_id": "b", "finish_order": ((i + 1) % 4) + 1, "odds": 3.0 + (i % 2)},
                {"horse_id": "c", "finish_order": ((i + 2) % 4) + 1, "odds": 6.0},
            ],
        })
    specs.append({
        "race_id": "RT", "race_date": "2021-01-01",
        "horses": [{"horse_id": "a", "odds": 2.0}, {"horse_id": "b", "odds": 4.0}],
    })
    return make_frames(specs)


def test_skip_f02_is_byte_identical_on_kept_columns():
    frames = _frames()
    full = build_asof_features(frames)
    proj = build_asof_features(frames, skip_blocks=frozenset({"pm_core_strength"}))
    # exactly the F02 columns are dropped
    assert set(full.columns) - set(proj.columns) == set(PM_CORE_STRENGTH_COLUMNS)
    # every kept column is byte-identical (row order + dtype too)
    assert_frame_equal(full[proj.columns], proj, check_exact=True, check_dtype=True)


def test_assemble_projected_matches_full_sliced():
    frames = _frames()
    full = assemble_feature_matrix(frames)  # wanted=None -> full fixed schema
    # a model that reads no F02 column: everything except pm_core
    wanted = frozenset(c for c in materialized_columns() if c not in PM_CORE_STRENGTH_COLUMNS)
    proj = assemble_feature_matrix(frames, wanted=wanted)
    assert set(full.columns) - set(proj.columns) == set(PM_CORE_STRENGTH_COLUMNS)
    assert_frame_equal(full[proj.columns], proj, check_exact=True, check_dtype=True)
    assert wanted.issubset(proj.columns)


def test_wanting_an_f02_column_keeps_the_whole_block():
    # a candidate model that reads one F02 column must get the FULL block (all-or-nothing).
    wanted = frozenset({"asof_pm_support_last", "win_rate"})
    assert skip_blocks_for_wanted(wanted) == frozenset()
    frames = _frames()
    proj = assemble_feature_matrix(frames, wanted=wanted)
    for c in PM_CORE_STRENGTH_COLUMNS:
        assert c in proj.columns


def test_wanted_none_builds_full_schema():
    assert skip_blocks_for_wanted(None) == frozenset()
    proj = assemble_feature_matrix(_frames())
    assert set(proj.columns) == set(ALL_COLUMNS)


def test_unknown_skip_block_raises():
    with pytest.raises(ValueError, match="unknown skip_blocks"):
        build_asof_features(_frames(), skip_blocks=frozenset({"not_a_block"}))


def test_registry_lists_only_true_f02_columns():
    # guards against a future edit registering a non-leaf block silently.
    assert set(_OPTIONAL_LEAF_BLOCKS) == {"pm_core_strength"}
    assert set(_OPTIONAL_LEAF_BLOCKS["pm_core_strength"]) == set(PM_CORE_STRENGTH_COLUMNS)


def test_wanted_missing_from_schema_is_fail_closed():
    # a wanted column that the projected matrix can never provide must raise, not serve short.
    frames = _frames()
    # ask for a real column but simulate a skip that removes it by requesting only F02-less set
    # while (hypothetically) needing an F02 column that got skipped is covered above; here we ask
    # for a bogus column to exercise the fail-closed guard.
    bogus = frozenset({"win_rate", "definitely_not_a_column"})
    with pytest.raises(FeatureSchemaError, match="wanted columns not in projected matrix"):
        assemble_feature_matrix(frames, wanted=bogus)


def test_projected_values_are_finite_where_expected():
    frames = _frames()
    proj = build_asof_features(frames, skip_blocks=frozenset({"pm_core_strength"}))
    # 'a' has history, so win_rate is present & finite at target race RT
    rt = proj[(proj["race_id"] == "RT") & (proj["horse_id"] == "a")]
    assert not rt.empty
    assert np.isfinite(rt.iloc[0]["win_rate"])
