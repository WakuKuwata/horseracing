"""Feature 026: pedigree leak boundary — a target's sire/damsire features must NOT depend on the
target's own past/current result, on same-day offspring, nor on any future race (constitution II)."""

from __future__ import annotations

import pandas as pd

from horseracing_features.pedigree_features import build_pedigree_features
from tests._frames import make_frames

_PED_COLS = ["sire_win_rate", "sire_avg_finish", "sire_starts",
             "sire_dist_band_win_rate", "sire_surface_win_rate",
             "damsire_win_rate", "damsire_avg_finish"]


def _h(hid, fin, *, sire="S", damsire="DS"):
    return {"horse_id": hid, "finish_order": fin, "sire_name": sire, "damsire_name": damsire}


def _base():
    # B (sibling) past results + H target on 2008-03-01 with same-day sibling D; a future race after.
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [_h("B", 1), _h("X", 2, sire="O", damsire="O")]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [_h("B", 3), _h("X", 1, sire="O", damsire="O")]},
        {"race_id": "200801200101", "race_date": "2008-01-20", "horses": [_h("H", 1), _h("X", 2, sire="O", damsire="O")]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [_h("H", 4), _h("D", 5), _h("X", 1, sire="O", damsire="O")]},
        {"race_id": "200804010101", "race_date": "2008-04-01", "horses": [_h("B", 1), _h("H", 1), _h("X", 2, sire="O", damsire="O")]},
    ]


def _row(specs, rid="200803010101", hid="H"):
    out = build_pedigree_features(make_frames(specs), min_starts=1)
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _assert_same(a, b):
    for c in _PED_COLS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_targets_own_current_result():
    base = _row(_base())
    m = _base()
    m[3]["horses"][0]["finish_order"] = 1  # change H's OWN finish in the target race
    _assert_same(base, _row(m))


def test_invariant_to_targets_own_past_result():
    base = _row(_base())
    m = _base()
    m[2]["horses"][0]["finish_order"] = 9  # change H's OWN past race (self-excluded)
    _assert_same(base, _row(m))


def test_invariant_to_same_day_other_offspring():
    base = _row(_base())
    m = _base()
    m[3]["horses"][1]["finish_order"] = 1  # change same-day sibling D's result
    _assert_same(base, _row(m))


def test_invariant_to_future_offspring_result():
    base = _row(_base())
    m = _base()
    m[4]["horses"][0]["finish_order"] = 9  # change B's FUTURE (after target) result
    _assert_same(base, _row(m))
