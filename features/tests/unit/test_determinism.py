"""US1 (SC-005): deterministic feature generation."""

from __future__ import annotations

import pandas as pd

from horseracing_features.builder import assemble_feature_matrix
from tests._frames import make_frames

_SPECS = [
    {"race_id": "200801010101", "race_date": "2008-01-01",
     "horses": [{"horse_id": "H1", "horse_number": 1, "finish_order": 1},
                {"horse_id": "H2", "horse_number": 2, "finish_order": 2}]},
    {"race_id": "200802010101", "race_date": "2008-02-01",
     "horses": [{"horse_id": "H1", "horse_number": 1, "finish_order": 2},
                {"horse_id": "H2", "horse_number": 2, "finish_order": 1}]},
]


def test_assemble_deterministic():
    a = assemble_feature_matrix(make_frames(_SPECS))
    b = assemble_feature_matrix(make_frames(_SPECS))
    pd.testing.assert_frame_equal(a, b)
