"""Feature 031 correctness: leave-one-out field aggregation, own×field interactions, coverage, NaN.

Expected field values are derived from the 023 build_pace_features output (the input to 031) so the
test pins the leave-one-out/interaction logic, not the as-of style computation itself."""

from __future__ import annotations

import numpy as np
import pandas as pd

from horseracing_features.pace_features import build_pace_features
from horseracing_features.pace_scenario_features import (
    PACE_SCENARIO_COLUMNS,
    build_pace_scenario_features,
)
from tests._frames import make_frames

TARGET = "200803010101"


def _prior(hid, style, order, corner):
    return {"horse_id": hid, "running_style": style, "finish_order": order,
            "corner_orders": corner}


def _specs_styled():
    """Priors: A,B = 先行(front), C = 差し(closer). Target race = A,B,C (own style irrelevant)."""
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            _prior("A", "先行", 1, [1, 1]), _prior("B", "先行", 2, [2, 2]),
            _prior("C", "差し", 3, [8, 8]), _prior("Z", "先行", 4, [4, 4])]},
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [
            {"horse_id": "A", "finish_order": 1}, {"horse_id": "B", "finish_order": 2},
            {"horse_id": "C", "finish_order": 3}]},
    ]


def _pace_sc(specs):
    frames = make_frames(specs)
    pace = build_pace_features(frames).set_index(["race_id", "horse_id"])
    sc = build_pace_scenario_features(frames).set_index(["race_id", "horse_id"])
    return pace, sc


def test_field_front_rate_leave_one_out():
    pace, sc = _pace_sc(_specs_styled())
    # C row: field excludes self → mean of A,B as-of front_runner_rate.
    exp = np.mean([pace.loc[(TARGET, "A"), "front_runner_rate"],
                   pace.loc[(TARGET, "B"), "front_runner_rate"]])
    assert sc.loc[(TARGET, "C"), "field_front_rate_ex_self"] == exp  # INV-C1
    # pace_imbalance = field_front − field_closer (INV-C2)
    row = sc.loc[(TARGET, "C")]
    assert (row["pace_imbalance_ex_self"]
            == row["field_front_rate_ex_self"] - row["field_closer_rate_ex_self"])


def test_interactions():
    pace, sc = _pace_sc(_specs_styled())
    c_own = pace.loc[(TARGET, "C")]
    c_sc = sc.loc[(TARGET, "C")]
    # closer_setup = own.closer_rate × field_front_rate_ex_self (INV-C3)
    assert c_sc["closer_setup"] == c_own["closer_rate"] * c_sc["field_front_rate_ex_self"]
    assert c_sc["front_pressure"] == c_own["front_runner_rate"] * c_sc["field_front_rate_ex_self"]
    # style_mismatch = own.rel_corner_pos_avg − ex_self mean (INV-C4)
    others = [pace.loc[(TARGET, "A"), "rel_corner_pos_avg"],
              pace.loc[(TARGET, "B"), "rel_corner_pos_avg"]]
    exp_mm = c_own["rel_corner_pos_avg"] - np.nanmean(others)
    got = c_sc["style_mismatch"]
    assert (pd.isna(got) and pd.isna(exp_mm)) or abs(got - exp_mm) < 1e-12


def test_all_debut_field_nan_coverage_zero():
    # No priors at all → every horse's as-of style is NaN.
    specs = [{"race_id": TARGET, "race_date": "2008-03-01", "horses": [
        {"horse_id": "A", "finish_order": 1}, {"horse_id": "B", "finish_order": 2},
        {"horse_id": "C", "finish_order": 3}]}]
    _, sc = _pace_sc(specs)
    row = sc.loc[(TARGET, "A")]
    assert pd.isna(row["field_front_rate_ex_self"])      # INV-C5: no known style → NaN
    assert pd.isna(row["front_pressure"]) and pd.isna(row["closer_setup"])
    assert row["field_style_coverage"] == 0.0            # 0 known / field_size


def test_partial_coverage():
    # Only A has a prior style; B,C debut. field_style_coverage = 1/3.
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            _prior("A", "先行", 1, [1, 1]), _prior("Z", "差し", 2, [5, 5])]},
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [
            {"horse_id": "A", "finish_order": 1}, {"horse_id": "B", "finish_order": 2},
            {"horse_id": "C", "finish_order": 3}]},
    ]
    pace, sc = _pace_sc(specs)
    assert sc.loc[(TARGET, "A"), "field_style_coverage"] == 1.0 / 3.0  # INV-C6
    # B's field (ex B) sees only A's known style → equals A's front_runner_rate.
    assert sc.loc[(TARGET, "B"), "field_front_rate_ex_self"] == pace.loc[
        (TARGET, "A"), "front_runner_rate"]


def test_all_columns_float64():
    _, sc = _pace_sc(_specs_styled())
    for c in PACE_SCENARIO_COLUMNS:
        assert sc[c].dtype == np.float64, c  # INV-C7
