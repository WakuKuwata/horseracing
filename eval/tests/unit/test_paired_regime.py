"""Feature 091 T032/T033/T035: the serving regime must reach BOTH arms, or the verdict is void.

The failure this guards against produces NUMBERS, not an error: if the mask lands on one arm only,
the report still fills in, the CI still looks tight, and the "improvement" is just the other arm
being handicapped. So the contract is checked structurally, and then the check itself is killed to
prove it was load-bearing (codex #3 — assertions that never fail guard nothing).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest
from horseracing_eval.foldfit import RegimeUnsupported, predict_over_folds_multi
from horseracing_eval.paired import PairedContractError
from horseracing_eval.predictor import Prediction
from horseracing_eval.regime_paired import FULL_INFO, SERVING, evaluate_regimes

GATE = {
    "min_effect_delta": 0.002,
    "full_info_guard": {"noninferior_width": 0.003},
    "bootstrap": {"b": 200, "seed": 1, "alpha": 0.05},
}


@dataclass
class _Horse:
    horse_id: str


@dataclass
class _Ctx:
    race_id: str
    race_date: dt.date
    started_horses: list


@dataclass
class _Label:
    horse_id: str
    win: int
    top2: int = 0
    top3: int = 0


@dataclass
class _EvalRace:
    context: _Ctx
    labels: list
    n_result_rows: int | None = None


def _races(n_days=6, per_day=3):
    """2023 supplies the expanding-window train side; 2024 is the validated year."""
    out = []
    for year in (2023, 2024):
        for d in range(n_days):
            for r in range(per_day):
                hs = [_Horse("A"), _Horse("B")]
                rid = f"{year}{d:02d}{r:02d}0101"
                out.append(
                    _EvalRace(
                        _Ctx(rid, dt.date(year, 1, 1) + dt.timedelta(days=d), hs),
                        [_Label("A", 1), _Label("B", 0)],
                        n_result_rows=2,
                    )
                )
    return out


class _Predictor:
    """Winner prob depends on the regime, so a one-sided application is visible in the numbers."""

    def __init__(self, base: float, serving_penalty: float, supports_regime: bool = True):
        self.base, self.penalty = base, serving_penalty
        self._spec = None
        self.applied: list = []
        if not supports_regime:
            del self.__dict__["_spec"]
            self.__class__ = type("NoRegime", (_NoRegimePredictor,), {})
            _NoRegimePredictor.__init__(self, base)

    def set_predict_weight_mask(self, spec):
        self._spec = spec
        self.applied.append(spec)

    def predict_race(self, ctx):
        p = self.base - (self.penalty if self._spec is not None else 0.0)
        return {"A": Prediction(p, p, p), "B": Prediction(1 - p, 1 - p, 1 - p)}


class _NoRegimePredictor:
    def __init__(self, base: float):
        self.base = base

    def predict_race(self, ctx):
        return {
            "A": Prediction(self.base, self.base, self.base),
            "B": Prediction(1 - self.base, 1 - self.base, 1 - self.base),
        }


class _Factory:
    def __init__(self, predictor):
        self._p = predictor

    def fit(self, train_races, *, num_threads=None):
        return self._p


def test_multi_regime_fits_once_and_predicts_under_each_regime():
    p = _Predictor(base=0.60, serving_penalty=0.10)
    preds, valid = predict_over_folds_multi(
        _Factory(p), _races(), regimes={SERVING: object(), FULL_INFO: None}, first_valid_year=2024
    )
    assert set(preds) == {SERVING, FULL_INFO}
    assert preds[SERVING] and preds[FULL_INFO]
    # the serving regime really changed the inputs, and the predictor was left reset afterwards
    any_race = next(iter(preds[SERVING]))
    assert preds[SERVING][any_race]["A"].win < preds[FULL_INFO][any_race]["A"].win
    assert p.applied[-1] is None


def test_non_default_regime_on_an_unsupporting_predictor_fails_closed():
    """Silently ignoring the regime would yield a full-info comparison labelled 'serving'."""
    p = _NoRegimePredictor(0.6)
    with pytest.raises(RegimeUnsupported):
        predict_over_folds_multi(
            _Factory(p), _races(), regimes={SERVING: object()}, first_valid_year=2024
        )


def test_both_arms_are_masked_and_the_counts_match():
    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _Predictor(base=0.60, serving_penalty=0.10)
    rep = evaluate_regimes(
        _Factory(cand), _Factory(act), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
    )
    srv = rep.serving_regime
    assert srv["mask_races_candidate"] == srv["mask_races_active"] > 0
    assert rep.full_info_regime["mask_races_candidate"] == 0  # full-info is spec=None


def test_serving_spec_none_is_refused():
    """None would collapse PRIMARY into full-info while still calling itself 'serving'."""
    with pytest.raises(PairedContractError, match="serving_spec"):
        evaluate_regimes(
            _Factory(_Predictor(0.6, 0.0)), _Factory(_Predictor(0.6, 0.0)), _races(),
            serving_spec=None, gate_config=GATE, first_valid_year=2024,
        )


def test_kill_test_one_sided_application_is_detected():
    """Break the wiring on the ACTIVE arm only; the contract check must fire (codex #3)."""

    class _OneSided(_Predictor):
        def set_predict_weight_mask(self, spec):  # swallows the regime
            self.applied.append(spec)

    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _OneSided(base=0.60, serving_penalty=0.10)
    rep = evaluate_regimes(
        _Factory(cand), _Factory(act), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
    )
    # counts still match (both arms were *offered* the regime), so the number-level guard cannot
    # see it — this is precisely why the predictor-side hook must be honoured, and why the
    # wiring smoke (T042) inspects the transformed matrix rather than call counts.
    assert rep.serving_regime["mask_races_candidate"] == rep.serving_regime["mask_races_active"]
    # ...and the active arm's serving score is identical to its full-info score, which IS visible:
    assert rep.serving_regime["active"]["winner_nll"] == rep.full_info_regime["active"]["winner_nll"]


def test_verdict_is_a_single_materialised_boolean():
    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _Predictor(base=0.60, serving_penalty=0.10)
    rep = evaluate_regimes(
        _Factory(cand), _Factory(act), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
    )
    assert isinstance(rep.verdict["adopt"], bool)
    assert "serving_regime.subgroups.subgroup_guard" in rep.verdict["formula"]
    assert rep.artifact_kind == "full_walk_forward" and rep.eligible_for_verdict


def test_acceptance_and_diagnostic_artifacts_are_not_verdict_eligible():
    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _Predictor(base=0.60, serving_penalty=0.10)
    for kind in ("acceptance", "diagnostic"):
        rep = evaluate_regimes(
            _Factory(cand), _Factory(act), _races(),
            serving_spec=object(), gate_config=GATE, first_valid_year=2024, artifact_kind=kind,
        )
        assert rep.artifact_kind == kind and not rep.eligible_for_verdict
