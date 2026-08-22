"""Feature 097 (T005/T006/T008/T015): early-mid pace — hand-computed values, missingness,
1200m identity, independence from first_3f, and projection parity.

Values are chosen at a realistic scale (tens of seconds) and asymmetric so mean and min differ;
a test that only checked finiteness would not have caught the 090-class bug (INV-EM8).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from horseracing_features.early_mid_pace_features import (
    EARLY_MID_PACE_COLUMNS,
    build_early_mid_pace_features,
    early_mid_runs,
)
from horseracing_features.pace_features import _pace_runs
from tests._frames import make_frames

_T = "200803010101"


def _h(hid, ft, l3, **kw):
    return {"horse_id": hid, "finish_time": ft, "last_3f": l3, "finish_order": kw.pop("fo", 1), **kw}


def _specs():
    # R1: H em=94.0-34.0=60.0, X em=95.0-36.0=59.0 -> race mean 59.5 -> rel_em(H)=+0.5
    # R2: H em=96.0-35.0=61.0, Y em=96.0-37.0=59.0 -> race mean 60.0 -> rel_em(H)=+1.0
    # target R3: H's as-of avg = (0.5+1.0)/2 = 0.75 ; best = min = 0.5
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01",
         "horses": [_h("H", 94.0, 34.0), _h("X", 95.0, 36.0, fo=2)]},
        {"race_id": "200802010101", "race_date": "2008-02-01",
         "horses": [_h("H", 96.0, 35.0), _h("Y", 96.0, 37.0, fo=2)]},
        {"race_id": _T, "race_date": "2008-03-01",
         "horses": [_h("H", 93.0, 33.0), _h("Z", 97.0, 38.0, fo=2)]},
    ]


def _target_row(df, hid="H"):
    return df[(df.race_id == _T) & (df.horse_id == hid)].iloc[0]


def test_hand_computed_values_exact():
    r = _target_row(build_early_mid_pace_features(make_frames(_specs())))
    assert r.asof_rel_early_mid_avg == 0.75
    assert r.asof_rel_early_mid_best == 0.5


def test_columns_and_dtypes():
    df = build_early_mid_pace_features(make_frames(_specs()))
    assert list(df.columns) == ["race_id", "horse_id", *EARLY_MID_PACE_COLUMNS]
    for c in EARLY_MID_PACE_COLUMNS:
        assert df[c].dtype == np.float64


def test_run_level_relative_uses_finishers_only():
    """A stopped rival is excluded from the race mean (in-race relative over FINISHERS)."""
    specs = _specs()
    specs[0]["horses"].append(
        {"horse_id": "S", "finish_time": 50.0, "last_3f": 30.0, "result_status": "stopped"})
    runs = early_mid_runs(make_frames(specs))
    h = runs[(runs.race_id == "200801010101") & (runs.horse_id == "H")].iloc[0]
    assert h.rel_em == 0.5          # unchanged: mean over H and X only


# --- missingness (INV-EM4) ---------------------------------------------------------------------

def test_no_past_runs_is_nan():
    r = _target_row(build_early_mid_pace_features(make_frames(_specs())), hid="Z")
    assert pd.isna(r.asof_rel_early_mid_avg) and pd.isna(r.asof_rel_early_mid_best)


def test_broken_input_em_nonpositive_is_nan_not_zero():
    """finish_time <= last_3f is a broken row: it must NOT produce 0 or a negative pace."""
    specs = _specs()
    specs[0]["horses"][0] = _h("H", 30.0, 34.0)        # em = -4 -> NaN -> run excluded
    r = _target_row(build_early_mid_pace_features(make_frames(specs)))
    assert r.asof_rel_early_mid_avg == 1.0               # only R2 survives
    assert r.asof_rel_early_mid_best == 1.0


def test_missing_last3f_run_is_excluded_not_zero_filled():
    specs = _specs()
    specs[1]["horses"][0] = {"horse_id": "H", "finish_time": 96.0, "last_3f": None,
                             "finish_order": 1}
    r = _target_row(build_early_mid_pace_features(make_frames(specs)))
    assert r.asof_rel_early_mid_avg == 0.5 and r.asof_rel_early_mid_best == 0.5
    assert not (r.asof_rel_early_mid_avg == 0.0)


# --- INV-EM7: 1200m identity / INV-EM2: independence ---------------------------------------------

def test_1200m_identity_with_real_first3f():
    """At 1200m, rel_em must equal rel_first3f exactly when first_3f == finish_time − last_3f
    (the same identity the production backfill uses)."""
    specs = _specs()
    for sp in specs:
        sp["distance"] = 1200
        for h in sp["horses"]:
            h["first_3f"] = h["finish_time"] - h["last_3f"]
    fr = make_frames(specs)
    em = early_mid_runs(fr).set_index(["race_id", "horse_id"])["rel_em"]
    f3 = _pace_runs(fr).set_index(["race_id", "horse_id"])["rel_first3f"]
    both = em.notna() & f3.notna()
    assert both.any()
    assert np.allclose(em[both].to_numpy(), f3[both].to_numpy(), rtol=0, atol=1e-12)


def test_independent_of_first3f_column():
    specs_with = _specs()
    for sp in specs_with:
        for h in sp["horses"]:
            h["first_3f"] = 35.0
    a = build_early_mid_pace_features(make_frames(specs_with))
    b = build_early_mid_pace_features(make_frames(_specs()))      # first_3f absent (NaN)
    assert_frame_equal(a, b, check_exact=True)


# --- T015: Feature 072 projection parity --------------------------------------------------------

def test_projection_parity():
    fr = make_frames(_specs())
    full = build_early_mid_pace_features(fr)
    proj = build_early_mid_pace_features(fr, target_race_ids=frozenset({_T}))
    exp = full[full.race_id == _T].reset_index(drop=True)
    assert_frame_equal(proj.sort_values("horse_id").reset_index(drop=True),
                       exp.sort_values("horse_id").reset_index(drop=True),
                       check_exact=True, check_dtype=True)
