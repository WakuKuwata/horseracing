"""Feature 031 leak boundary: pace_scenario columns must not depend on the target race's own result,
a co-runner's current-race result, same-day other races, or future races; and must never read the
current race's running_style/corner_orders/finish_order/result_status (only the 023 as-of output)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from horseracing_features.pace_scenario_features import (
    PACE_SCENARIO_COLUMNS,
    build_pace_scenario_features,
)
from tests._frames import make_frames

_SRC = (Path(__file__).resolve().parents[2]
        / "src" / "horseracing_features" / "pace_scenario_features.py")
TARGET = "200803010101"


def _prior(hid, style, order, corner):
    return {"horse_id": hid, "running_style": style, "finish_order": order,
            "corner_orders": corner}


def _base():
    return [
        # priors establish as-of style for A,B (front) and C (closer)
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            _prior("A", "先行", 1, [1, 1]), _prior("B", "先行", 2, [2, 2]),
            _prior("C", "差し", 3, [8, 8])]},
        # TARGET (own current results must not matter)
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [
            {"horse_id": "A", "finish_order": 1, "corner_orders": [1, 1]},
            {"horse_id": "B", "finish_order": 2, "corner_orders": [2, 2]},
            {"horse_id": "C", "finish_order": 3, "corner_orders": [9, 9]}]},
        # same-day OTHER race
        {"race_id": "200803010102", "race_date": "2008-03-01", "horses": [
            _prior("A", "差し", 5, [7, 7]), _prior("M", "先行", 1, [1, 1])]},
        # future race
        {"race_id": "200804010101", "race_date": "2008-04-01", "horses": [
            _prior("A", "差し", 6, [6, 6]), _prior("B", "差し", 5, [5, 5])]},
    ]


def _row(specs, hid="C", rid=TARGET):
    out = build_pace_scenario_features(make_frames(specs))
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _same(a, b):
    for c in PACE_SCENARIO_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_targets_own_result():
    base = _row(_base())
    m = _base()
    h = m[1]["horses"][2]  # C in TARGET
    h["finish_order"] = 1
    h["corner_orders"] = [1, 1]
    h["running_style"] = "逃げ"
    _same(base, _row(m))


def test_invariant_to_co_runner_current_result():
    base = _row(_base())
    m = _base()
    a = m[1]["horses"][0]  # A in TARGET (a co-runner whose as-of style feeds C's field)
    a["finish_order"] = 18
    a["corner_orders"] = [18, 18]
    a["running_style"] = "差し"
    _same(base, _row(m))


def test_invariant_to_same_day_other_race():
    base = _row(_base())
    m = _base()
    m[2]["horses"][0]["finish_order"] = 1   # same-day other race result
    m[2]["horses"][0]["corner_orders"] = [1, 1]
    _same(base, _row(m))


def test_invariant_to_future_race():
    base = _row(_base())
    m = _base()
    m[3]["horses"][0]["finish_order"] = 1   # future race result
    m[3]["horses"][0]["running_style"] = "先行"
    _same(base, _row(m))


def test_source_never_reads_current_race_result_columns():
    src = _SRC.read_text(encoding="utf-8")
    for tok in ("running_style", "corner_orders", "finish_order", "result_status"):
        assert tok not in src, tok  # current-race result tokens → only via build_pace_features
