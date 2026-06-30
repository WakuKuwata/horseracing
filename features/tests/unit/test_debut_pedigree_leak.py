"""Feature 032 leak boundary: debut_pedigree columns must not depend on the target horse's own
current result, a same-day same-sire offspring's result, or a future offspring's debut; and must
never read the current race's finishing-position / result-status / odds raw columns."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from horseracing_features.debut_pedigree_features import (
    DEBUT_PEDIGREE_COLUMNS,
    build_debut_pedigree_features,
)
from tests._frames import make_frames

_SRC = (Path(__file__).resolve().parents[2]
        / "src" / "horseracing_features" / "debut_pedigree_features.py")
_KEYS = ["race_id", "horse_id"]
TARGET = "200803010101"


def _h(hid, fin, sire="S"):
    return {"horse_id": hid, "finish_order": fin, "sire_name": sire}


def _base():
    return [
        # prior debuts of sire S (other offspring): A win, B loss
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [_h("A", 1), _h("B", 5)]},
        # target day: C (debut, the row we check) + D (same-day other S offspring debut)
        {"race_id": TARGET, "race_date": "2008-03-01", "horses": [_h("C", 3)]},
        {"race_id": "200803010102", "race_date": "2008-03-01", "horses": [_h("D", 1)]},
        # future S offspring debut
        {"race_id": "200804010101", "race_date": "2008-04-01", "horses": [_h("E", 1)]},
    ]


def _row(specs, hid="C", rid=TARGET):
    out = build_debut_pedigree_features(make_frames(specs), min_starts=1)
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _same(a, b):
    for c in DEBUT_PEDIGREE_COLUMNS:
        assert (pd.isna(a[c]) and pd.isna(b[c])) or a[c] == b[c], c


def test_invariant_to_targets_own_result():
    base = _row(_base())
    m = _base()
    m[1]["horses"][0]["finish_order"] = 1   # change C's own finish in the target race
    _same(base, _row(m))


def test_invariant_to_same_day_other_offspring():
    base = _row(_base())
    m = _base()
    m[2]["horses"][0]["finish_order"] = 5   # change same-day D's result
    _same(base, _row(m))


def test_invariant_to_future_offspring_debut():
    base = _row(_base())
    m = _base()
    m[3]["horses"][0]["finish_order"] = 9   # change future E's debut result
    _same(base, _row(m))


def test_source_never_reads_current_race_result_columns():
    src = _SRC.read_text(encoding="utf-8")
    for tok in ("finish_order", "result_status", "odds"):
        assert tok not in src, tok  # current-race result tokens → only via 026 _runs / history
