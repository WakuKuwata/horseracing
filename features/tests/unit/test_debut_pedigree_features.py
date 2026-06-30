"""Feature 032 correctness: sire debut win rate (self-excluded, strictly-before), gating products,
NaN propagation, float64. Expected gating values are read from the 026/history outputs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from horseracing_features.debut_pedigree_features import (
    DEBUT_PEDIGREE_COLUMNS,
    build_debut_pedigree_features,
)
from horseracing_features.history import build_history_features
from horseracing_features.pedigree_features import build_pedigree_features
from tests._frames import make_frames

_KEYS = ["race_id", "horse_id"]
TARGET = "200803010101"


def _h(hid, fin, sire):
    return {"horse_id": hid, "finish_order": fin, "sire_name": sire}


def _outputs(specs, min_starts=1):
    frames = make_frames(specs)
    dp = build_debut_pedigree_features(frames, min_starts=min_starts).set_index(_KEYS)
    ped = build_pedigree_features(frames).set_index(_KEYS)
    hist = build_history_features(frames).set_index(_KEYS)
    return dp, ped, hist


def test_sire_debut_win_rate_value():
    # S's other offspring debut before target: A(win) + B(loss) → 1/2 = 0.5 for C (debut).
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            _h("A", 1, "S"), _h("Z", 2, "O")]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [_h("B", 5, "S")]},
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [_h("C", 3, "S")]},
    ]
    dp, _, _ = _outputs(specs, min_starts=1)
    assert dp.loc[(TARGET, "C"), "sire_debut_win_rate"] == 0.5  # INV-C1


def test_sire_debut_win_rate_self_excluded():
    # A debuts (win) then runs again at target; B debuts (loss). For A@target, its OWN debut win is
    # excluded → only B(loss) → 0/1 = 0.0 (not 0.5).
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [_h("A", 1, "S")]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [_h("B", 5, "S")]},
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [_h("A", 2, "S")]},
    ]
    dp, _, _ = _outputs(specs, min_starts=1)
    assert dp.loc[(TARGET, "A"), "sire_debut_win_rate"] == 0.0  # INV-C2


def test_sire_debut_min_starts_gate():
    # default min_starts=10 > 2 other-debuts → NaN (not 0-filled).
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [_h("A", 1, "S")]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [_h("B", 5, "S")]},
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [_h("C", 3, "S")]},
    ]
    dp, _, _ = _outputs(specs, min_starts=10)
    assert pd.isna(dp.loc[(TARGET, "C"), "sire_debut_win_rate"])  # INV-C5


def test_gating_debut_open_and_closed():
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            _h("A", 1, "S"), _h("X", 2, "S")]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [_h("X", 1, "S")]},
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [
            _h("C", 3, "S"),   # C: debut (is_debut=1)
            _h("A", 4, "S")]},  # A: 1 prior start (is_debut=0, is_low_history=1)
    ]
    dp, ped, hist = _outputs(specs, min_starts=1)
    # debut horse C: gate open → debut_x == sire_win_rate (INV-C3)
    sw_c = ped.loc[(TARGET, "C"), "sire_win_rate"]
    got = dp.loc[(TARGET, "C"), "debut_x_sire_win_rate"]
    assert (pd.isna(got) and pd.isna(sw_c)) or got == sw_c
    # non-debut A: debut gate closed → 0.0; low-history gate open → == sire_win_rate
    assert dp.loc[(TARGET, "A"), "debut_x_sire_win_rate"] == 0.0
    assert hist.loc[(TARGET, "A"), "is_debut"] == 0 and hist.loc[(TARGET, "A"), "is_low_history"] == 1
    sw_a = ped.loc[(TARGET, "A"), "sire_win_rate"]
    got_a = dp.loc[(TARGET, "A"), "lowhist_x_sire_win_rate"]
    assert (pd.isna(got_a) and pd.isna(sw_a)) or got_a == sw_a


def test_gating_unknown_sire_nan():
    # debut horse with no sire → sire_win_rate NaN → open-gate product NaN (INV-C4).
    specs = [
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [
            {"horse_id": "C", "finish_order": 3, "sire_name": None}]},
    ]
    dp, _, _ = _outputs(specs, min_starts=1)
    assert pd.isna(dp.loc[(TARGET, "C"), "debut_x_sire_win_rate"])


def test_all_columns_float64():
    specs = [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [_h("A", 1, "S")]},
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [_h("C", 3, "S")]},
    ]
    dp, _, _ = _outputs(specs, min_starts=1)
    for c in DEBUT_PEDIGREE_COLUMNS:
        assert dp[c].dtype == np.float64, c  # INV-C6
