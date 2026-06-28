"""Feature 026: sire / damsire aptitude correctness (self-excluded, conditional, debut, damsire)."""

from __future__ import annotations

import pandas as pd

from horseracing_features.pedigree_features import build_pedigree_features
from tests._frames import make_frames


def _h(hid, fin, *, sire="S", damsire="DS"):
    return {"horse_id": hid, "finish_order": fin, "sire_name": sire, "damsire_name": damsire}


def _f(fin):  # filler horse with an unrelated sire AND damsire (not in S / DS pools)
    return _h("F", fin, sire="OTHER", damsire="ODS")


def _specs():
    # sire S offspring: B (sibling), H (target, has its own past win), D (debut, same-day as H)
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "distance": 1600, "horses": [
            _h("B", 1), _f(2)]},          # B wins @1600 芝
        {"race_id": "200802010101", "race_date": "2008-02-01", "distance": 1600, "horses": [
            _h("B", 5), _f(1)]},          # B loses @1600 芝
        {"race_id": "200801150101", "race_date": "2008-01-15", "distance": 2000, "horses": [
            _h("H", 1), _f(2)]},          # H's OWN past win @2000
        {"race_id": "200803010101", "race_date": "2008-03-01", "distance": 1600, "horses": [
            _h("H", 3), _h("D", 4), _f(1)]},  # TARGET day: H + debut D + F
    ]


def _row(frames, rid, hid, **kw):
    out = build_pedigree_features(frames, **kw)
    return out[(out.race_id == rid) & (out.horse_id == hid)].iloc[0]


def test_sire_overall_self_excluded():
    # H at target: other offspring of S (= B only, H itself excluded) before 2008-03-01.
    # B: win@R1, loss@R2 -> wins 1, cnt 2, finsum 6 -> win_rate .5, avg_finish 3.0, starts 2.
    r = _row(make_frames(_specs()), "200803010101", "H")
    assert r.sire_win_rate == 0.5
    assert r.sire_avg_finish == 3.0
    assert r.sire_starts == 2.0   # NOT 3 — H's own past win is excluded (no double count)


def test_debut_horse_gets_sire_from_other_offspring():
    # D is a debut (no own past) but a child of S -> sire features from B + H (the other offspring).
    # before 2008-03-01 excl D: B(win,loss) + H(win) -> wins 2, cnt 3 -> .667, starts 3.
    r = _row(make_frames(_specs()), "200803010101", "D")
    assert round(r.sire_win_rate, 4) == 0.6667
    assert r.sire_starts == 3.0
    assert pd.notna(r.sire_win_rate)   # SC-001: value despite empty own record


def test_sire_conditional_dist_and_surface():
    # min_starts=1 so the thin synthetic conditional rates resolve. H @ target is 1600 芝 (band 1).
    # other offspring in band1/芝 before = B (R1 win, R2 loss) -> .5.
    r = _row(make_frames(_specs()), "200803010101", "H", min_starts=1)
    assert r.sire_dist_band_win_rate == 0.5
    assert r.sire_surface_win_rate == 0.5


def test_conditional_min_starts_gate_nan():
    # with default min_starts=10 the (1-start) conditional bucket is below threshold -> NaN.
    r = _row(make_frames(_specs()), "200803010101", "H")
    assert pd.isna(r.sire_dist_band_win_rate)
    assert pd.isna(r.sire_surface_win_rate)


def test_unknown_sire_is_nan_not_zero():
    # F has sire "OTHER" with no other offspring before its target -> NaN (not 0).
    specs = [
        {"race_id": "200803010101", "race_date": "2008-03-01", "horses": [
            {"horse_id": "Z", "finish_order": 1, "sire_name": None},
            {"horse_id": "F", "finish_order": 2, "sire_name": "OTHER"}]},
    ]
    r = _row(make_frames(specs), "200803010101", "Z")
    assert pd.isna(r.sire_win_rate) and pd.isna(r.sire_starts)  # sire unknown -> NaN, 0 not injected


def test_pedigree_columns_are_stable_float64():
    # all pedigree columns (incl. the sire_starts count) must be float64 regardless of pool, so a
    # window with no unknown-sire rows can't drift int64 vs the full pool's float64 (parity, A2).
    out = build_pedigree_features(make_frames(_specs()))
    for c in ["sire_win_rate", "sire_avg_finish", "sire_starts",
              "sire_dist_band_win_rate", "sire_surface_win_rate",
              "damsire_win_rate", "damsire_avg_finish"]:
        assert str(out[c].dtype) == "float64", (c, out[c].dtype)


def test_damsire_overall_self_excluded():
    # all of B/H/D share damsire DS. For H: other (B+? ) before target excluding H.
    # B(win,loss) before 2008-03-01, H excluded -> damsire_win_rate from B only = .5.
    r = _row(make_frames(_specs()), "200803010101", "H")
    assert r.damsire_win_rate == 0.5
    assert round(r.damsire_avg_finish, 4) == 3.0
