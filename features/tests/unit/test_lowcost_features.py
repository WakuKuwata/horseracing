"""Feature 030: low-cost feature correctness (斤量 static + as-of place/human/course/change)."""

from __future__ import annotations

import pandas as pd

from horseracing_features.lowcost_features import build_lowcost_features
from horseracing_features.static_features import build_static_features
from tests._frames import make_frames


def _h(hid, fin, *, jw=56.0, jk="J1", tr="T1"):
    return {"horse_id": hid, "finish_order": fin, "jockey_weight": jw,
            "jockey_id": jk, "trainer_id": tr}


def _hist():
    # H: R1 win(1), R2 3着, R3 5着(斤量54,騎手J1) → target R4(斤量56,騎手J2). X filler.
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
            _h("H", 1), _h("X", 2, jk="JX")]},
        {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
            _h("H", 3), _h("X", 1, jk="JX")]},
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            _h("H", 5, jw=54.0, jk="J1"), _h("X", 1, jk="JX")]},
        {"race_id": "200804010101", "race_date": "2008-04-01", "horses": [
            _h("H", 2, jw=56.0, jk="J2"), _h("X", 1, jk="JX")]},
    ]


def _srow(frames, rid, hid):
    out = build_static_features(frames)
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def _arow(frames, rid, hid, **kw):
    out = build_lowcost_features(frames, **kw)
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def test_carried_weight_static():
    # H at R4: jockey_weight 56, body 460 → ratio 56/460. rel = 56 − mean(56, X=56)=0.
    r = _srow(make_frames(_hist()), "200804010101", "H")
    assert r.carried_weight == 56.0
    assert round(r.carried_weight_ratio, 5) == round(56.0 / 460.0, 5)
    assert r.carried_weight_rel == 0.0  # both H and X carry 56 → field mean 56
    assert r.race_month == 4.0 and r.race_season == 1.0  # April = spring(1)


def test_carried_weight_change_asof():
    r = _arow(make_frames(_hist()), "200804010101", "H")
    assert r.carried_weight_change == 2.0  # 56 (R4) − 54 (R3 prev started)


def test_carried_weight_change_debut_nan():
    r = _arow(make_frames(_hist()), "200801010101", "H")
    assert pd.isna(r.carried_weight_change)


def test_place_and_show_rate():
    # H at R4: prior finished R1(1),R2(3),R3(5). top2={R1}=1/3, top3={R1,R2}=2/3.
    r = _arow(make_frames(_hist()), "200804010101", "H")
    assert round(r.place_rate, 4) == 0.3333
    assert round(r.show_rate, 4) == 0.6667


def test_jockey_change():
    # R4 jockey J2 vs R3 (prev started) jockey J1 → changed.
    r = _arow(make_frames(_hist()), "200804010101", "H")
    assert r.jockey_change == 1.0
    # debut → NaN
    assert pd.isna(_arow(make_frames(_hist()), "200801010101", "H").jockey_change)


def test_venue_rate_conditional():
    # all H races share venue 05 (default). At R4, prior venue-05 starts=3 → with min_starts=1, rate set.
    r = _arow(make_frames(_hist()), "200804010101", "H", min_starts=1)
    assert pd.notna(r.venue_win_rate) and pd.notna(r.venue_place_rate)
    # debut → NaN even at min_starts=1
    assert pd.isna(_arow(make_frames(_hist()), "200801010101", "H", min_starts=1).venue_win_rate)


def test_all_asof_columns_float64():
    out = build_lowcost_features(make_frames(_hist()))
    from horseracing_features.lowcost_features import OUTPUT_COLUMNS
    for c in OUTPUT_COLUMNS:
        assert str(out[c].dtype) == "float64", (c, out[c].dtype)
