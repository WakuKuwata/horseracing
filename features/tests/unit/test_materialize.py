"""US4 (FR-014): materialize round-trip preserves the matrix exactly."""

from __future__ import annotations

import pandas as pd

from horseracing_features.builder import assemble_feature_matrix
from tests._frames import make_frames

_SPECS = [
    {"race_id": "200801010101", "race_date": "2008-01-01",
     "horses": [{"horse_id": "H1", "horse_number": 1, "finish_order": 1}]},
    {"race_id": "200802010101", "race_date": "2008-02-01",
     "horses": [{"horse_id": "H1", "horse_number": 1, "finish_order": 1},  # has history
                {"horse_id": "H2", "horse_number": 2, "finish_order": 2}]},  # debut -> NaN features
]


def test_parquet_roundtrip_fidelity(tmp_path):
    fm = assemble_feature_matrix(make_frames(_SPECS))
    assert fm["avg_finish"].isna().any()  # ensure NaN present to test preservation

    path = tmp_path / "fm.parquet"
    fm.to_parquet(path, index=False)
    reloaded = pd.read_parquet(path)

    assert list(reloaded.columns) == list(fm.columns)  # column order preserved
    pd.testing.assert_frame_equal(fm, reloaded)         # dtypes + NaN(≠0) preserved
