"""Feature 070 (F05) pm_conditioned: λ=5 conditioned shrinkage + 2 groups + leak + parity."""

from __future__ import annotations

import numpy as np
import pandas as pd

from horseracing_features.pm_conditioned import (
    PM_CONDITIONED_COLUMNS,
    PM_CONDITIONED_RESIDUAL_COLUMNS,
    PM_CONDITIONED_SUPPORT_COLUMNS,
    _conditioned_shrunk,
    build_pm_conditioned_features,
)
from tests._frames import make_frames

_TARGET = "200805010101"


def _shrunk_src():
    d = pd.to_datetime(["2008-01-01", "2008-02-01", "2008-03-01"])
    return pd.DataFrame({
        "horse_id": ["H", "H", "H"], "race_date": d,
        "cell": ["A", "B", "B"], "_v": [10.0, 20.0, 30.0],
    })


def _tgt(cell):
    return pd.DataFrame({"race_id": ["R"], "horse_id": ["H"],
                         "race_date": [pd.Timestamp("2008-04-01")], "cell": [cell]})


def test_lambda5_shrinkage_and_real_cell_count():
    src = _shrunk_src()
    # cell B: {20,30} n=2 mean=25; parent {10,20,30} mean=20; shrunk=(2*25+5*20)/7=150/7
    r = _conditioned_shrunk(src, _tgt("B"), "_v", "cell").iloc[0]
    assert abs(r["shrunk"] - 150.0 / 7.0) < 1e-12
    assert r["cell_cnt"] == 2.0


def test_parent_fallback_when_cell_absent():
    # target cell C never seen -> n_cell=0 -> shrunk = parent_mean=20; real cell count 0.
    r = _conditioned_shrunk(_shrunk_src(), _tgt("C"), "_v", "cell").iloc[0]
    assert abs(r["shrunk"] - 20.0) < 1e-12
    assert r["cell_cnt"] == 0.0


def test_parent_is_overall_all_prior_including_cell_null(  ):
    # codex 実装#1: a past race with a NULL cell key must still count toward the OVERALL parent.
    # src: race1 cell=<NA> val=10, race2 cell=B val=20 ; target cell=C (unseen) -> n_cell=0 ->
    # parent fallback = mean(10,20)=15 (NOT 20 — the null-cell race is NOT excluded from parent).
    d = pd.to_datetime(["2008-01-01", "2008-02-01"])
    src = pd.DataFrame({"horse_id": ["H", "H"], "race_date": d,
                        "cell": [pd.NA, "B"], "_v": [10.0, 20.0]})
    r = _conditioned_shrunk(src, _tgt("C"), "_v", "cell").iloc[0]
    assert abs(r["shrunk"] - 15.0) < 1e-12
    assert r["cell_cnt"] == 0.0


def test_null_cell_target_falls_back_to_parent():
    # a TARGET with a null cell key -> n_cell=0 -> parent mean (not NaN).
    r = _conditioned_shrunk(_shrunk_src(), _tgt(None), "_v", "cell").iloc[0]
    assert abs(r["shrunk"] - 20.0) < 1e-12  # parent mean of {10,20,30}
    assert r["cell_cnt"] == 0.0


def test_parent_empty_is_nan_not_zero():
    # a horse with NO prior obs at all -> parent empty -> NaN (Unknown, IV/analyze U1).
    empty = _shrunk_src().iloc[0:0]
    r = _conditioned_shrunk(empty, _tgt("A"), "_v", "cell").iloc[0]
    assert np.isnan(r["shrunk"]) and r["cell_cnt"] == 0.0


def _specs():
    # H: two 芝 starts + one ダ start, then target on 芝.
    def race(rid, date, track):
        return {"race_id": rid, "race_date": date, "track_type": track, "horses": [
            {"horse_id": "H", "horse_number": 1, "popularity": 1, "odds": 2.0, "finish_order": 1},
            {"horse_id": "X", "horse_number": 2, "popularity": 2, "odds": 3.0, "finish_order": 2}]}
    return [
        race("200801010101", "2008-01-01", "芝"),
        race("200802010101", "2008-02-01", "芝"),
        race("200803010101", "2008-03-01", "ダ"),
        race(_TARGET, "2008-05-01", "芝"),
    ]


def _target(frames):
    out = build_pm_conditioned_features(frames)
    return out[out.race_id == _TARGET].set_index("horse_id").sort_index()


def test_per_axis_count_counts_real_cells_only():
    r = _target(make_frames(_specs())).loc["H"]
    # surface cell 芝 has 2 real obs (races 1,2); distband/venue are constant so all 3.
    assert r["asof_pm_support_cond_count_surface"] == 2.0
    assert r["asof_pm_support_cond_count_distband"] == 3.0
    assert not np.isnan(r["asof_pm_support_surface"])


def test_pool_end_independent_future_and_leak():
    base = _target(make_frames(_specs()))
    # future race must not change past rows (pool-end independent -> materialize-safe)
    s = _specs() + [{"race_id": "200812010101", "race_date": "2008-12-01", "track_type": "芝",
                     "horses": [
                         {"horse_id": "H", "horse_number": 1, "popularity": 1, "odds": 2.0,
                          "finish_order": 1},
                         {"horse_id": "X", "horse_number": 2, "popularity": 2, "odds": 3.0,
                          "finish_order": 2}]}]
    pd.testing.assert_frame_equal(base, _target(make_frames(s)), check_exact=True)
    # target-race odds/result change -> no effect
    s = _specs()
    s[-1]["horses"][0]["odds"] = 99.0
    s[-1]["horses"][0]["finish_order"] = 9
    pd.testing.assert_frame_equal(base, _target(make_frames(s)), check_exact=True)


def test_support_residual_columns_and_parity():
    # 070 REJECTED + reverted (unwired); module kept as negative result — no registry checks.
    assert len(PM_CONDITIONED_SUPPORT_COLUMNS) == 6
    assert len(PM_CONDITIONED_RESIDUAL_COLUMNS) == 2
    pm = build_pm_conditioned_features(make_frames(_specs()))
    keys = ["race_id", "horse_id"]
    assert set(pm.columns) == set(keys) | set(PM_CONDITIONED_COLUMNS)
    assert not pm.duplicated(subset=keys).any()
    for name in PM_CONDITIONED_COLUMNS:
        low = name.lower()
        for tok in ("odds", "popularity", "payout", "dividend"):
            assert tok not in low, (name, tok)
