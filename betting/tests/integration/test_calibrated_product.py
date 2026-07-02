"""Feature 046: p calibration in the product recommend path.

(a) 007 accepts a p_calibrator: EV/probs change under power γ, lv records it, None = original.
(b) product serve records pcal in logic_version (identity fallback in a thin-data env).
(c) walk-forward leak boundary: the fit never sees the target race (split_before).
"""

from __future__ import annotations

import argparse

import pytest
from horseracing_db.enums import BetType
from horseracing_db.models import Recommendation
from horseracing_probability.model_calibration import PCalibrator
from sqlalchemy import select

from horseracing_betting.cli import _cmd_recommend_serve, _fit_product_p_calibrator
from horseracing_betting.recommend import generate_recommendations
from tests._synth import make_active_model, make_prediction_run, seed_learnable

pytestmark = pytest.mark.integration

_RACE = "200801010101"  # race_date 2008-01-02


def _pcal(gamma: float) -> PCalibrator:
    return PCalibrator(
        method="power", params={"gamma": gamma}, train_window=None, n_races=10,
        n_samples=80, prob_range=(0.0, 1.0), select="explicit", base_model_version=None,
        logic_version=f"pcal=power(p^gamma);gamma={gamma:.5f};select=explicit",
        sufficient=True,
    )


def _setup(session, tmp_path):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    mv = make_active_model(session, tmp_path)
    return make_prediction_run(session, race_id=_RACE, model_version=mv)


def test_007_p_calibrator_changes_probs_and_records_lv(session, tmp_path):
    run_id = _setup(session, tmp_path)
    raw_ids = generate_recommendations(session, prediction_run_id=run_id, threshold=0.0)
    cal_ids = generate_recommendations(
        session, prediction_run_id=run_id, threshold=0.0, p_calibrator=_pcal(2.0))
    raw = {tuple(sorted(r.selection.items())): r for r in session.scalars(
        select(Recommendation).where(Recommendation.recommendation_id.in_(raw_ids)))}
    cal = {tuple(sorted(r.selection.items())): r for r in session.scalars(
        select(Recommendation).where(Recommendation.recommendation_id.in_(cal_ids)))}
    assert cal and all("pcal=power" in r.logic_version for r in cal.values())
    assert all("pcal=" not in r.logic_version for r in raw.values())  # None = original lv
    # γ=2 sharpens the race-normalized p → pseudo_roi differs for at least one shared selection
    shared = set(raw) & set(cal)
    assert shared and any(raw[s].pseudo_roi != cal[s].pseudo_roi for s in shared)


def test_walk_forward_fit_excludes_target_race(session, tmp_path):
    run_id = _setup(session, tmp_path)  # predictions exist ONLY for _RACE (2008-01-02)
    assert run_id is not None
    import datetime
    # cutoff at the target race itself → strictly-before excludes it → zero informative races
    pcal_before = _fit_product_p_calibrator(
        session, before_date=datetime.date(2008, 1, 2), target_race_id=_RACE)
    assert pcal_before.n_races == 0 and pcal_before.sufficient is False  # identity fallback
    # cutoff AFTER the race → the race enters the training window (boundary sanity)
    pcal_after = _fit_product_p_calibrator(
        session, before_date=datetime.date(2008, 2, 1), target_race_id="")
    assert pcal_after.n_races >= 1


def test_serve_records_pcal_identity_fallback(session, tmp_path, capsys):
    _setup(session, tmp_path)
    rc = _cmd_recommend_serve(session, argparse.Namespace(race_id=_RACE))
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("OK:") and "pcal=" in out          # calibrator applied + reported
    rows = session.scalars(select(Recommendation)).all()
    assert rows and all("pcal=" in r.logic_version for r in rows)
    # thin data (only this run persisted, strictly-before empty) → identity fallback recorded
    assert all("identity" in r.logic_version or "gamma=1.0" in r.logic_version for r in rows)
    # win group present too (045) and calibrated lv applied to both groups
    assert any(r.bet_type == BetType.WIN for r in rows)
