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

    def _p(self):
        return self.base - (self.penalty if self._spec is not None else 0.0)

    def predict_race(self, ctx):
        p = self._p()
        return {"A": Prediction(p, p, p), "B": Prediction(1 - p, 1 - p, 1 - p)}

    def raw_win_probs(self, ctx):
        """Pre-calibration scores. Deliberately a different shape from the calibrated ones so a
        sign disagreement between the two views is expressible."""
        p = self._p()
        return ["A", "B"], [p * 0.9, 1 - p * 0.9]


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
    preds, valid, raw = predict_over_folds_multi(
        _Factory(p), _races(), regimes={SERVING: object(), FULL_INFO: None}, first_valid_year=2024
    )
    assert raw == {SERVING: {}, FULL_INFO: {}}  # not collected unless asked
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


def test_kill_test_a_swallowed_regime_fails_closed():
    """Break the wiring on the ACTIVE arm only: it accepts the spec and ignores it.

    Equal mask COUNTS cannot catch this — both arms were offered the regime, so the handshake
    looks fine and a full-info comparison would be reported under the label "serving". What IS
    observable is that the arm's serving scores come out bit-identical to its full-info scores,
    and that is what the contract check must key on."""

    class _OneSided(_Predictor):
        def set_predict_weight_mask(self, spec):  # accepts, then ignores
            self.applied.append(spec)

    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _OneSided(base=0.60, serving_penalty=0.10)
    with pytest.raises(PairedContractError, match="NO effect"):
        evaluate_regimes(
            _Factory(cand), _Factory(act), _races(),
            serving_spec=object(), gate_config=GATE, first_valid_year=2024,
        )


def test_kill_test_a_swallowed_regime_on_the_candidate_also_fails_closed():
    """Same guard must hold on the arm whose result we would like to be good."""

    class _OneSided(_Predictor):
        def set_predict_weight_mask(self, spec):
            self.applied.append(spec)

    with pytest.raises(PairedContractError, match="NO effect"):
        evaluate_regimes(
            _Factory(_OneSided(0.62, 0.02)), _Factory(_Predictor(0.60, 0.10)), _races(),
            serving_spec=object(), gate_config=GATE, first_valid_year=2024,
        )


def test_verdict_paths_quoted_in_the_contract_resolve():
    """The formula names serving_regime.gate.adopted and serving_regime.subgroups.subgroup_guard;
    a reader must be able to follow those paths in the emitted JSON."""
    rep = evaluate_regimes(
        _Factory(_Predictor(0.62, 0.02)), _Factory(_Predictor(0.60, 0.10)), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
    )
    d = rep.to_dict()
    assert isinstance(d["serving_regime"]["gate"]["adopted"], bool)
    assert "sub_gates" in d["serving_regime"]["gate"]
    assert "subgroups" in d["serving_regime"]


def test_uncalibrated_diagnostic_is_reported_per_regime():
    """Mandatory diagnostic: it must be possible to see whether the calibrator drove the result."""
    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _Predictor(base=0.60, serving_penalty=0.10)
    rep = evaluate_regimes(
        _Factory(cand), _Factory(act), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
    )
    for regime in (SERVING, FULL_INFO):
        assert rep.uncalibrated[regime]["available"]
        assert rep.uncalibrated[regime]["n_races"] > 0
    # it is a diagnostic: the verdict must not consult it
    assert "uncalibrated" not in rep.verdict


def test_missing_raw_scores_fail_closed_rather_than_dropping_the_diagnostic():
    """A silently absent diagnostic is worse than an error: the reversal check would just vanish."""

    class _NoRaw(_Predictor):
        raw_win_probs = None

        def __getattribute__(self, name):
            if name == "raw_win_probs":
                raise AttributeError(name)
            return object.__getattribute__(self, name)

    with pytest.raises(RegimeUnsupported, match="raw_win_probs"):
        predict_over_folds_multi(
            _Factory(_NoRaw(0.6, 0.0)), _races(),
            regimes={SERVING: object()}, first_valid_year=2024, collect_raw=True,
        )


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


GATE_WITH_SUBGROUPS = dict(
    GATE, subgroup_guard={"critical_subgroups": ["2026_only"], "decision": "three_way"}
)


def test_declared_subgroups_but_none_computed_is_NO_DECISION_not_adopt():
    """The contract is a three-term AND. A term that was never computed makes the verdict
    undecidable — reporting ADOPT off two of three terms is the exact failure this gate exists
    to prevent, and a favourable effect size makes it MORE tempting, not less."""
    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _Predictor(base=0.60, serving_penalty=0.10)
    rep = evaluate_regimes(
        _Factory(cand), _Factory(act), _races(),
        serving_spec=object(), gate_config=GATE_WITH_SUBGROUPS, first_valid_year=2024,
    )
    if rep.verdict["subgroup_guard"] is None:
        assert rep.verdict["status"] == "NO_DECISION"
        assert rep.verdict["adopt"] is False


def test_sub_gates_are_all_reported_so_a_partial_pass_is_visible():
    """Every term of `serving_regime.gate.adopted` must be inspectable, not just the headline."""
    cand = _Predictor(base=0.62, serving_penalty=0.02)
    act = _Predictor(base=0.60, serving_penalty=0.10)
    rep = evaluate_regimes(
        _Factory(cand), _Factory(act), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
    )
    sg = rep.verdict["sub_gates"]
    # v3: the regime path shares ONE gate implementation with the standard paired path, so it now
    # also carries the recent-window guard it silently omitted before.
    assert set(sg) == {
        "effect_beats_delta", "ci_upper_below_zero", "recent_no_evidence_of_harm", "top2_noninferior",
        "top3_noninferior", "calibration_noninferior", "calibration_not_emergency",
    }
    # primary is the AND of all of them, never a subset
    assert rep.verdict["primary"] == all(sg.values())


# --- the ONE expected exception: the m=1.0 control arm ------------------------------------------


class _RegimeInvariant(_Predictor):
    """An arm trained with every race masked: it reads no same-day weight, so the serving mask is
    correctly a no-op on it."""

    def set_predict_weight_mask(self, spec):
        self.applied.append(spec)


def test_regime_invariant_candidate_is_allowed_only_for_non_verdict_artifacts():
    """m=1.0 discards today's weight by construction, so serving == full-info for that arm. That is
    the property being measured, not a broken mask — and it is only tolerated on an artifact that
    can never decide anything."""
    rep = evaluate_regimes(
        _Factory(_RegimeInvariant(0.62, 0.02)), _Factory(_Predictor(0.60, 0.10)), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
        artifact_kind="diagnostic",
    )
    assert rep.notes["candidate_is_regime_invariant"] is True
    assert "regime_invariance_note" in rep.notes
    assert rep.eligible_for_verdict is False


def test_the_same_shape_still_fails_closed_on_a_verdict_artifact():
    """The relaxation must not leak into the run that decides adoption."""
    with pytest.raises(PairedContractError, match="NO effect"):
        evaluate_regimes(
            _Factory(_RegimeInvariant(0.62, 0.02)), _Factory(_Predictor(0.60, 0.10)), _races(),
            serving_spec=object(), gate_config=GATE, first_valid_year=2024,
        )


def test_both_arms_inert_is_still_a_fault_even_on_a_diagnostic():
    """A genuinely broken mask leaves BOTH arms inert. That is how a fault is told apart from the
    m=1.0 property, so it must keep failing closed regardless of artifact kind."""
    with pytest.raises(PairedContractError, match="NO effect"):
        evaluate_regimes(
            _Factory(_RegimeInvariant(0.62, 0.02)), _Factory(_RegimeInvariant(0.60, 0.10)),
            _races(), serving_spec=object(), gate_config=GATE, first_valid_year=2024,
            artifact_kind="diagnostic",
        )


def test_an_inert_active_arm_is_still_a_fault_on_a_diagnostic():
    """Only the CANDIDATE may be regime-invariant. An inert active arm means the baseline never
    experienced the condition it is supposed to be losing under."""
    with pytest.raises(PairedContractError, match="NO effect"):
        evaluate_regimes(
            _Factory(_Predictor(0.62, 0.02)), _Factory(_RegimeInvariant(0.60, 0.10)),
            _races(), serving_spec=object(), gate_config=GATE, first_valid_year=2024,
            artifact_kind="diagnostic",
        )


def test_non_verdict_artifacts_say_so_inside_the_verdict_block():
    """`"status": "ADOPT"` in a diagnostic file is a misreading hazard on its own. The loader
    refuses it, but the artifact should not need the loader to be read correctly."""
    rep = evaluate_regimes(
        _Factory(_Predictor(0.62, 0.02)), _Factory(_Predictor(0.60, 0.10)), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
        artifact_kind="diagnostic",
    )
    assert rep.verdict["advisory_only"] is True
    assert "CANNOT decide adoption" in rep.verdict["advisory_note"]


def test_a_real_verdict_carries_no_advisory_marker():
    rep = evaluate_regimes(
        _Factory(_Predictor(0.62, 0.02)), _Factory(_Predictor(0.60, 0.10)), _races(),
        serving_spec=object(), gate_config=GATE, first_valid_year=2024,
    )
    assert "advisory_only" not in rep.verdict
