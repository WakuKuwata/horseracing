"""Feature 033 correctness: condition-change base, signed hinges, ability interactions, NaN, float64.
Ability interaction expected values are read from the 023 build_pace_features output."""

from __future__ import annotations

import numpy as np
import pandas as pd

from horseracing_features.condition_change_features import (
    CONDITION_CHANGE_COLUMNS,
    build_condition_change_features,
)
from horseracing_features.pace_features import build_pace_features
from tests._frames import make_frames

_KEYS = ["race_id", "horse_id"]
TARGET = "200803010101"


def _run(hid, rid, date, *, distance=1600, track="芝", going="良", fin=1, corner=None):
    race = {"race_id": rid, "race_date": date, "distance": distance, "track_type": track,
            "going": going, "horses": [{"horse_id": hid, "finish_order": fin}]}
    if corner is not None:
        race["horses"][0]["corner_orders"] = corner
    return race


def _build(specs):
    frames = make_frames(specs)
    cc = build_condition_change_features(frames).set_index(_KEYS)
    pace = build_pace_features(frames).set_index(_KEYS)
    return cc, pace


def test_distance_extension():
    specs = [
        _run("H", "200801010101", "2008-01-01", distance=1600),
        _run("H", TARGET, "2008-03-01", distance=2000),
    ]
    cc, _ = _build(specs)
    row = cc.loc[(TARGET, "H")]
    assert row["dist_change"] == 400.0       # INV-C1
    assert row["dist_extension"] == 400.0
    assert row["dist_shortening"] == 0.0


def test_distance_shortening():
    specs = [
        _run("H", "200801010101", "2008-01-01", distance=2000),
        _run("H", TARGET, "2008-03-01", distance=1400),
    ]
    cc, _ = _build(specs)
    row = cc.loc[(TARGET, "H")]
    assert row["dist_change"] == -600.0      # INV-C2
    assert row["dist_shortening"] == 600.0
    assert row["dist_extension"] == 0.0


def test_surface_and_going_change():
    specs = [
        _run("H", "200801010101", "2008-01-01", track="芝", going="良"),
        _run("H", TARGET, "2008-03-01", track="ダ", going="重"),
    ]
    cc, _ = _build(specs)
    row = cc.loc[(TARGET, "H")]
    assert row["surface_switch"] == 1.0      # 芝→ダ (INV-C3)
    assert row["going_change"] == 2.0        # 良(0)→重(2)


def test_ability_interaction():
    # H has a prior race with a defined last_3f/corner so rel_last3f_best/rel_time_avg exist as-of.
    specs = [
        _run("H", "200712010101", "2007-12-01", distance=1600, fin=1, corner=[2, 2]),
        _run("H", "200801010101", "2008-01-01", distance=1600, fin=1, corner=[2, 2]),
        _run("H", TARGET, "2008-03-01", distance=2000),
    ]
    cc, pace = _build(specs)
    row = cc.loc[(TARGET, "H")]
    rl = pace.loc[(TARGET, "H"), "rel_last3f_best"]
    exp = row["dist_extension"] * (-rl)
    got = row["dist_ext_x_closing"]
    assert (pd.isna(got) and pd.isna(exp)) or abs(got - exp) < 1e-9   # INV-C4


def test_debut_all_nan():
    specs = [_run("H", TARGET, "2008-03-01", distance=2000)]
    cc, _ = _build(specs)
    row = cc.loc[(TARGET, "H")]
    for c in ("dist_change", "surface_switch", "going_change",
              "dist_extension", "dist_shortening", "dist_ext_x_closing", "dist_short_x_speed"):
        assert pd.isna(row[c]), c                                     # INV-C5


def test_ability_nan_when_no_pace_history():
    # prior race exists (so dist_change defined) but the prior has no finish info → rel_* NaN as-of.
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "distance": 1600,
         "horses": [{"horse_id": "H", "entry_status": "started", "result_status": "cancel"}]},
        _run("H", TARGET, "2008-03-01", distance=2000),
    ]
    cc, _ = _build(specs)
    row = cc.loc[(TARGET, "H")]
    assert pd.isna(row["dist_ext_x_closing"])                         # INV-C6


def test_all_columns_float64():
    specs = [
        _run("H", "200801010101", "2008-01-01", distance=1600),
        _run("H", TARGET, "2008-03-01", distance=2000),
    ]
    cc, _ = _build(specs)
    for c in CONDITION_CHANGE_COLUMNS:
        assert cc[c].dtype == np.float64, c                           # INV-C7
