"""Polish (SC-001 via real DB): as-of leak check through the full DB->pandas path."""

from __future__ import annotations

import datetime

import pytest

from horseracing_features.builder import build_feature_matrix
from tests._synth import insert_run

pytestmark = pytest.mark.integration


def test_asof_excludes_future_via_real_db(session):
    insert_run(session, race_id="200801010101", race_date=datetime.date(2008, 1, 1),
               horse_id="H", finish_order=5)
    insert_run(session, race_id="200802010101", race_date=datetime.date(2008, 2, 1),
               horse_id="H", finish_order=3)  # target
    insert_run(session, race_id="200803010101", race_date=datetime.date(2008, 3, 1),
               horse_id="H", finish_order=1)  # future (in pool, must be excluded by as-of)

    # end_date=None -> the future race IS loaded into the pool; as-of must still exclude it.
    fm = build_feature_matrix(session, start_date=datetime.date(2008, 1, 1))
    row = fm[(fm.race_id == "200802010101") & (fm.horse_id == "H")].iloc[0]

    assert row.career_starts == 1     # only the 2008-01 race, not the future one
    assert row.avg_finish == 5.0
    assert row.prev_finish == 5
    # the future race's own row sees the 2008-02 race as history
    future = fm[(fm.race_id == "200803010101") & (fm.horse_id == "H")].iloc[0]
    assert future.career_starts == 2
