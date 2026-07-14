"""Feature 056: leak boundary + value checks for the raw-column bundle (4 groups).

INV: target-race result / same-day / future mutations never change the target's features
(strictly-before as-of; owner/breeder use the daily cumsum − current-day discipline so the
whole target DAY is excluded). Values verified against hand-computed fixtures.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from horseracing_features.owner_breeder_features import (
    MIN_STARTS,
    build_owner_breeder_features,
)
from horseracing_features.pace_features import build_pace_features
from horseracing_features.race_level_features import build_race_level_features
from tests._frames import make_frames

_TARGET = "200803010101"


def _specs():
    return [
        {"race_id": "200801010101", "race_date": "2008-01-01", "prize_money": 550, "horses": [
            {"horse_id": "H", "horse_number": 1, "finish_order": 1,
             "first_3f": 35.0, "last_3f": 34.0, "owner_name": "馬主A", "breeder_name": "牧場A"},
            {"horse_id": "X", "horse_number": 2, "finish_order": 2,
             "first_3f": 37.0, "last_3f": 36.0, "owner_name": "馬主B", "breeder_name": "牧場B"}]},
        {"race_id": _TARGET, "race_date": "2008-03-01", "prize_money": 1000, "horses": [
            {"horse_id": "H", "horse_number": 1, "finish_order": 1,
             "first_3f": 34.0, "last_3f": 35.5, "owner_name": "馬主A", "breeder_name": "牧場A"},
            {"horse_id": "X", "horse_number": 2, "finish_order": 2,
             "first_3f": 36.0, "last_3f": 36.5, "owner_name": "馬主B", "breeder_name": "牧場B"}]},
    ]


def _rows(builder, frames, race_id=_TARGET):
    out = builder(frames)
    return out[out.race_id == race_id].set_index("horse_id").sort_index()


def _same(a, b):
    pd.testing.assert_frame_equal(a, b, check_exact=True)


# ---- values (hand-computed) --------------------------------------------------


def test_pace_first3f_values():
    out = _rows(build_pace_features, make_frames(_specs()))
    # past race 2008-01-01: race mean first3f = 36.0 → H rel = −1.0; last3f mean 35.0 → H rel −1.0
    assert out.loc["H", "asof_rel_first3f_avg"] == -1.0
    assert out.loc["H", "asof_rel_first3f_best"] == -1.0
    # pace_balance = rel_last3f − rel_first3f = (−1.0) − (−1.0) = 0.0
    assert out.loc["H", "asof_pace_balance_avg"] == 0.0


def test_race_level_values():
    out = _rows(build_race_level_features, make_frames(_specs()))
    # H's only prior race carried prize 550 → asof = log1p(550)
    assert math.isclose(out.loc["H", "asof_prize_avg"], math.log1p(550))


def test_owner_rate_min_starts_gate():
    # only 1 prior finished run per owner (< MIN_STARTS) → NaN, never 0
    assert MIN_STARTS > 1
    out = _rows(build_owner_breeder_features, make_frames(_specs()))
    assert pd.isna(out.loc["H", "asof_owner_win_rate"])
    assert pd.isna(out.loc["H", "asof_breeder_win_rate"])


def test_owner_rate_value_above_min_starts():
    # MIN_STARTS prior finished runs for 馬主A (H wins all) → rate 1.0 at the target
    specs = []
    for i in range(MIN_STARTS):
        specs.append({"race_id": f"2008010{i + 1:02d}0101", "race_date": f"2008-01-{i + 1:02d}",
                      "horses": [
                          {"horse_id": "H", "horse_number": 1, "finish_order": 1,
                           "owner_name": "馬主A"},
                          {"horse_id": "X", "horse_number": 2, "finish_order": 2,
                           "owner_name": "馬主B"}]})
    specs.append({"race_id": _TARGET, "race_date": "2008-03-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "finish_order": 2, "owner_name": "馬主A"},
        {"horse_id": "X", "horse_number": 2, "finish_order": 1, "owner_name": "馬主B"}]})
    out = _rows(build_owner_breeder_features, make_frames(specs))
    assert out.loc["H", "asof_owner_win_rate"] == 1.0
    assert out.loc["H", "asof_owner_place_rate"] == 1.0
    assert out.loc["X", "asof_owner_win_rate"] == 0.0  # 馬主B: 20 starts, 0 wins
    assert out.loc["X", "asof_owner_place_rate"] == 1.0  # …but all 2nd = top3


def test_nfkc_name_normalization_merges_width_variants():
    specs = _specs()
    specs[0]["horses"][0]["owner_name"] = "馬主Ａ"  # full-width A
    base = _rows(build_owner_breeder_features, make_frames(_specs()))
    varied = _rows(build_owner_breeder_features, make_frames(specs))
    _same(base, varied)  # NFKC merges the variant → identical aggregation


# ---- leak boundary -----------------------------------------------------------


def test_invariant_to_own_current_race_result():
    base_p = _rows(build_pace_features, make_frames(_specs()))
    base_r = _rows(build_race_level_features, make_frames(_specs()))
    base_o = _rows(build_owner_breeder_features, make_frames(_specs()))
    specs = _specs()
    specs[1]["horses"][0]["first_3f"] = 99.0     # target's own テン3F
    specs[1]["horses"][0]["finish_order"] = 9    # target's own result
    specs[1]["prize_money"] = 99999              # ← static prize IS allowed to differ, so keep it
    specs[1]["prize_money"] = 1000               # …but hold it constant here to isolate results
    mutated = make_frames(specs)
    _same(base_p, _rows(build_pace_features, mutated))
    _same(base_r, _rows(build_race_level_features, mutated))
    _same(base_o, _rows(build_owner_breeder_features, mutated))


def test_invariant_to_same_day_other_race():
    specs = _specs() + [
        {"race_id": "200803010102", "race_date": "2008-03-01", "prize_money": 5000, "horses": [
            {"horse_id": "H2", "horse_number": 1, "finish_order": 1, "first_3f": 30.0,
             "owner_name": "馬主A", "breeder_name": "牧場A"}]},
    ]
    base_p = _rows(build_pace_features, make_frames(_specs()))
    base_r = _rows(build_race_level_features, make_frames(_specs()))
    base_o = _rows(build_owner_breeder_features, make_frames(_specs()))
    mutated = make_frames(specs)
    _same(base_p, _rows(build_pace_features, mutated))
    _same(base_r, _rows(build_race_level_features, mutated))
    # owner 馬主A gains a SAME-DAY win — daily cumsum−当日 must exclude it
    _same(base_o, _rows(build_owner_breeder_features, mutated))


def test_invariant_to_future_race():
    specs = _specs() + [
        {"race_id": "200805010101", "race_date": "2008-05-01", "prize_money": 8000, "horses": [
            {"horse_id": "H", "horse_number": 1, "finish_order": 1, "first_3f": 30.0,
             "owner_name": "馬主A", "breeder_name": "牧場A"}]},
    ]
    base_p = _rows(build_pace_features, make_frames(_specs()))
    base_r = _rows(build_race_level_features, make_frames(_specs()))
    base_o = _rows(build_owner_breeder_features, make_frames(_specs()))
    mutated = make_frames(specs)
    _same(base_p, _rows(build_pace_features, mutated))
    _same(base_r, _rows(build_race_level_features, mutated))
    _same(base_o, _rows(build_owner_breeder_features, mutated))


def test_missing_first3f_propagates_nan_not_zero():
    specs = _specs()
    specs[0]["horses"][0]["first_3f"] = None  # H's only prior run lacks テン3F
    out = _rows(build_pace_features, make_frames(specs))
    assert pd.isna(out.loc["H", "asof_rel_first3f_avg"])
    assert pd.isna(out.loc["H", "asof_pace_balance_avg"])
    # last3f-based 023 columns keep working
    assert not pd.isna(out.loc["H", "rel_last3f_avg"])


# ---- source hygiene (no odds/payout tokens in the new modules) ----------------


def test_groups_registered_for_feature_eval_default():
    # the training CLI default drop string (_DEF_055) relies on these exact group names
    from horseracing_features.registry import FEATURE_GROUPS, FEATURE_VERSION

    groups = {g: [c for c, gg in FEATURE_GROUPS.items() if gg == g]
              for g in ("pace_first3f", "owner_breeder", "race_level", "sire_line")}
    assert sorted(groups["pace_first3f"]) == [
        "asof_pace_balance_avg", "asof_rel_first3f_avg", "asof_rel_first3f_best"]
    assert sorted(groups["owner_breeder"]) == [
        "asof_breeder_win_rate", "asof_owner_place_rate", "asof_owner_win_rate"]
    assert sorted(groups["race_level"]) == ["asof_prize_avg", "prize_money_log", "prize_rel"]
    assert sorted(groups["sire_line"]) == ["damsire_line", "sire_line"]
    assert FEATURE_VERSION == "features-018"  # 070 rejected+reverted; 069 F02


def test_no_forbidden_tokens_in_new_modules():
    src_dir = Path(__file__).resolve().parents[2] / "src" / "horseracing_features"
    for mod in ("owner_breeder_features.py", "race_level_features.py"):
        text = (src_dir / mod).read_text(encoding="utf-8")
        for tok in ("odds", "payout", "dividend", "popularity"):
            assert tok not in text, (mod, tok)
