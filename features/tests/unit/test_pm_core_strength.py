"""T012: F02 q/s math + as-of reductions (FR-006/010/011, codex D2/D3)."""

from __future__ import annotations

import math

import numpy as np

from horseracing_features.pm_core_strength import (
    _race_support,
    build_pm_core_strength_features,
)
from tests._frames import make_frames


def _race(rid, date, odds_by_horse, target=False):
    horses = [{"horse_id": h, "odds": o, "finish_order": i + 1}
              for i, (h, o) in enumerate(odds_by_horse.items())]
    return {"race_id": rid, "race_date": date, "horses": horses}


def _support_map(frames):
    started = frames.race_horses.merge(
        frames.races[["race_id", "race_date"]], on="race_id"
    )
    started["field_size"] = started.groupby("race_id")["horse_id"].transform("size")
    s = _race_support(started)
    return {(r.race_id, r.horse_id): r.s for r in s.itertuples()}


def test_q_is_market_share_and_s_is_log_qN():
    # 2 horses odds 2.0, 2.0 -> q=0.5 each, N=2 -> s=log(0.5*2)=log(1)=0
    f = make_frames([_race("R1", "2020-01-01", {"a": 2.0, "b": 2.0})])
    sm = _support_map(f)
    assert sm[("R1", "a")] == 0.0
    assert sm[("R1", "b")] == 0.0


def test_favorite_has_positive_support():
    # heavy favorite (1.5) vs longshot (6.0): favorite q > 1/N -> s > 0
    f = make_frames([_race("R1", "2020-01-01", {"fav": 1.5, "dog": 6.0})])
    sm = _support_map(f)
    assert sm[("R1", "fav")] > 0
    assert sm[("R1", "dog")] < 0


def test_common_odds_multiplier_invariance():
    # scaling all odds by a constant leaves q (hence s) unchanged
    a = _support_map(make_frames([_race("R1", "2020-01-01", {"x": 2.0, "y": 4.0})]))
    b = _support_map(make_frames([_race("R2", "2020-01-01", {"x": 6.0, "y": 12.0})]))
    assert a[("R1", "x")] == b[("R2", "x")]
    assert a[("R1", "y")] == b[("R2", "y")]


def test_one_invalid_odds_voids_whole_race():
    # b has odds 0 (invalid) -> the WHOLE race produces no s (complete-field)
    f = make_frames([_race("R1", "2020-01-01", {"a": 2.0, "b": 0.0})])
    sm = _support_map(f)
    assert ("R1", "a") not in sm and ("R1", "b") not in sm


def test_odds_1_0_favorite_is_kept_not_dropped():
    # 元返し 1.0 is a legit heavy favorite; the race must NOT be voided (analyze D1)
    f = make_frames([_race("R1", "2020-01-01", {"fav": 1.0, "dog": 50.0})])
    sm = _support_map(f)
    assert ("R1", "fav") in sm and sm[("R1", "fav")] > 0


def test_999_9_cap_voids_race():
    f = make_frames([_race("R1", "2020-01-01", {"a": 2.0, "b": 999.9})])
    sm = _support_map(f)
    assert ("R1", "a") not in sm


def test_n1_race_gives_s_zero_and_is_counted():
    f = make_frames([_race("R1", "2020-01-01", {"solo": 3.0})])
    sm = _support_map(f)
    assert sm[("R1", "solo")] == 0.0  # log(1*1)


def test_asof_strictly_before_and_obs_count():
    # horse 'h' runs 3 past races then a target; as-of at target sees 3 obs, none same-day
    specs = [
        _race("P1", "2020-01-01", {"h": 2.0, "o": 2.0}),
        _race("P2", "2020-02-01", {"h": 1.5, "o": 6.0}),
        _race("P3", "2020-03-01", {"h": 3.0, "o": 3.0}),
        _race("T1", "2020-04-01", {"h": 2.0, "o": 2.0}),
    ]
    out = build_pm_core_strength_features(make_frames(specs))
    row = out[(out.race_id == "T1") & (out.horse_id == "h")].iloc[0]
    assert row["asof_pm_obs_count"] == 3
    assert row["asof_pm_has_obs"] == 1.0
    assert math.isfinite(row["asof_pm_support_last"])
    # last obs (P3) support: fav 3.0 vs 3.0 -> s=0
    assert row["asof_pm_support_last"] == 0.0


def test_debut_has_nan_and_zero_obs():
    # a horse with no prior race: continuous NaN, obs_count 0, has_obs 0
    specs = [_race("T1", "2020-04-01", {"new": 2.0, "o": 2.0})]
    out = build_pm_core_strength_features(make_frames(specs))
    row = out[(out.race_id == "T1") & (out.horse_id == "new")].iloc[0]
    assert row["asof_pm_obs_count"] == 0
    assert row["asof_pm_has_obs"] == 0.0
    assert np.isnan(row["asof_pm_support_last"])
    assert np.isnan(row["asof_pm_support_trend"])
    assert np.isnan(row["asof_pm_support_sd5"])


def test_trend_and_sd_need_two_obs():
    # single past obs -> trend/sd NaN (need >=2)
    specs = [
        _race("P1", "2020-01-01", {"h": 1.5, "o": 6.0}),
        _race("T1", "2020-02-01", {"h": 2.0, "o": 2.0}),
    ]
    out = build_pm_core_strength_features(make_frames(specs))
    row = out[(out.race_id == "T1") & (out.horse_id == "h")].iloc[0]
    assert row["asof_pm_obs_count"] == 1
    assert np.isnan(row["asof_pm_support_trend"])
    assert np.isnan(row["asof_pm_support_sd5"])
    assert math.isfinite(row["asof_pm_support_last"])
