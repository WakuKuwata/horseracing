"""T020 (US3): 2×2 (raw/cal p × raw/cal q) Kelly grid + double-correction (Feature 017, SC-009)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_probability.fl_bias import FLCalibrator
from horseracing_probability.model_calibration import PCalibrator

from horseracing_betting.calibration_eval import compare_pq_grid
from horseracing_betting.kelly_types import KellyConfig
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 12, 31)
_NO_TAKEOUT = {bt: 1.0 for bt in ("place", "quinella", "exacta", "wide", "trio", "trifecta")}
_CFG = KellyConfig(lambda_real=0.5, lambda_est=0.25, cap_bet=0.05, cap_total=0.10, o_min=1.0,
                   min_edge=0.0, min_edge_est=0.0, bankroll=100.0)


def _pcal(gamma):
    return PCalibrator(method="power", params={"gamma": gamma}, train_window=None, n_races=0,
                       n_samples=0, prob_range=(0.0, 1.0), select="explicit",
                       base_model_version=None, logic_version=f"pcal;gamma={gamma}", sufficient=True)


def _qcal(gamma):
    return FLCalibrator(method="power", params={"gamma": gamma}, train_window=None, n_races=0,
                        n_samples=0, odds_range=(0.0, 1.0), logic_version=f"fl;gamma={gamma}",
                        sufficient=True)


def test_pq_grid_four_cells(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path, model_version="pq")
    grid = compare_pq_grid(
        session, date_from=_FROM, date_to=_TO, cfg=_CFG,
        p_calibrator=_pcal(0.6), q_calibrator=_qcal(1.2),
        payout_rates=_NO_TAKEOUT, model_version=mv, threshold=1.0, top_k=3,
        bootstrap_blocks=20, seed=7,
    )
    # 2×2 = 4 cells, each a distinct (p_cal, q_cal) combination (SC-009)
    keys = {(c.p_cal, c.q_cal) for c in grid.cells}
    assert keys == {(False, False), (True, False), (False, True), (True, True)}
    for c in grid.cells:
        assert c.segment.n_bets >= 0
    assert isinstance(grid.double_correction_detected, bool)
