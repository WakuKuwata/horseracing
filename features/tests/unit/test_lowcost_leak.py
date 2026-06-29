"""Feature 030 leak boundary: 030 as-of columns must not depend on the target race's own result,
same-day other races, or future races; and must never read running_style/corner_orders (II)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from horseracing_features.lowcost_features import OUTPUT_COLUMNS, build_lowcost_features
from tests._frames import make_frames

_SRC = Path(__file__).resolve().parents[2] / "src" / "horseracing_features" / "lowcost_features.py"


def _h(hid, fin, *, jk="J1", tr="T1"):
    return {"horse_id": hid, "finish_order": fin, "jockey_id": jk, "trainer_id": tr}


def _base():
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [_h("H", 1), _h("X", 2, jk="JX")]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [_h("H", 3), _h("X", 1, jk="JX")]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            _h("H", 4), _h("D", 5), _h("X", 1, jk="JX")]},     # target day (H + same-day D)
        {"race_id": "200804010101", "race_date": "2008-04-01", "horses": [_h("H", 1), _h("X", 2, jk="JX")]},
    ]


def _row(specs, rid="200803010101", hid="H"):
    out = build_lowcost_features(make_frames(specs), min_starts=1)
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _same(a, b):
    for c in OUTPUT_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_targets_own_result():
    base = _row(_base())
    m = _base()
    m[2]["horses"][0]["finish_order"] = 1   # change H's own finish in the target race
    _same(base, _row(m))


def test_invariant_to_same_day_other_race():
    base = _row(_base())
    m = _base()
    m[2]["horses"][1]["finish_order"] = 1   # change same-day D's result
    _same(base, _row(m))


def test_invariant_to_future_race():
    base = _row(_base())
    m = _base()
    m[3]["horses"][0]["finish_order"] = 9   # change a future race result
    _same(base, _row(m))


def test_source_never_reads_running_style_or_corner():
    src = _SRC.read_text(encoding="utf-8")
    assert "running_style" not in src and "corner_orders" not in src  # result-derived → never used
