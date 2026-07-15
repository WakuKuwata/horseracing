"""Feature 072 (T009): projected build == full build restricted to target rows, through the real
DB->pandas path (testcontainer). The DB-free make_frames gates (test_projection_foundation) prove
the math; this proves the full builder path (loader dtypes, static merge, population slice)."""

from __future__ import annotations

import datetime

import pytest
from pandas.testing import assert_frame_equal

from horseracing_features.builder import build_feature_matrix
from tests._synth import insert_run

pytestmark = pytest.mark.integration

_KEYS = ["race_id", "horse_id"]


def test_projected_build_matches_full_restricted(session):
    # a horse with history + the target race
    for m, order in ((1, 5), (2, 3), (3, 1)):
        insert_run(session, race_id=f"20080{m}010101", race_date=datetime.date(2008, m, 1),
                   horse_id="H", finish_order=order)
    insert_run(session, race_id="200804010101", race_date=datetime.date(2008, 4, 1),
               horse_id="H", finish_order=2)  # TARGET
    insert_run(session, race_id="200804010101", race_date=datetime.date(2008, 4, 1),
               horse_id="H2", finish_order=1)  # field-mate in the target race

    R = "200804010101"
    full = build_feature_matrix(session, start_date=datetime.date(2008, 1, 1))
    proj = build_feature_matrix(session, start_date=datetime.date(2008, 1, 1),
                                target_race_ids=frozenset({R}))
    full_t = full[full["race_id"] == R].sort_values(_KEYS).reset_index(drop=True)
    proj = proj.sort_values(_KEYS).reset_index(drop=True)
    assert list(proj.columns) == list(full.columns)
    assert_frame_equal(full_t, proj, check_exact=True, check_dtype=True)
    assert set(proj["race_id"]) == {R}
