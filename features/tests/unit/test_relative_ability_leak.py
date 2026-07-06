"""Feature 059 leak boundary: relative_ability columns (computed over the full build_asof_features
chain) must not depend on the target race's own result, a co-runner's current-race result, same-day
other races, or future races; and the module must never read any current-race result/odds column
(only the merged as-of ability frame + entry_status)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from horseracing_features.materialize import build_asof_features
from horseracing_features.relative_ability_features import RELATIVE_ABILITY_COLUMNS
from tests._frames import make_frames

_SRC = (Path(__file__).resolve().parents[2]
        / "src" / "horseracing_features" / "relative_ability_features.py")
TARGET = "200803010101"


def _h(hid, order):
    return {"horse_id": hid, "finish_order": order}


def _base():
    return [
        # priors establish as-of win_rate/ability for A (strong), B, C
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            _h("A", 1), _h("B", 2), _h("C", 3)]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
            _h("A", 1), _h("B", 3), _h("C", 2)]},
        # TARGET (own current results must not matter)
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [
            _h("A", 1), _h("B", 2), _h("C", 3)]},
        # same-day OTHER race
        {"race_id": "200803010102", "race_date": "2008-03-01", "horses": [
            _h("A", 5), _h("M", 1)]},
        # future race
        {"race_id": "200804010101", "race_date": "2008-04-01", "horses": [
            _h("A", 6), _h("B", 5)]},
    ]


def _row(specs, hid="C", rid=TARGET):
    out = build_asof_features(make_frames(specs))
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _same(a, b):
    for c in RELATIVE_ABILITY_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_targets_own_result():
    base = _row(_base())
    m = _base()
    m[2]["horses"][2]["finish_order"] = 1   # C in TARGET wins instead
    _same(base, _row(m))


def test_invariant_to_co_runner_current_result():
    base = _row(_base())
    m = _base()
    m[2]["horses"][0]["finish_order"] = 18  # A's current result (feeds C's field ability)
    _same(base, _row(m))


def test_invariant_to_same_day_other_race():
    base = _row(_base())
    m = _base()
    m[3]["horses"][0]["finish_order"] = 1   # same-day other race result
    _same(base, _row(m))


def test_invariant_to_future_race():
    base = _row(_base())
    m = _base()
    m[4]["horses"][0]["finish_order"] = 1   # future race result
    _same(base, _row(m))


def test_source_never_reads_current_race_result_or_odds_columns():
    # Strip the module docstring (its prose describes the leak boundary using these very terms);
    # the CODE body must not reference any current-race result / odds column.
    parts = _SRC.read_text(encoding="utf-8").split('"""')
    code = "".join(parts[2:]) if len(parts) >= 3 else parts[0]
    for tok in ("finish_order", "result_status", "corner_orders", "running_style",
                "last_3f", "first_3f", "finish_time", "odds", "popularity"):
        assert tok not in code, tok  # inputs are as-of ability cols + entry_status only
