"""Feature 049 US2: A/B harness mechanics + pre-registered gate logic (T017)."""

from __future__ import annotations

import datetime
import random

import pytest
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_eval.stage_discount_eval import (
    WORST_FOLD_TOP3_DLOGLOSS_TOL,
    decide_gate,
    evaluate_stage_discount,
)


class _NoisyFav:
    """win mass on horse #1, but 2nd/3rd finish nearly at random -> the plain-Harville
    tail overstates the favourite's top2/top3, so a λ<1 discount should improve it."""

    is_leaky_reference = False

    def __init__(self, skill: float = 6.0) -> None:
        self.skill = skill

    def fit(self, train_races):  # noqa: ARG002
        return None

    def predict_race(self, race: RaceContext):
        from horseracing_eval.baselines import harville_topk

        horses = race.started_horses
        raw = [self.skill if h.horse_number == 1 else 1.0 for h in horses]
        s = sum(raw)
        win = [min(max(r / s, 1e-6), 1 - 1e-6) for r in raw]
        top2, top3 = harville_topk(win)
        return {
            h.horse_id: Prediction(win=win[i], top2=top2[i], top3=top3[i])
            for i, h in enumerate(horses)
        }


def _pl_pick(weights, rng):
    tot = sum(weights.values())
    r = rng.random() * tot
    acc = 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return next(iter(weights))


def _make_races(years, per_year=120, field=10, seed=0, lam_true=0.5):
    rng = random.Random(seed)
    races = []
    for y in years:
        for r in range(per_year):
            rid = f"{y:04d}05010{(r % 9) + 1:01d}{(r % 12) + 1:02d}"
            horses = tuple(
                HorseEntry(horse_id=f"{rid}_{i}", horse_number=i + 1) for i in range(field)
            )
            # win prob: skill on #1 like _NoisyFav; finishers sampled by PL with weight
            # win**lam_true so the true stage exponent is lam_true (interior, <1)
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
            labels = tuple(
                ScoringLabel(
                    horse_id=f"{rid}_{i}",
                    win=int(order.get(i) == 1),
                    top2=int(order.get(i, 9) <= 2),
                    top3=int(order.get(i, 9) <= 3),
                )
                for i in range(field)
            )
            races.append(EvalRace(
                context=RaceContext(rid, datetime.date(y, 6, 1), horses),
                labels=labels,
            ))
    return races


def test_win_metrics_identical_and_first_fold_identity():
    races = _make_races([2007, 2008, 2009], seed=1)
    rep = evaluate_stage_discount(_NoisyFav(), races, first_valid_year=2008, min_races=50)
    # win probs identical between baseline and candidate everywhere (INV-S2)
    assert rep.win_identical
    assert rep.win_max_abs_diff == 0.0
    assert rep.baseline["win"]["log_loss"] == rep.candidate["win"]["log_loss"]
    # first evaluated fold (2008) has no prior OOS -> identity fit
    assert rep.fold_lambdas[0]["valid_year"] == 2008
    assert rep.fold_lambdas[0]["n_fit"] == 0
    assert rep.fold_lambdas[0]["fallback"] or (
        rep.fold_lambdas[0]["lambda2"] == 1.0 and rep.fold_lambdas[0]["lambda3"] == 1.0
    )


def test_deterministic():
    races = _make_races([2007, 2008, 2009], seed=2)
    a = evaluate_stage_discount(_NoisyFav(), races, first_valid_year=2008, min_races=50)
    b = evaluate_stage_discount(_NoisyFav(), races, first_valid_year=2008, min_races=50)
    assert a.candidate == b.candidate
    assert a.fold_lambdas == b.fold_lambdas
    assert a.adopted == b.adopted


def test_discount_improves_overstated_tail():
    # enough prior data (2007) so 2008+ folds fit a real λ<1
    races = _make_races([2007, 2008, 2009, 2010], per_year=200, seed=5)
    rep = evaluate_stage_discount(_NoisyFav(), races, first_valid_year=2008, min_races=100)
    # at least one fold fit a non-identity discount
    assert any(not fl["fallback"] and fl["lambda3"] < 1.0 for fl in rep.fold_lambdas[1:])
    # candidate top3 LogLoss should not be worse overall (overstated tail is compressible)
    assert rep.candidate["top3"]["log_loss"] <= rep.baseline["top3"]["log_loss"] + 1e-9


# ---- pure gate logic (improve / regress) ------------------------------------


def _m(ll2, e2, ll3, e3):
    return {"top2": {"log_loss": ll2, "ece": e2}, "top3": {"log_loss": ll3, "ece": e3},
            "win": {"log_loss": 0.2, "ece": 0.001}}


def test_gate_adopts_on_clear_improvement():
    base = _m(0.34, 0.007, 0.43, 0.019)
    cand = _m(0.33, 0.005, 0.42, 0.010)
    primary, guard = decide_gate(base, cand, winning_folds_top3=12, worst_fold_top3_dloss=0.001, n_folds=18)
    assert primary and guard


def test_gate_rejects_when_ece_regresses():
    base = _m(0.34, 0.007, 0.43, 0.019)
    cand = _m(0.33, 0.008, 0.42, 0.020)  # ECE worse
    primary, _ = decide_gate(base, cand, winning_folds_top3=12, worst_fold_top3_dloss=0.001, n_folds=18)
    assert not primary


def test_gate_rejects_without_strict_majority():
    base = _m(0.34, 0.007, 0.43, 0.019)
    cand = _m(0.33, 0.005, 0.42, 0.010)
    primary, _ = decide_gate(base, cand, winning_folds_top3=9, worst_fold_top3_dloss=0.001, n_folds=18)
    assert not primary  # 9*2 == 18, not > 18


def test_gate_guard_fails_on_bad_fold():
    base = _m(0.34, 0.007, 0.43, 0.019)
    cand = _m(0.33, 0.005, 0.42, 0.010)
    _, guard = decide_gate(base, cand, winning_folds_top3=12,
                           worst_fold_top3_dloss=WORST_FOLD_TOP3_DLOGLOSS_TOL + 1e-4, n_folds=18)
    assert not guard
