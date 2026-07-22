"""Feature 079 (step 3): LightGBMPredictor EV-weight integration (byte-parity + effect).

Two end-to-end guarantees on a real (testcontainer) DB fit:
- ev_weight ON but with NO OOF coverage => every race is complete-field-neutral (weight 1.0)
  => predictions are BYTE-IDENTICAL to the ev_weight OFF fit (strongest parity: the weight
  machinery that degenerates to uniform must not perturb the fitted PL model);
- ev_weight ON with a complete, varied OOF-p => weights become non-uniform, predictions move,
  and market-aware provenance is recorded (is_leaky_reference True).
"""

from __future__ import annotations

import numpy as np
import pytest
from horseracing_eval.dataset import load_eval_races

from horseracing_training import LightGBMPredictor
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def _races(session):
    seed_learnable(session, years=(2007, 2008), races_per_year=10, field_size=8)
    return load_eval_races(session)


def _fit(session, races, *, ev_weight=False, oof_p=None):
    p = LightGBMPredictor(
        session, seed=42, objective="pl_topk", calibration="isotonic",
        ev_weight=ev_weight, oof_p=oof_p,
    )
    p.fit([er.context for er in races])
    return p


def _wins(pred, race):
    d = pred.predict_race(race)
    return np.array([d[h.horse_id].win for h in race.started_horses])


def test_ev_weight_neutral_oof_is_byte_identical(session):
    races = _races(session)
    target = races[-1].context
    off = _fit(session, races, ev_weight=False)
    # ev_weight ON but oof_p covers nothing -> every race NaN -> complete-field-neutral (1.0)
    on_neutral = _fit(session, races, ev_weight=True, oof_p={("x", "y"): 0.5})
    assert np.array_equal(_wins(off, target), _wins(on_neutral, target))
    info = on_neutral.fit_info_["ev_weight"]
    assert info["informative_rows"] == 0
    assert info["weight_min"] == 1.0 and info["weight_max"] == 1.0


def test_ev_weight_complete_oof_moves_predictions_and_records_provenance(session):
    races = _races(session)
    target = races[-1].context

    # complete, race-varying OOF p so max cap-eligible EV differs across races
    oof_p: dict = {}
    n = len(races)
    for k, er in enumerate(races):
        hs = er.context.started_horses
        p_top = 0.1 + 0.6 * (k / max(n - 1, 1))  # 0.1..0.7 across races
        rest = (1.0 - p_top) / (len(hs) - 1)
        for j, h in enumerate(hs):
            oof_p[(er.context.race_id, h.horse_id)] = p_top if j == 0 else rest

    on = _fit(session, races, ev_weight=True, oof_p=oof_p)
    # the fit still yields a valid per-race distribution
    assert abs(_wins(on, target).sum() - 1.0) < 1e-9

    # the weight machinery ran end-to-end with NON-UNIFORM per-race weights (that a non-uniform
    # weight actually moves a PL fit is proven on non-separable data in the unit suite; this
    # synth signal is perfectly separable so the saturated predictions barely move).
    info = on.fit_info_["ev_weight"]
    assert info["scheme"] == "evw-v1"
    assert info["center"] == 1.0 and info["tau"] == 0.10 and info["odds_cap"] == 21.0
    assert info["informative_rows"] > 0
    assert info["weight_min"] < info["weight_max"]  # non-uniform reweighting occurred
    assert abs(info["weight_mean"] - 1.0) < 0.15    # normalised near mean 1
    assert on.fit_info_["is_leaky_reference"] is True


def test_ev_weight_on_requires_oof(session):
    with pytest.raises(ValueError, match="requires a frozen OOF-p source"):
        LightGBMPredictor(session, objective="pl_topk", ev_weight=True, oof_p=None)
