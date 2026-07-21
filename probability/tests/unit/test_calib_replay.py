"""Feature 078 US3 (T013): OOF-replay parity — shipped params are apply-safe before activation."""

from __future__ import annotations

import pytest
from horseracing_eval.stage_discount import StageDiscount

from horseracing_probability.calib_activation import load_calibration
from horseracing_probability.calib_manifest import build_manifest_v3
from horseracing_probability.calib_replay import ReplayParityError, replay_parity_report
from horseracing_probability.model_calibration import PCalibrator

_WIN_VECTORS = [
    {"a": 0.5, "b": 0.3, "c": 0.15, "d": 0.05},
    {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25},
    {"a": 0.7, "b": 0.2, "c": 0.07, "d": 0.03},
]


def _tg(gamma_lo=1.6, gamma_hi=0.5):
    return PCalibrator(
        method="two_gamma" if (gamma_lo, gamma_hi) != (1.0, 1.0) else "identity",
        params={"gamma_lo": gamma_lo, "gamma_hi": gamma_hi, "pivot": 0.15},
        train_window=None, n_races=0, n_samples=0, prob_range=(0.0, 1.0), select="test",
        base_model_version="lgbm-063", logic_version="t", sufficient=True)


def test_fitted_params_are_apply_safe():
    rep = replay_parity_report(
        two_gamma=_tg(1.6, 0.5), stage_discount=StageDiscount(lambda2=0.82, lambda3=0.70),
        win_vectors=_WIN_VECTORS)
    assert rep["passed"] and rep["n_win_vectors"] == 3
    assert rep["two_gamma_identity"] is False and rep["stage_identity"] is False


def test_identity_params_are_byte_parity():
    rep = replay_parity_report(
        two_gamma=_tg(1.0, 1.0), stage_discount=StageDiscount(lambda2=1.0, lambda3=1.0),
        win_vectors=_WIN_VECTORS)
    assert rep["two_gamma_identity"] is True and rep["stage_identity"] is True


def test_v3_manifest_activation_replays_cleanly(tmp_path):
    """End-to-end: a real v3 manifest's LOADED activation params pass the replay parity check."""
    import datetime
    import json

    from horseracing_probability.calib_activation import Profile
    dig = "a" * 64
    m = build_manifest_v3(
        attestation_digest=dig, bundle_digest="b" * 64,
        evaluation={"two_gamma_win": {}, "stage_discount_topk": {}},
        code_sha="078", seed=1, num_threads=1,
        two_gamma_verdict="ADOPT",
        two_gamma_params={"gamma_lo": 1.6, "gamma_hi": 0.5, "pivot": 0.15},
        two_gamma_fit_through="2024-12-31", two_gamma_fit_race_set_hash="h1", two_gamma_n_fit=100,
        stage_verdict="ADOPT", stage_params={"lambda2": 0.82, "lambda3": 0.70},
        stage_fit_through="2024-12-31", stage_fit_race_set_hash="h2", stage_n_fit=90)
    path = (tmp_path / "v3.json").resolve()
    path.write_text(json.dumps(m), encoding="utf-8")
    act = load_calibration(str(path), active_model_version="lgbm-063",
                           target_date=datetime.date(2025, 6, 1), profile=Profile.PRODUCTION)
    rep = replay_parity_report(two_gamma=act.two_gamma, stage_discount=act.stage_discount,
                               win_vectors=_WIN_VECTORS)
    assert rep["passed"]


def test_degenerate_win_vector_is_rejected():
    with pytest.raises(ReplayParityError, match="positive"):
        replay_parity_report(two_gamma=_tg(), stage_discount=StageDiscount(),
                             win_vectors=[{"a": 0.0, "b": 0.0}])
