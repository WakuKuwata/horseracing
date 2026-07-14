"""Feature 070 (F04) pm_expectation_residual: 2-population residuals + leak + additive parity."""

from __future__ import annotations

import numpy as np
import pandas as pd
from horseracing_db.enums import ResultStatus

from horseracing_features.pm_core_strength import race_market_primitive
from horseracing_features.pm_expectation_residual import (
    PM_EXPECTATION_RESIDUAL_COLUMNS,
    build_pm_expectation_residual_features,
)
from horseracing_features.pm_rank_robust import rank_percentile_primitive
from tests._frames import make_frames

_TARGET = "200804010101"


def _race(rid, date, hfin=1, hpop=1, xpop=2, hodds=2.0, xodds=3.0, **hkw):
    # complete-field: both H and X carry popularity AND odds.
    return {"race_id": rid, "race_date": date, "horses": [
        {"horse_id": "H", "horse_number": 1, "popularity": hpop, "odds": hodds,
         "finish_order": hfin, **hkw},
        {"horse_id": "X", "horse_number": 2, "popularity": xpop, "odds": xodds,
         "finish_order": 2}]}


def _specs():
    # 3 identical prior starts: H rank1 (u=1), wins (v=1 -> e=0); q_H=0.6, win=1 -> w=0.4.
    return [
        _race("200801010101", "2008-01-01"),
        _race("200802010101", "2008-02-01"),
        _race("200803010101", "2008-03-01"),
        _race(_TARGET, "2008-04-01"),
    ]


def _target(frames):
    out = build_pm_expectation_residual_features(frames)
    return out[out.race_id == _TARGET].set_index("horse_id").sort_index()


def test_residual_values_two_populations():
    r = _target(make_frames(_specs())).loc["H"]
    # q_H = (1/2)/((1/2)+(1/3)) = 0.6 ; e=0 ; w=0.4
    assert abs(r["asof_pm_finish_resid_career"] - 0.0) < 1e-12
    assert abs(r["asof_pm_win_resid_career"] - 0.4) < 1e-12
    assert abs(r["asof_pm_win_resid_mean10"] - 0.4) < 1e-12
    assert abs(r["asof_pm_resid_sd5"] - 0.0) < 1e-12  # sd of [0.4,0.4,0.4]
    assert r["asof_pm_result_obs_count"] == 3.0


def test_v_denominator_is_n_started_not_n_finished():
    # a 3-horse race where a NON-target horse DNFs: N_started=3 stays the v denominator.
    def race(rid, date, hfin):
        return {"race_id": rid, "race_date": date, "horses": [
            {"horse_id": "H", "horse_number": 1, "popularity": 1, "odds": 2.0, "finish_order": hfin},
            {"horse_id": "X", "horse_number": 2, "popularity": 2, "odds": 3.0, "finish_order": 2},
            {"horse_id": "Y", "horse_number": 3, "popularity": 3, "odds": 6.0,
             "result_status": ResultStatus.STOPPED}]}  # DNF started horse
    specs = [race(f"20080{i}010101", f"2008-0{i}-01", 2) for i in (1, 2, 3)]
    specs.append(race(_TARGET, "2008-04-01", 1))
    r = _target(make_frames(specs)).loc["H"]
    # H rank1 of N_started=3 -> u = 1-(1-1)/(3-1) = 1.0 ; H finish 2nd -> v = 1-(2-1)/(3-1)=0.5
    # e = v - u = 0.5 - 1.0 = -0.5  (uses N_started=3, NOT N_finished=2)
    assert abs(r["asof_pm_finish_resid_career"] - (-0.5)) < 1e-12


def test_q_missing_skips_win_residual_only():
    # a past race where X lacks odds -> q undefined -> that race yields NO win_residual obs,
    # but finish_residual (popularity-complete) still counts it.
    specs = _specs()
    specs[0]["horses"][1]["odds"] = None  # race 1 not odds-complete
    r = _target(make_frames(specs)).loc["H"]
    assert r["asof_pm_result_obs_count"] == 2.0  # win pop: races 2,3 only


def test_finish_resid_gated_on_finished_count():
    # H starts 3 times but FINISHES only once (< min_obs=3) -> finish_resid NaN, but win_resid uses
    # the started count (3) so it is present.
    specs = _specs()
    for i in (0, 1):
        specs[i]["horses"][0]["result_status"] = ResultStatus.STOPPED  # H DNF in races 1,2
    r = _target(make_frames(specs)).loc["H"]
    assert np.isnan(r["asof_pm_finish_resid_career"])   # only 1 finished < 3
    assert r["asof_pm_result_obs_count"] == 3.0          # started count
    assert not np.isnan(r["asof_pm_win_resid_career"])   # win pop has 3


def test_shares_f02_q_and_f03_u_primitives():
    # the residual must reuse the SAME q/u as F02/F03 (no re-computation, FR-004).
    frames = make_frames(_specs())
    started = frames.race_horses.merge(
        frames.races[["race_id", "race_date"]], on="race_id", how="left")
    started = started[started.entry_status == "started"].copy()
    started["race_date"] = pd.to_datetime(started["race_date"])
    q = race_market_primitive(started)
    u = rank_percentile_primitive(started)
    assert abs(q[q.horse_id == "H"]["q"].iloc[0] - 0.6) < 1e-9
    assert u[u.horse_id == "H"]["u"].iloc[0] == 1.0


def test_resid_sd5_min_obs2():
    # with only 1 prior started race, sd5 needs >=2 -> NaN.
    specs = _specs()[2:]  # 1 prior + target
    r = _target(make_frames(specs)).loc["H"]
    assert np.isnan(r["asof_pm_resid_sd5"])


def test_leak_and_clean_names_and_parity():
    # 070 REJECTED + reverted (unwired); module kept as negative result — no registry checks.
    base = _target(make_frames(_specs()))
    # target-race result/odds change -> no effect (strictly-before)
    s = _specs()
    s[-1]["horses"][0]["finish_order"] = 9
    s[-1]["horses"][0]["odds"] = 99.0
    pd.testing.assert_frame_equal(base, _target(make_frames(s)), check_exact=True)
    for name in PM_EXPECTATION_RESIDUAL_COLUMNS:
        low = name.lower()
        for tok in ("odds", "popularity", "payout", "dividend"):
            assert tok not in low, (name, tok)
    pm = build_pm_expectation_residual_features(make_frames(_specs()))
    keys = ["race_id", "horse_id"]
    assert set(pm.columns) == set(keys) | set(PM_EXPECTATION_RESIDUAL_COLUMNS)
    assert not pm.duplicated(subset=keys).any()
