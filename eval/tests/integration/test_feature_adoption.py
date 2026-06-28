"""Feature 020 US2 (SC-004/005/006/009/010): walk-forward adoption gate behaviour.

Uses feature-aware fakes (no LightGBM) to exercise the harness/gate logic: the candidate set is
FIXED a priori by the caller (no OOS-driven feature selection — no selection leak), per-fold + mean
metrics are computed, the PRIMARY gate is LogLoss improvement AND ECE non-degradation, fold-level
diffs reject lucky folds, a non-improving candidate yields adopted=False (no false positive), and the
report is deterministic for the same inputs.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.dataset import load_eval_races
from horseracing_eval.feature_eval import evaluate_feature_adoption
from horseracing_eval.harness import evaluate
from tests._fakepredictor import FakePredictor
from tests._synth import insert_race, make_informative_field

pytestmark = pytest.mark.integration


def _seed(session):
    for year in (2007, 2008, 2009, 2010):
        for r in range(1, 6):
            rid = f"{year}06{r:02d}{r:02d}01"
            insert_race(
                session, race_id=rid, race_date=datetime.date(year, 6, r),
                horses=make_informative_field(8, winner=0),  # horse_number 1 always wins
            )


def test_candidate_beats_baseline_is_adopted(session):
    _seed(session)
    # Both predictors discriminate (winner = horse #1); the sharper candidate is closer to the
    # realized 1.0 win rate, so it improves BOTH LogLoss and ECE over the weaker baseline.
    report = evaluate_feature_adoption(
        session,
        candidate=FakePredictor(skill=50.0),   # sharp, near-calibrated to the true winner
        baseline=FakePredictor(skill=8.0),     # weaker discrimination
    )
    assert report.n_folds == len(report.per_fold) > 0          # per-fold + folds present
    assert report.mean_logloss_cand < report.mean_logloss_base  # LogLoss improved
    assert report.mean_ece_cand <= report.mean_ece_base          # ECE not worse
    assert report.primary_pass is True
    assert report.n_winning_folds == report.n_folds             # every fold wins -> not lucky
    assert report.adopted is True


def test_worst_fold_ece_tol_is_wired(session):
    """The per-fold worst-ECE guard is a real, separate knob: an impossibly tight tolerance blocks
    adoption via the fold guard even when the PRIMARY (mean) gate still passes."""
    _seed(session)
    kwargs = dict(candidate=FakePredictor(skill=50.0), baseline=FakePredictor(skill=8.0))
    assert evaluate_feature_adoption(session, **kwargs).adopted is True
    strict = evaluate_feature_adoption(session, worst_fold_ece_tol=-1.0, **kwargs)
    assert strict.primary_pass is True      # mean gate unaffected
    assert strict.adopted is False          # fold guard vetoes


def test_no_false_positive_when_candidate_not_better(session):
    """SC-010: a candidate that does NOT beat baseline must NOT be adopted."""
    _seed(session)
    report = evaluate_feature_adoption(
        session,
        candidate=FakePredictor(skill=8.0),    # weaker (worse)
        baseline=FakePredictor(skill=50.0),    # better
    )
    assert report.primary_pass is False
    assert report.adopted is False
    # adopted=True must IMPLY a real improvement (gate is win quality, never ROI)
    assert not report.adopted or report.mean_logloss_cand < report.mean_logloss_base


def test_no_selection_leak_baseline_is_the_fixed_predictor(session):
    """SC-004: the harness does NOT re-select features; baseline metrics == the passed predictor's."""
    _seed(session)
    baseline = FakePredictor(skill=2.0)
    report = evaluate_feature_adoption(
        session, candidate=FakePredictor(skill=8.0), baseline=baseline,
    )
    races = load_eval_races(session, start_date=datetime.date(2007, 1, 1))
    independent = evaluate(FakePredictor(skill=2.0), races).overall["win"]["log_loss"]
    assert report.mean_logloss_base == pytest.approx(independent)


def test_deterministic(session):
    """SC-004/005/006: same inputs -> identical AdoptionReport."""
    _seed(session)
    r1 = evaluate_feature_adoption(
        session, candidate=FakePredictor(skill=50.0), baseline=FakePredictor(skill=8.0)
    )
    r2 = evaluate_feature_adoption(
        session, candidate=FakePredictor(skill=50.0), baseline=FakePredictor(skill=8.0)
    )
    assert r1 == r2


def test_gate_is_win_quality_not_roi(session):
    """SC-008/010: the adoption report carries NO ROI/market field — the gate is win quality only."""
    _seed(session)
    report = evaluate_feature_adoption(
        session, candidate=FakePredictor(skill=50.0), baseline=FakePredictor(skill=8.0)
    )
    fields = set(vars(report))
    assert not any("roi" in f or "kelly" in f or "market" in f for f in fields)
