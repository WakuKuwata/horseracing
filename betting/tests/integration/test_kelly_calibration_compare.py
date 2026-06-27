"""T018 (US2): raw / cal / cal+haircut Kelly risk comparison (Feature 017, SC-006/007/008)."""

from __future__ import annotations

import datetime

import pytest
from horseracing_probability.model_calibration import PCalibrator

from horseracing_betting.calibration_eval import compare_calibration_modes
from horseracing_betting.kelly_types import KellyConfig
from tests._synth import make_active_model, seed_learnable

pytestmark = pytest.mark.integration

_FROM = datetime.date(2008, 1, 1)
_TO = datetime.date(2008, 12, 31)
_NO_TAKEOUT = {bt: 1.0 for bt in ("place", "quinella", "exacta", "wide", "trio", "trifecta")}
_CFG = KellyConfig(lambda_real=0.5, lambda_est=0.25, cap_bet=0.05, cap_total=0.10, o_min=1.0,
                   min_edge=0.0, min_edge_est=0.0, bankroll=100.0,
                   haircut_type="relative", haircut=0.2)


def _cal(gamma):
    return PCalibrator(method="power", params={"gamma": gamma}, train_window=None, n_races=0,
                       n_samples=0, prob_range=(0.0, 1.0), select="explicit",
                       base_model_version=None, logic_version=f"pcal;gamma={gamma}", sufficient=True)


def _seed(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    return make_active_model(session, tmp_path, model_version="cc")


def test_compare_three_modes_metrics_and_gate(session, tmp_path):
    mv = _seed(session, tmp_path)
    rep = compare_calibration_modes(
        session, date_from=_FROM, date_to=_TO, cfg=_CFG, p_calibrator=_cal(0.6),
        modes=("raw", "cal", "cal+haircut"), payout_rates=_NO_TAKEOUT, model_version=mv,
        threshold=1.0, top_k=3, bootstrap_blocks=30, seed=99,
    )
    modes = {r.mode: r for r in rep.results}
    assert set(modes) == {"raw", "cal", "cal+haircut"}     # 3 modes (SC-006)
    for r in rep.results:
        s = r.segment
        assert s.n_bets >= 0
        assert 0.0 <= s.ruin_probability <= 1.0
        assert s.max_drawdown >= 0.0
    # raw is the baseline; cal/cal+haircut carry a risk-non-worse gate flag (SC-007/SC-008)
    assert modes["raw"].risk_not_worse is True
    assert isinstance(modes["cal"].risk_not_worse, bool)
    assert isinstance(modes["cal+haircut"].over_conservative, bool)
    assert "SUCCESS" in rep.verdict or "NOT-ADOPTED" in rep.verdict


def test_compare_deterministic(session, tmp_path):
    mv = _seed(session, tmp_path)
    kw = dict(date_from=_FROM, date_to=_TO, cfg=_CFG, p_calibrator=_cal(0.6),
              payout_rates=_NO_TAKEOUT, model_version=mv, threshold=1.0, top_k=3,
              bootstrap_blocks=30, seed=99)
    a = compare_calibration_modes(session, **kw)
    b = compare_calibration_modes(session, **kw)
    fa = [(r.mode, round(r.segment.terminal_bankroll, 6), round(r.segment.ruin_probability, 6))
          for r in a.results]
    fb = [(r.mode, round(r.segment.terminal_bankroll, 6), round(r.segment.ruin_probability, 6))
          for r in b.results]
    assert fa == fb
