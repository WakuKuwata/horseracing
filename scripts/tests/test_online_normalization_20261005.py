"""Temporal-boundary and paired-ratio tests for the online normalization supplement."""
import importlib.util
from pathlib import Path

import numpy as np


SPEC = importlib.util.spec_from_file_location("online_normalization", Path(__file__).resolve().parents[1] / "roi_explore/comprehensive_20261005/online_normalization.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_current_year_scores_cannot_change_prior_threshold():
    years = np.array([2012] * 4 + [2013] * 3)
    ev = np.array([.9, 1.1, 1.3, 1.4, 1.3, 1.3, 1.3])
    score = np.array([.8, 1., 1.2, 1.4, 1.1, 1.2, 1.5])
    _, t1 = MOD.select_masks(years, ev, score, [2013])
    score[years == 2013] *= 100
    _, t2 = MOD.select_masks(years, ev, score, [2013])
    assert [t["threshold"] for t in t1] == [t["threshold"] for t in t2]
    assert [t["prior_candidates"] for t in t1] == [4, 3]


def test_ties_kept_without_matching_current_count():
    years = np.array([2012] * 4 + [2013] * 3)
    ev = np.array([.9, .9, 1.3, 1.3, 1.4, .9, .9])
    score = np.full(7, 1.2)
    masks, thresholds = MOD.select_masks(years, ev, score, [2013])
    assert masks["N1"].sum() == 0  # fixed threshold is strict >
    assert masks["N3"].sum() == 3  # transferred threshold includes ties
    assert masks["S3"].sum() == 1
    assert thresholds[0]["prior_selected_by_threshold"] == 4


def test_absent_prior_year_does_not_fall_back_to_future():
    masks, threshold = MOD.select_masks(np.array([2013, 2013]), np.array([2., 2.]), np.array([2., 2.]), [2013])
    assert not masks["N3"].any() and not masks["N4"].any()
    assert all(t["threshold"] is None for t in threshold)


def test_paired_bootstrap_is_ratio_of_totals_not_mean_day_roi():
    stakes = np.array([[100., 100.], [900., 0.]])
    payouts = np.array([[200., 300.], [0., 0.]])
    draws = np.array([[1, 1], [2, 0], [0, 2]])
    res = MOD.ratio_samples(stakes, payouts, draws)
    np.testing.assert_allclose(res[:2], [[.2, 3.], [2., 3.]])
    assert np.isnan(res[2, 1])
    assert res[0, 1] - res[0, 0] == 2.8
