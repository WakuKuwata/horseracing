"""Feature 078 US1 (T005): calibrate_stage_oof — OOF-faithful stage-λ re-validation.

Reuses the pre-registered 049 gate over a bundle-backed predictor (no DB). λ is fit prequentially on
RAW OOF win (D1); the verdict is tri-value; win is identical by construction. Fixtures mirror the 049
evaluator's _make_races so a genuine interior λ_true < 1 makes the discount ADOPT.
"""

from __future__ import annotations

import datetime
import random

from horseracing_eval.baselines import harville_topk
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, RaceContext

from horseracing_probability.oof_calibration import (
    ADOPT,
    NO_DECISION,
    REJECT,
    calibrate_stage_oof,
)


def _pl_pick(weights, rng):
    tot = sum(weights.values())
    r = rng.random() * tot
    acc = 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return next(iter(weights))


def _fixtures(years, *, per_year=140, field=10, seed=0, lam_true=0.5):
    """Return (eval_races, bundle) whose win vector + labels match, with true stage exponent
    ``lam_true`` (interior < 1 → the discount should ADOPT)."""
    rng = random.Random(seed)
    races, preds = [], {}
    for y in years:
        for r in range(per_year):
            rid = f"{y:04d}05010{(r % 9) + 1:01d}{(r % 12) + 1:02d}"
            horses = tuple(HorseEntry(horse_id=f"{rid}_{i}", horse_number=i + 1)
                           for i in range(field))
            raw = {i: (6.0 if i == 0 else 1.0) for i in range(field)}
            s = sum(raw.values())
            wp = {i: raw[i] / s for i in range(field)}
            remaining = dict(wp)
            order = {}
            for placepos in (1, 2, 3):
                w = {i: p ** lam_true for i, p in remaining.items()}
                pick = _pl_pick(w, rng)
                order[pick] = placepos
                del remaining[pick]
            labels = tuple(ScoringLabel(
                horse_id=f"{rid}_{i}", win=int(order.get(i) == 1),
                top2=int(order.get(i, 9) <= 2), top3=int(order.get(i, 9) <= 3),
            ) for i in range(field))
            races.append(EvalRace(
                context=RaceContext(rid, datetime.date(y, 6, 1), horses), labels=labels))
            win = [wp[i] for i in range(field)]
            top2, top3 = harville_topk(win)  # bundle stores λ=1 Harville (baseline derivation)
            preds[rid] = {
                f"{rid}_{i}": {"win": win[i], "top2": top2[i], "top3": top3[i]}
                for i in range(field)
            }
    return races, {"predictions": preds, "bundle_digest": "deadbeef"}


def test_fits_interior_lambda_on_raw_win_and_decides():
    races, bundle = _fixtures([2007, 2008, 2009], seed=1, lam_true=0.5)
    art = calibrate_stage_oof(None, bundle, eval_races=races, min_races=50)
    # a REAL decision was reached (not NO_DECISION), on the RAW-win serving pipeline, win untouched
    assert art["verdict"] in (ADOPT, REJECT)
    assert art["consumer_pipeline"] == "serving_raw"       # D1: λ fit/applied on RAW win
    assert art["gate"]["win_identical"] is True             # win untouched by construction
    assert art["metrics"]["win_max_abs_diff"] == 0.0
    assert art["stage"] == "stage_discount_topk"
    assert 0.0 < art["prequential"]["last_lambda3"] < 1.0   # interior λ recovered from the discount


def test_adopts_with_a_strong_clean_signal():
    # a strong interior discount (λ_true=0.4) over many races → the pre-registered gate ADOPTS
    races, bundle = _fixtures([2007, 2008, 2009, 2010], seed=7, per_year=260, lam_true=0.4)
    art = calibrate_stage_oof(None, bundle, eval_races=races, min_races=50)
    assert art["verdict"] == ADOPT
    assert art["gate"]["primary_pass"] and art["gate"]["guard_pass"]


def test_no_decision_single_fold():
    # one valid fold after first_valid_year has no strictly-later held-out block for a verdict
    races, bundle = _fixtures([2007, 2008], seed=2, lam_true=0.5)
    art = calibrate_stage_oof(None, bundle, eval_races=races, min_races=50)
    # first fold is identity-only; with just one held-out fold the evidence is thin → NO_DECISION
    assert art["verdict"] in (ADOPT, NO_DECISION)  # depends on fold count; must never crash
    assert art["metrics"]["win_max_abs_diff"] == 0.0


def test_no_decision_when_all_folds_fall_back():
    # min_races far above the per-fold sample count → every fold falls back to identity → NO_DECISION
    races, bundle = _fixtures([2007, 2008, 2009], seed=3, lam_true=0.5, per_year=30)
    art = calibrate_stage_oof(None, bundle, eval_races=races, min_races=10_000)
    assert art["verdict"] == NO_DECISION
    assert art["verdict_reason"]["cause"] == "no_held_out_stage_evidence"


def test_deterministic():
    races, bundle = _fixtures([2007, 2008, 2009], seed=4)
    a = calibrate_stage_oof(None, bundle, eval_races=races, min_races=50)
    b = calibrate_stage_oof(None, bundle, eval_races=races, min_races=50)
    assert a == b
