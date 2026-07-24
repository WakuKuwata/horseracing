"""Feature 081 Phase 0 residual-offset probe — pure-math unit tests."""

from __future__ import annotations

import numpy as np

from horseracing_eval.residual_probe import (
    RaceProbe,
    _delta_nll_race,
    fit_gamma,
    prequential_delta_nll,
    score_statistic,
)


def _race(day, p, h, winner_idx):
    return RaceProbe(day=day, p=np.asarray(p, float), h=np.asarray(h, float), winner_idx=winner_idx)


def test_race_constant_h_cancels_in_softmax():
    """A within-race CONSTANT candidate cannot move a race-softmax ranking: U == 0, ΔNLL == 0
    for any gamma (the race-constant-features-need-interaction property)."""
    r = _race("d1", [0.5, 0.3, 0.2], [[7.0], [7.0], [7.0]], winner_idx=0)
    assert np.allclose(score_statistic(r), 0.0)
    for g in (-2.0, 0.0, 1.5):
        assert abs(_delta_nll_race(*_clean(r), 0, np.array([g]))) < 1e-12


def _clean(r):
    from horseracing_eval.residual_probe import _clean as c
    return c(r)


def test_score_sign_matches_residual_direction():
    """If the winner has an ABOVE-average h, U>0 => a small positive gamma lowers NLL."""
    r = _race("d1", [0.4, 0.4, 0.2], [[1.0], [0.0], [0.0]], winner_idx=0)
    U = score_statistic(r)
    assert U[0] > 0
    p, h = _clean(r)
    base = _delta_nll_race(p, h, 0, np.array([0.0]))
    tilt = _delta_nll_race(p, h, 0, np.array([0.05]))
    assert base == 0.0
    assert tilt < 0  # improvement


def test_delta_nll_zero_at_gamma_zero():
    r = _race("d1", [0.5, 0.3, 0.2], [[1.0], [-1.0], [0.5]], winner_idx=1)
    p, h = _clean(r)
    assert abs(_delta_nll_race(p, h, 1, np.array([0.0]))) < 1e-12


def test_fit_gamma_recovers_planted_signal():
    """Plant a factor where the winner is consistently the max-h horse; fit should give gamma>0
    and reduce mean ΔNLL below zero."""
    rng = np.random.default_rng(0)
    races = []
    for i in range(400):
        h = rng.normal(size=4)
        p = np.full(4, 0.25)
        winner = int(np.argmax(h))  # winner correlates with high h
        races.append(_race(f"d{i%50}", p, h[:, None], winner))
    gamma = fit_gamma(races, k=1)
    assert gamma[0] > 0
    total = np.mean([_delta_nll_race(*_clean(r), r.winner_idx, gamma) for r in races])
    assert total < 0


def test_fit_gamma_null_factor_stays_near_zero():
    """A factor unrelated to the winner should fit gamma ~ 0 and ΔNLL ~ 0."""
    rng = np.random.default_rng(1)
    races = []
    for i in range(400):
        h = rng.normal(size=5)
        winner = int(rng.integers(0, 5))  # winner independent of h
        races.append(_race(f"d{i%40}", np.full(5, 0.2), h[:, None], winner))
    gamma = fit_gamma(races, k=1)
    assert abs(gamma[0]) < 0.15


def test_prequential_holds_out_and_reports_coverage():
    rng = np.random.default_rng(2)
    folds = []
    for f in range(4):
        fold = []
        for i in range(60):
            h = rng.normal(size=4)
            winner = int(np.argmax(h))
            fold.append(_race(f"f{f}d{i%10}", np.full(4, 0.25), h[:, None], winner))
        folds.append(fold)
    res = prequential_delta_nll(folds, "planted", k=1)
    assert res.n_races == 240
    assert res.coverage > 0.9
    assert res.point_delta_nll < 0                 # planted signal improves out-of-fold
    assert len(res.gammas_by_fold) == 3            # fold 0 skipped (no prior)
    assert res.mean_score_U[0] > 0


def test_nan_h_is_no_tilt():
    """NaN in h contributes multiplier 1 (no tilt). A race where only the winner is NaN but all
    others are 0 => U==0 (winner h treated as 0)."""
    r = _race("d1", [0.5, 0.3, 0.2], [[np.nan], [0.0], [0.0]], winner_idx=0)
    assert np.allclose(score_statistic(r), 0.0)


def test_vector_candidate_two_columns():
    """seasonal_sex-style 2-column candidate: fit returns a length-2 gamma."""
    rng = np.random.default_rng(3)
    races = []
    for i in range(300):
        h = rng.normal(size=(5, 2))
        winner = int(np.argmax(h[:, 0] + 0.5 * h[:, 1]))
        races.append(_race(f"d{i%30}", np.full(5, 0.2), h, winner))
    gamma = fit_gamma(races, k=2)
    assert gamma.shape == (2,)
    total = np.mean([_delta_nll_race(*_clean(r), r.winner_idx, gamma) for r in races])
    assert total < 0
