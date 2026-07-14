"""Feature 070 (F03) pm_rank_robust: rank-percentile formula + leak boundary + additive parity.

Behavioural leak-guard (058/069 precedent — this group intentionally uses market data): the TARGET
race's popularity must never change its own features; a PAST race's must.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from horseracing_features.pm_rank_robust import (
    PM_RANK_ROBUST_COLUMNS,
    build_pm_rank_robust_features,
    rank_percentile_primitive,
)
from tests._frames import make_frames

_TARGET = "200803010101"


def _specs():
    # H: 3 prior COMPLETE-field starts (all horses have popularity) then the TARGET.
    def race(rid, date, hpop, xpop, hfin=1):
        return {"race_id": rid, "race_date": date, "horses": [
            {"horse_id": "H", "horse_number": 1, "popularity": hpop, "finish_order": hfin},
            {"horse_id": "X", "horse_number": 2, "popularity": xpop, "finish_order": 2}]}
    return [
        race("200801010101", "2008-01-01", 1, 2),   # H top favourite (rank1, N2 -> u=1)
        race("200802010101", "2008-02-01", 2, 1),   # H rank2 of 2 -> u=0
        race("200802150101", "2008-02-15", 1, 2),   # H rank1 -> u=1
        race(_TARGET, "2008-03-01", 4, 1),
    ]


def _target_rows(frames):
    out = build_pm_rank_robust_features(frames)
    return out[out.race_id == _TARGET].set_index("horse_id").sort_index()


def _same(a, b):
    pd.testing.assert_frame_equal(a, b, check_exact=True)


def test_percentile_and_rate_values():
    r = _target_rows(make_frames(_specs())).loc["H"]
    # H's 3 prior u = [1.0, 0.0, 1.0]; last=1.0, mean5=2/3
    assert r["asof_pm_rankpct_last"] == 1.0
    assert abs(r["asof_pm_rankpct_mean5"] - (2.0 / 3.0)) < 1e-12
    # favourite (rank==1) in 2 of 3; top3 (rank<=3) in all 3
    assert abs(r["asof_pm_favorite_rate5"] - (2.0 / 3.0)) < 1e-12
    assert r["asof_pm_top3fav_rate5"] == 1.0
    assert r["asof_pm_rank_obs_count"] == 3.0


def test_competition_rank_tie_is_row_order_independent():
    # two horses share popularity 1 (tie) -> competition rank 1,1,3; u deterministic under shuffle.
    runs = pd.DataFrame({
        "race_id": ["R", "R", "R"], "horse_id": ["H", "A", "B"],
        "race_date": [pd.Timestamp("2008-01-01")] * 3,
        "popularity": [1, 1, 3], "entry_status": ["started"] * 3,
    })
    base = rank_percentile_primitive(runs).set_index("horse_id")["u"].sort_index()
    shuf = (rank_percentile_primitive(runs.iloc[::-1].reset_index(drop=True))
            .set_index("horse_id")["u"].sort_index())
    pd.testing.assert_series_equal(base, shuf)
    # H,A tie at competition rank 1 of N=3 -> u=1.0; B rank3 -> u=0.0
    assert base["H"] == 1.0 and base["A"] == 1.0 and base["B"] == 0.0


def test_min_obs_gate_nan_including_last():
    # H has only 2 prior complete-field starts (< min_obs=3): all continuous cols NaN incl. last.
    specs = _specs()[1:]  # drop one prior -> 2 priors before target
    r = _target_rows(make_frames(specs)).loc["H"]
    assert np.isnan(r["asof_pm_rankpct_last"])
    assert np.isnan(r["asof_pm_rankpct_mean5"])
    assert r["asof_pm_rank_obs_count"] == 2.0  # count is a FACT, not gated


def test_popularity_only_complete_field():
    # a past race where ONE started horse lacks popularity -> that race is dropped (complete-field),
    # but does NOT require odds. H's other 3 races still count.
    specs = _specs()
    specs[0]["horses"][1]["popularity"] = None  # X missing popularity in race 1
    r = _target_rows(make_frames(specs)).loc["H"]
    assert r["asof_pm_rank_obs_count"] == 2.0  # race 1 dropped, 2 remain


def test_n1_race_gives_u_one():
    prim = rank_percentile_primitive(pd.DataFrame({
        "race_id": ["R"], "horse_id": ["H"], "race_date": [pd.Timestamp("2008-01-01")],
        "popularity": [1], "entry_status": ["started"],
    }))
    assert prim["u"].iloc[0] == 1.0 and prim["N"].iloc[0] == 1.0


def test_leak_invariant_to_target_and_future_and_positive():
    base = _target_rows(make_frames(_specs()))
    # target-race popularity change -> no effect
    s = _specs()
    s[-1]["horses"][0]["popularity"] = 18
    _same(base, _target_rows(make_frames(s)))
    # future race -> no effect
    s = _specs() + [{"race_id": "200812010101", "race_date": "2008-12-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "popularity": 1, "finish_order": 1},
        {"horse_id": "X", "horse_number": 2, "popularity": 2, "finish_order": 2}]}]
    _same(base, _target_rows(make_frames(s)))
    # positive: changing a PAST popularity DOES change features (flips H's rank1->rank2 in race 1)
    s = _specs()
    s[0]["horses"][0]["popularity"] = 2
    s[0]["horses"][1]["popularity"] = 1
    changed = _target_rows(make_frames(s))
    assert not base.equals(changed)


def test_clean_names():
    # 070 was REJECTED + reverted (not in the registry); the module is kept as a negative result,
    # so this tests the leak-guard token discipline of the columns only (no FEATURE_GROUPS check).
    for name in PM_RANK_ROBUST_COLUMNS:
        low = name.lower()
        for tok in ("odds", "popularity", "payout", "dividend", "result", "finish_order"):
            assert tok not in low, (name, tok)


def test_is_purely_additive():
    pm = build_pm_rank_robust_features(make_frames(_specs()))
    keys = ["race_id", "horse_id"]
    assert set(pm.columns) == set(keys) | set(PM_RANK_ROBUST_COLUMNS)
    assert not pm.duplicated(subset=keys).any()
