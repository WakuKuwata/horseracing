"""US4 mainline: HPO + OOF target encoding wired through the predictor stay leak-safe.

Consistency, beats-uniform and determinism must still hold with both features enabled. The
TE leak-safety itself is locked by unit tests (test_hpo_oof.py); here we exercise the full
predictor/harness plumbing end-to-end.
"""

from __future__ import annotations

import pytest
from horseracing_eval.baselines import UniformBaseline
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.harness import evaluate

from horseracing_training import LightGBMPredictor
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration

_TE = ("jockey_id", "trainer_id", "venue_code")


def _predictor(session):
    return LightGBMPredictor(session, seed=42, hpo=True, target_encode_cols=_TE)


def test_hpo_oof_path_beats_uniform_and_passes_consistency(session):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=12, field_size=8)
    races = load_eval_races(session)

    # evaluate() calls check_consistency on every valid race (fail-fast); reaching a result
    # means the HPO+OOF predictor satisfied probability consistency throughout.
    model = evaluate(_predictor(session), races, first_valid_year=2008)
    uniform = evaluate(UniformBaseline(), races, first_valid_year=2008)

    assert model.overall["win"]["log_loss"] < uniform.overall["win"]["log_loss"]
    # the TE columns were taken out of the native-categorical set
    fitted = _predictor(session)
    fitted.fit([er.context for er in races])
    assert set(fitted.te_cols_) <= set(_TE)
    assert all(c not in fitted.fit_info_["categorical_cols"] for c in fitted.te_cols_)


def test_hpo_oof_path_is_deterministic(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    races = load_eval_races(session)
    train_ctx = [er.context for er in races]
    target = races[-1].context

    p1 = _predictor(session)
    p1.fit(train_ctx)
    p2 = _predictor(session)
    p2.fit(train_ctx)

    assert p1.fit_info_["params"] == p2.fit_info_["params"]  # HPO picked the same params
    assert p1.predict_race(target) == p2.predict_race(target)
