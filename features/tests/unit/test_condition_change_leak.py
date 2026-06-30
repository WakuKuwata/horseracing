"""Feature 033 leak boundary: condition_change columns must not depend on the target horse's own
current result, or same-day/future races; and must never read the current race's finishing-position
/ result-status / odds raw columns."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from horseracing_features.condition_change_features import (
    CONDITION_CHANGE_COLUMNS,
    build_condition_change_features,
)
from tests._frames import make_frames

_SRC = (Path(__file__).resolve().parents[2]
        / "src" / "horseracing_features" / "condition_change_features.py")
_KEYS = ["race_id", "horse_id"]
TARGET = "200803010101"


def _r(hid, rid, date, *, distance=1600, fin=1, corner=None):
    h = {"horse_id": hid, "finish_order": fin}
    if corner is not None:
        h["corner_orders"] = corner
    return {"race_id": rid, "race_date": date, "distance": distance, "horses": [h]}


def _base():
    return [
        _r("H", "200712010101", "2007-12-01", distance=1600, fin=1, corner=[2, 2]),
        _r("H", "200801010101", "2008-01-01", distance=1600, fin=1, corner=[2, 2]),
        _r("H", TARGET, "2008-03-01", distance=2000, fin=3, corner=[5, 5]),
        # same-day other race + future race (same horse H to also exercise prior-race lookups)
        _r("H", "200804010101", "2008-04-01", distance=1800, fin=1, corner=[1, 1]),
    ]


def _row(specs, hid="H", rid=TARGET):
    out = build_condition_change_features(make_frames(specs))
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _same(a, b):
    for c in CONDITION_CHANGE_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_targets_own_result():
    base = _row(_base())
    m = _base()
    m[2]["horses"][0]["finish_order"] = 1
    m[2]["horses"][0]["corner_orders"] = [1, 1]
    _same(base, _row(m))


def test_invariant_to_future_race():
    base = _row(_base())
    m = _base()
    m[3]["horses"][0]["finish_order"] = 9
    m[3]["distance"] = 1200          # future race condition change must not leak back
    _same(base, _row(m))


def test_source_never_reads_current_race_result_columns():
    src = _SRC.read_text(encoding="utf-8")
    for tok in ("finish_order", "result_status", "odds"):
        assert tok not in src, tok
