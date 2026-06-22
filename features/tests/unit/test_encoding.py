"""US3 (SC-006): target encoding train-only, unknown -> default."""

from __future__ import annotations

import datetime

import pandas as pd

from horseracing_features.encoding import fit_target_encoding
from tests._frames import make_frames

_SPECS = [
    # train period (2008): JA wins, JB loses
    {"race_id": "200801010101", "race_date": "2008-01-01",
     "horses": [{"horse_id": "H1", "jockey_id": "JA", "finish_order": 1},
                {"horse_id": "H2", "jockey_id": "JB", "finish_order": 2}]},
    # after cutoff (2009): reversed — must NOT influence the encoding
    {"race_id": "200901010101", "race_date": "2009-01-01",
     "horses": [{"horse_id": "H3", "jockey_id": "JA", "finish_order": 2},
                {"horse_id": "H4", "jockey_id": "JB", "finish_order": 1}]},
]


def test_encoding_uses_only_pre_cutoff():
    enc = fit_target_encoding(make_frames(_SPECS), train_cutoff=datetime.date(2009, 1, 1),
                              category="jockey_id")
    assert enc.mapping["JA"] == 1.0   # 2008 only: JA won
    assert enc.mapping["JB"] == 0.0   # 2008 only: JB lost
    assert "JC" not in enc.mapping


def test_unknown_category_uses_default():
    enc = fit_target_encoding(make_frames(_SPECS), train_cutoff=datetime.date(2009, 1, 1),
                              category="jockey_id")
    # train overall win mean = (1 + 0) / 2 = 0.5
    assert enc.default == 0.5
    out = enc.transform(pd.Series(["UNKNOWN_JOCKEY"]))
    assert out.iloc[0] == 0.5
