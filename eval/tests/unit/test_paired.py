"""T011/T012a/T012b: paired_eval orchestration + gate, via DB-free fake factories.

A fake PredictorFactory returns caller-controlled win probs, so the paired pipeline (per-fold
refit loop, model-blind race set, winner NLL diff, recent-window AND guard, period boundaries,
contract failure) is exercised deterministically without the DB.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.paired import PairedContractError, paired_eval
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext


def _race(rid, year, month, winner_prob, n=4, winner="h0"):
    horses = tuple(HorseEntry(horse_id=f"h{i}") for i in range(n))
    # winner gets winner_prob; the rest split the remainder uniformly
    rest = (1.0 - winner_prob) / (n - 1)
    ctx = RaceContext(race_id=rid, race_date=datetime.date(year, month, 1), started_horses=horses)
    labels = (ScoringLabel(winner, 1, 1, 1), ScoringLabel("h1", 0, 1, 1), ScoringLabel("h2", 0, 0, 1))
    return EvalRace(context=ctx, labels=labels), winner_prob, rest, winner, n


class _FakePredictor:
    is_leaky_reference = False

    def __init__(self, win_for_winner: float):
        self._w = win_for_winner

    def fit(self, races):  # noqa: D401 - protocol stub
        return None

    def predict_race(self, ctx):
        n = len(ctx.started_horses)
        rest = (1.0 - self._w) / (n - 1)
        out = {}
        for h in ctx.started_horses:
            p = self._w if h.horse_id == "h0" else rest
            out[h.horse_id] = Prediction(win=p, top2=min(1.0, p * 2), top3=min(1.0, p * 3))
        return out


class _FakeFactory:
    def __init__(self, win_for_winner: float, tag: str):
        self._w = win_for_winner
        self.recipe_meta = {"tag": tag}
        self.recipe_hash = tag

    def fit(self, train_races, *, num_threads=None):
        return _FakePredictor(self._w)


def _races(years=(2020, 2021, 2022, 2023, 2024)):
    out = []
    for y in years:
        for m in (3, 9):
            out.append(_race(f"R{y}{m:02d}", y, m, 0.5)[0])
    return out


def test_same_factory_paired_diff_is_zero():
    races = _races()
    rep = paired_eval(_FakeFactory(0.5, "a"), _FakeFactory(0.5, "a"), races,
                      first_valid_year=2020, bootstrap_b=100)
    assert rep.periods["all"]["diff"] == pytest.approx(0.0, abs=1e-12)
    assert rep.gate.primary is False  # not strictly better


def test_swap_flips_paired_diff_sign():
    races = _races()
    ab = paired_eval(_FakeFactory(0.6, "cand"), _FakeFactory(0.4, "act"), races,
                     first_valid_year=2020, bootstrap_b=100)
    ba = paired_eval(_FakeFactory(0.4, "act"), _FakeFactory(0.6, "cand"), races,
                     first_valid_year=2020, bootstrap_b=100)
    assert ab.periods["all"]["diff"] == pytest.approx(-ba.periods["all"]["diff"], abs=1e-12)


def test_better_candidate_passes_winner_nll_driven_guards():
    races = _races()
    # candidate assigns MORE prob to the actual winner -> lower winner NLL -> better.
    # (top2/top3 non-inferiority and calibration depend on realistic probs, not asserted here;
    # this test isolates the winner-NLL/CI/recent conditions that the fake data controls.)
    rep = paired_eval(_FakeFactory(0.7, "cand"), _FakeFactory(0.4, "act"), races,
                      first_valid_year=2020, bootstrap_b=500)
    assert rep.periods["all"]["diff"] < 0
    assert rep.gate.primary is True
    assert rep.gate.stat_guard is True  # CI upper < 0
    assert rep.gate.recent_guard is True


def test_top2_top3_are_emitted_for_gate_input():
    races = _races()
    rep = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.5, "a"), races,
                      first_valid_year=2020, bootstrap_b=100)
    assert "top2_diff" in rep.gate.reasons and "top3_diff" in rep.gate.reasons


def test_recent_guard_and_is_conservative():
    # candidate better overall AND in both recent windows -> recent_guard passes
    races = _races()
    rep = paired_eval(_FakeFactory(0.7, "c"), _FakeFactory(0.4, "a"), races,
                      first_valid_year=2020, bootstrap_b=200)
    assert rep.gate.recent_guard is True
    assert "recent_3y" in rep.periods and "recent_5y" in rep.periods


def test_period_windows_partition_by_boundary():
    races = _races(years=(2019, 2020, 2021, 2022, 2023, 2024))
    rep = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.5, "a"), races,
                      first_valid_year=2019, bootstrap_b=100)
    # recent_3y must contain fewer or equal races than recent_5y (nested windows)
    assert rep.periods["recent_3y"]["n_races"] <= rep.periods["recent_5y"]["n_races"]
    assert rep.periods["recent_5y"]["n_races"] <= rep.periods["all"]["n_races"]


def test_paired_eval_is_deterministic_same_seed(monkeypatch):
    # Feature 073 US1 (T007, SC-003): same inputs + same bootstrap seed => bit-identical report
    # (winner NLL, paired diff, CI, tri-value decision). The eval contract must be reproducible;
    # the gate-config records the <1e-9 tolerance. Fake factories remove model-fit nondeterminism,
    # so the only stochastic element is the seeded bootstrap RNG — two runs must match exactly.
    races = _races()
    cfg = {"bootstrap": {"seed": 20260713, "b": 300}}
    r1 = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.5, "a"), races,
                     first_valid_year=2020, gate_config=cfg, subgroups=True)
    r2 = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.5, "a"), races,
                     first_valid_year=2020, gate_config=cfg, subgroups=True)
    tol = 1e-9
    assert abs(r1.periods["all"]["diff"] - r2.periods["all"]["diff"]) < tol
    assert abs(r1.bootstrap_ci["ci_low"] - r2.bootstrap_ci["ci_low"]) < tol
    assert abs(r1.bootstrap_ci["ci_high"] - r2.bootstrap_ci["ci_high"]) < tol
    assert abs(r1.bootstrap_ci["point"] - r2.bootstrap_ci["point"]) < tol
    # tri-value decision + audit provenance reproduce exactly
    assert r1.decision == r2.decision
    assert r1.gate_config_hash == r2.gate_config_hash
    # full serialized report is identical (strongest determinism assertion)
    assert r1.to_dict() == r2.to_dict()


def test_one_sided_missing_prediction_is_contract_failure():
    class _DropFactory(_FakeFactory):
        def fit(self, train_races, *, num_threads=None):
            class _P(_FakePredictor):
                def predict_race(self, ctx):
                    out = super().predict_race(ctx)
                    out.pop("h3", None)  # drop one started horse's prediction
                    return out
            return _P(self._w)

    races = _races()
    with pytest.raises(PairedContractError):
        paired_eval(_DropFactory(0.5, "c"), _FakeFactory(0.5, "a"), races,
                    first_valid_year=2020, bootstrap_b=50)
