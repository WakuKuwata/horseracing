"""173: pure helpers of the candidate-B retention scorer.

cd scripts/tests && ../../training/.venv/bin/python -m pytest test_odds_noise_retention_173.py -q
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "roi_explore" / "odds_noise_retention_173"))
import score_retention as SR  # noqa: E402

from horseracing_eval.bootstrap import block_bootstrap_counts  # noqa: E402


def test_prereg_is_frozen_and_loadable():
    pre = SR.load_prereg()
    assert pre["verdict"]["min_n_j"] == 20
    assert pre["verdict"]["pass_if"] == {"retention_ge": 0.5, "ev_ratio_median_ge": 0.95}
    assert pre["_sha256"] == hashlib.sha256(SR.PREREG_JSON.read_bytes()).hexdigest()


def test_prereg_sha_mismatch_refuses(tmp_path):
    p = tmp_path / "prereg.json"
    p.write_text(json.dumps({"verdict": {}}), encoding="utf-8")
    (tmp_path / "prereg.sha256").write_text("0" * 64, encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing"):
        SR.load_prereg(p, tmp_path / "prereg.sha256")


def test_hours_band_edges():
    got = SR.hours_band([0.1, 0.25, 0.26, 1.0, 3.0, 10.0, 40.0, 41.0])
    assert list(got) == [
        "(0,0.25]", "(0,0.25]", "(0.25,0.5]", "(0.5,1]", "(1,3]", "(3,10]", "(10,40]", ">40",
    ]


def test_weighted_median():
    assert SR.weighted_median([1, 2, 3], [1, 1, 1]) == 2.0
    assert SR.weighted_median([1, 2, 3], [0, 0, 5]) == 3.0
    assert SR.weighted_median([3, 1, 2], [1, 1, 0]) == 1.0  # zero-weight rows drop out
    assert np.isnan(SR.weighted_median([1.0], [0.0]))


def test_day_index_sorted_and_dense():
    idx, days = SR.day_index(["2026-10-12", "2026-10-05", "2026-10-12"])
    assert days == ["2026-10-05", "2026-10-12"]
    assert idx.tolist() == [1, 0, 1]


def test_rule_masks_follow_frozen_definitions():
    ev = np.array([1.25, 1.25, 1.35, 1.15, np.nan])
    odds = np.array([25.0, 45.0, 30.0, 30.0, 30.0])
    dsl = np.array([30.0, 30.0, 30.0, 30.0, 30.0])
    m = SR.rule_masks(ev, odds, dsl)
    assert m["S3"].tolist() == [True, True, True, False, False]  # EV>1.2 only
    assert m["S1"].tolist() == [True, False, True, False, False]  # + 20<=odds<40 + gap
    assert m["S2"].tolist() == [False, False, True, False, False]  # EV>1.3
    assert m["S4"].tolist() == [True, False, True, True, False]  # EV>1.1 in band (row 3: 1.15)
    assert set(m) == {"S1", "S2", "S3", "S4"}


def _block_inputs():
    # 6 rows over 3 days; J selects rows 0,1,2,3; closing keeps 0 and 2
    j = np.array([1, 1, 1, 1, 0, 0], bool)
    f = np.array([1, 0, 1, 0, 1, 0], bool)
    ev_j = np.array([1.3, 1.4, 1.5, 1.6, 1.0, 0.9])
    ev_f = np.array([1.3, 0.7, 1.5, 0.8, 1.2, 0.5])
    won = np.array([1, 0, 0, 0, 0, 1], bool)
    p_j = np.array([0.05, 0.05, 0.05, 0.05, 0.1, 0.1])
    day = np.array([0, 0, 1, 1, 2, 2])
    return j, f, ev_j, ev_f, won, p_j, day


def test_arm_block_point_estimates_and_ci():
    j, f, ev_j, ev_f, won, p_j, day = _block_inputs()
    counts = block_bootstrap_counts(3, 500, 7)
    blk = SR.arm_block(j, f, ev_j, ev_f, won, p_j, day, counts, official_ret=np.array([2.0, 0, 0, 0, np.nan, np.nan]))
    assert blk["n_j"] == 4 and blk["n_retained"] == 2 and blk["retention"] == 0.5
    assert blk["ev_ratio_median"] == 0.5  # lower median of [1.0, 0.5, 1.0, 0.5] (same estimator as the replicates)
    assert blk["calib_won_over_p"] == pytest.approx(1 / 0.2)
    lo, hi = blk["retention_ci"]
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
    lo2, hi2 = blk["ev_ratio_median_ci"]
    assert 0.5 <= lo2 <= blk["ev_ratio_median"] <= hi2 <= 1.0  # every day holds one 1.0 and one 0.5
    assert blk["roi_official_reference"] == {"n": 4, "roi": 0.5, "note": blk["roi_official_reference"]["note"]}


def test_arm_block_deterministic_for_same_seed():
    j, f, ev_j, ev_f, won, p_j, day = _block_inputs()
    a = SR.arm_block(j, f, ev_j, ev_f, won, p_j, day, block_bootstrap_counts(3, 300, 11))
    b = SR.arm_block(j, f, ev_j, ev_f, won, p_j, day, block_bootstrap_counts(3, 300, 11))
    assert a == b


def test_arm_block_empty_selection():
    j, f, ev_j, ev_f, won, p_j, day = _block_inputs()
    blk = SR.arm_block(np.zeros(6, bool), f, ev_j, ev_f, won, p_j, day, block_bootstrap_counts(3, 50, 1))
    assert blk["n_j"] == 0 and blk["retention"] is None and blk["ev_ratio_median"] is None


def test_verdict_three_outcomes():
    pre = {"verdict": {"min_n_j": 20, "pass_if": {"retention_ge": 0.5, "ev_ratio_median_ge": 0.95}}}
    assert SR.verdict({"n_j": 19, "retention": 0.9, "ev_ratio_median": 1.0}, pre)["outcome"] == "NO_DECISION"
    assert SR.verdict({"n_j": 20, "retention": 0.5, "ev_ratio_median": 0.95}, pre)["outcome"] == "PASS"
    assert SR.verdict({"n_j": 40, "retention": 0.49, "ev_ratio_median": 0.99}, pre)["outcome"] == "REJECT"
    assert SR.verdict({"n_j": 40, "retention": 0.6, "ev_ratio_median": 0.94}, pre)["outcome"] == "REJECT"


def test_frame_at_recomputes_market_block_from_given_odds():
    r02 = SR.C.load_r02()
    base = pd.DataFrame(
        {
            "race_id": ["r1"] * 3 + ["r2"] * 2,
            "horse_id": list("abcde"),
            "horse_number": [1, 2, 3, 1, 2],
            "popularity": [9.0] * 5,
            "q": [0.0] * 5,
        }
    )
    fr = SR.frame_at(base, np.array([2.0, 4.0, 4.0, 1.5, 3.0]), np.log(60.0), r02)
    inv = 1 / fr["odds"]
    q = inv / inv.groupby(fr["race_id"]).transform("sum")
    assert np.allclose(fr["q"], q)
    assert fr["popularity"].tolist() == [1.0, 2.0, 3.0, 1.0, 2.0]  # popularity := odds_rank
    assert (fr["log_min"] == np.log(60.0)).all()
    fr0 = SR.frame_at(base, np.array([2.0, 4.0, 4.0, 1.5, 3.0]), 0.0, r02)
    assert (fr0["log_min"] == 0.0).all()


def test_popularity_with_fallback_uses_rank_min_where_unknown():
    got = SR.popularity_with_fallback([3.0, None, np.nan, 1.0], [10.0, 5.0, 5.0, 2.0], ["a", "a", "a", "b"])
    assert got.tolist() == [3.0, 1.0, 1.0, 1.0]  # ties share the min rank; observed values untouched


def test_predict_both_popularity_override_only_touches_ens_frame():
    frame = pd.DataFrame({"popularity": [1.0, 2.0]})
    seen = {}

    class _M:
        def design_matrix(self, f):
            seen["ens"] = f["popularity"].tolist()
            return np.zeros((2, 1))

    class _B:
        def predict(self, X, num_threads=1):
            return np.full(len(X), 0.1)

    import horseracing_training.market_ev as mev

    orig = mev._design_matrix
    mev._design_matrix = lambda spec, f: seen.setdefault("jud", f["popularity"].tolist()) and np.zeros((2, 1))
    try:
        SR.predict_both(frame, (_M(), [_B()], {}), ({}, [_B()], {}), ens_popularity=np.array([7.0, 8.0]))
    finally:
        mev._design_matrix = orig
    assert seen == {"ens": [7.0, 8.0], "jud": [1.0, 2.0]}
    assert frame["popularity"].tolist() == [1.0, 2.0]  # caller's frame not mutated
