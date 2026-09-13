from __future__ import annotations

import copy
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from horseracing_db.models import ModelVersion
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.decision import gate_config_hash
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_features.registry import FEATURE_VERSION

from horseracing_training.adoption import AdoptionDecision, AdoptionGate, evaluate_promotion
from horseracing_training.artifacts import save_model_version
from horseracing_training.promote import PromoteError, apply_promotion, plan_promotion
from horseracing_training.promotion_evidence import (
    fitted_contract,
    stamp_report,
)
from tests._promotion_evidence import (
    CFG,
    WINDOW,
    Registry,
    StubEval,
    confirmed_report,
    contracts,
    factory,
    predictor,
)


def promote(report, **over):
    return evaluate_promotion(
        legacy=AdoptionDecision(True, {}), verdict=report, **{**contracts(), **over}
    )


@pytest.mark.parametrize(
    "key", ["artifact_kind", "eligible_for_verdict", "can_adopt", "promotion_evidence"]
)
def test_required_provenance_never_defaults_to_approval(key):
    report = confirmed_report()
    del report[key]
    assert not promote(report).promotable


@pytest.mark.parametrize("key", ["eligible_for_verdict", "can_adopt"])
@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "false", {}, []])
def test_eligibility_requires_boolean_true(key, value):
    report = confirmed_report()
    report[key] = value
    assert not promote(report).promotable


@pytest.mark.parametrize("assurance", [None, False, "", "partial", 1])
def test_missing_or_partial_subgroup_assurance_cannot_promote(assurance):
    assert not promote(confirmed_report(assurance=assurance)).promotable


@pytest.mark.parametrize("value", [False, None, 1, "true", "false"])
def test_confirmation_requires_boolean_true(value):
    report = confirmed_report()
    report["promotion_evidence"]["confirmatory"] = value
    assert not promote(report).promotable


@pytest.mark.parametrize(
    "bad",
    [[], "ADOPT", 123, {"verdict": "ADOPT"}, {"gate_config": "v4"}, {"decision_reason": ["full"]}],
)
def test_malformed_json_shapes_fail_closed(bad):
    assert not promote(bad).promotable


@pytest.mark.parametrize("role", ["candidate", "active"])
@pytest.mark.parametrize("field", ["feature_cols", "calibration", "params", "seed"])
def test_actual_training_contract_mismatch_cannot_promote(role, field):
    from horseracing_eval.hashing import stable_hash

    expected = contracts()
    changed = copy.deepcopy(expected[role + "_contract"])
    changed["contract"][field] = {"different": True}
    changed["sha256"] = stable_hash(changed["contract"])
    expected[role + "_contract"] = changed
    result = promote(confirmed_report(), **expected)
    assert result.reasons["cause"] == role + "_training_contract_mismatch"


@pytest.mark.parametrize("role", ["candidate", "active"])
def test_report_from_another_recipe_is_rejected(role):
    report = confirmed_report()
    report[role + "_recipe_hash"] = "different"
    assert promote(report).reasons["cause"] == role + "_report_recipe_mismatch"


@pytest.mark.parametrize("bad_sd", [-1.0, float("nan"), float("inf"), True, "0.001", 10**1000])
def test_confirmation_cannot_claim_a_missing_retraining_variance(bad_sd):
    cfg = copy.deepcopy(CFG)
    cfg["seed_noise"]["sd_fold"] = bad_sd
    with pytest.raises((ValueError, RuntimeError)):
        stamp_report(
            confirmed_report(),
            factory(42),
            factory(43),
            cfg=cfg,
            confirmed=True,
            expected_hash=gate_config_hash(cfg),
            window=WINDOW,
            feature_version=FEATURE_VERSION,
        )


def register(registry, root, name, *, seed, verdict=None, candidate=True, reference=None):
    save_model_version(
        registry,
        model_version=name,
        predictor=predictor(seed=seed),
        eval_result=StubEval(),
        decision=AdoptionDecision(True, {}),
        gate=AdoptionGate(0.05),
        artifacts_root=root,
        feature_version=FEATURE_VERSION,
        register_as_candidate=candidate,
        verdict=verdict,
        reference_model_version=reference,
    )
    return registry.get(ModelVersion, name)


def registered_pair(tmp_path):
    registry = Registry()
    active = register(registry, tmp_path, "active", seed=43)
    active.adoption_status = "active"
    candidate = register(registry, tmp_path, "candidate", seed=42)
    return registry, active, candidate


def test_confirmed_report_normal_promotion_and_atomic_demotion(tmp_path):
    registry, old, candidate = registered_pair(tmp_path)
    plan = plan_promotion(
        registry, model_version="candidate", verdict=confirmed_report(), current_fv=FEATURE_VERSION
    )
    assert plan.ok and plan.basis == "v3_verdict"
    apply_promotion(registry, plan, at="2026-09-13T00:00:00Z")
    assert candidate.adoption_status == "active" and old.adoption_status == "candidate"
    assert old.metrics_summary["promotion"]["superseded_by"] == "candidate"
    assert candidate.metrics_summary["promotion"]["basis"] == "v3_verdict"
    assert candidate.metrics_summary["promotion"]["artifact_binding"] == plan.artifact_binding
    assert candidate.metrics_summary["promotion"]["v3_verdict"][
        "gate_config_hash"
    ] == gate_config_hash(CFG)


@pytest.mark.parametrize("role", ["active", "candidate"])
@pytest.mark.parametrize(
    "file", ["model.txt", "calibrator.pkl", "preprocessor.pkl", "metadata.json"]
)
def test_registered_body_swap_cannot_use_normal_promotion(tmp_path, role, file):
    registry, _, _ = registered_pair(tmp_path)
    path = tmp_path / "model_versions" / role / file
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(PromoteError, match="override"):
        plan_promotion(
            registry,
            model_version="candidate",
            verdict=confirmed_report(),
            current_fv=FEATURE_VERSION,
        )


def test_changed_active_or_artifact_after_plan_requires_new_preflight(tmp_path):
    registry, old, candidate = registered_pair(tmp_path)
    plan = plan_promotion(
        registry, model_version="candidate", verdict=confirmed_report(), current_fv=FEATURE_VERSION
    )
    path = tmp_path / "model_versions/candidate/model.txt"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(PromoteError, match="artifact changed"):
        apply_promotion(registry, plan, at="now")
    assert old.adoption_status == "active" and candidate.adoption_status == "candidate"
    old.adoption_status = "candidate"
    with pytest.raises(PromoteError, match="active model changed"):
        apply_promotion(registry, plan, at="now")


def test_old_models_keep_explicit_override_and_rollback(tmp_path):
    registry, old, candidate = registered_pair(tmp_path)
    del old.metrics_summary["training"]["promotion_identity"]
    del candidate.metrics_summary["training"]["promotion_identity"]
    with pytest.raises(PromoteError, match="override"):
        plan_promotion(
            registry,
            model_version="candidate",
            verdict=confirmed_report(),
            current_fv=FEATURE_VERSION,
        )
    plan = plan_promotion(
        registry,
        model_version="candidate",
        verdict=None,
        override_reason="  documented exceptional adoption  ",
        current_fv=FEATURE_VERSION,
    )
    apply_promotion(registry, plan, at="now")
    assert candidate.adoption_status == "active" and old.adoption_status == "candidate"
    assert (
        candidate.metrics_summary["promotion"]["override_reason"]
        == "documented exceptional adoption"
    )
    assert "--model-version active" in plan.rollback_command
    with pytest.raises(PromoteError):
        plan_promotion(
            registry, model_version="active", override_reason="  ", current_fv=FEATURE_VERSION
        )


def test_automatic_registration_does_not_create_two_actives(tmp_path):
    registry, active, _ = registered_pair(tmp_path)
    candidate = register(
        registry,
        tmp_path,
        "new",
        seed=42,
        candidate=False,
        verdict=confirmed_report(),
        reference="active",
    )
    assert candidate.adoption_status == "candidate" and active.adoption_status == "active"
    assert (
        candidate.metrics_summary["promotion"]["reasons"]["cause"]
        == "existing_active_requires_explicit_promotion"
    )


@pytest.mark.parametrize("candidate", [True, False])
def test_active_id_cannot_be_overwritten_before_disk_write(tmp_path, candidate):
    registry, active, _ = registered_pair(tmp_path)
    before = {p.name: p.read_bytes() for p in (tmp_path / "model_versions/active").iterdir()}
    with pytest.raises(ValueError, match="overwrite ACTIVE"):
        register(
            registry,
            tmp_path,
            "active",
            seed=42,
            candidate=candidate,
            verdict=confirmed_report(),
            reference="active",
        )
    after = {p.name: p.read_bytes() for p in (tmp_path / "model_versions/active").iterdir()}
    assert before == after and active.adoption_status == "active"


def test_initial_automatic_activation_requires_a_bound_registered_reference(tmp_path):
    registry = Registry()
    register(registry, tmp_path, "reference", seed=43)
    candidate = register(
        registry,
        tmp_path,
        "new",
        seed=42,
        candidate=False,
        verdict=confirmed_report(),
        reference="reference",
    )
    assert candidate.adoption_status == "active"


def test_actual_cli_report_can_register_and_promote_without_new_training_or_database(
    tmp_path, monkeypatch
):
    """Real CLI -> paired evaluator -> serialized envelope -> registry -> normal promotion."""
    import horseracing_eval.dataset as dataset

    from horseracing_training import cli

    class SyntheticFactory:
        def __init__(self, seed, p):
            values = factory(seed)
            self.__dict__.update(values.__dict__)
            self.p = p

        def fit(self, races, *, num_threads=None):
            def predict(ctx):
                win = [self.p, (1 - self.p) / 2, (1 - self.p) / 2]
                return {
                    h.horse_id: Prediction(win[i], [1.0, 0.9, 0.1][i], 1.0)
                    for i, h in enumerate(ctx.started_horses)
                }

            self._pred.predict_race = predict
            return self._pred

    races = []
    for year in (2020, 2021):
        for day in (1, 2):
            horses = tuple(HorseEntry(f"h{i}") for i in range(3))
            ctx = RaceContext(f"{year}0000000{day}", dt.date(year, 6, day), horses)
            labels = tuple(
                ScoringLabel(h.horse_id, int(i == 0), int(i <= 1), 1) for i, h in enumerate(horses)
            )
            races.append(EvalRace(ctx, labels, 3))
    monkeypatch.setattr(dataset, "load_eval_races", lambda *a, **k: races)
    monkeypatch.setattr(
        cli,
        "_factory_from_spec",
        lambda session, spec, **kw: (
            SyntheticFactory(42, 0.99) if spec == "candidate" else SyntheticFactory(43, 0.8)
        ),
    )
    cfg_path = tmp_path / "gate.json"
    cfg_path.write_text(json.dumps(CFG))
    output = tmp_path / "report.json"
    args = SimpleNamespace(
        candidate="candidate",
        active="active",
        gate_config=str(cfg_path),
        from_=dt.date(2021, 1, 1),
        to=dt.date(2021, 12, 31),
        first_valid_year=2021,
        confirmatory=True,
        gate_config_hash=gate_config_hash(CFG),
        seed=9,
        bootstrap_b=40,
        num_threads=1,
        json_out=str(output),
    )
    assert cli._paired_eval(None, args) == 0
    report = json.loads(output.read_text())
    assert report["decision"] == "ADOPT"
    registry, _, candidate = registered_pair(tmp_path)
    plan = plan_promotion(
        registry, model_version="candidate", verdict=report, current_fv=FEATURE_VERSION
    )
    assert plan.basis == "v3_verdict"
    apply_promotion(registry, plan, at="now")
    assert candidate.adoption_status == "active"
    # Same numerical result through the exploratory CLI must never supply normal promotion.
    args.confirmatory = False
    assert cli._paired_eval(None, args) == 0
    exploratory = json.loads(output.read_text())
    assert exploratory["decision"] == "ADOPT" and exploratory["can_adopt"] is False
    assert not promote(exploratory).promotable


def test_oof_contract_equals_its_servable_form_without_mutating_predictor():
    from horseracing_training.calib_split import OofCalibratedPredictor
    from horseracing_training.recipe import ModelRecipe

    oof = OofCalibratedPredictor(None, ModelRecipe(), method="isotonic", n_oof_blocks=8)
    oof._base = predictor()
    before = copy.deepcopy(oof._base.fit_info_)
    got = fitted_contract(oof, feature_version=FEATURE_VERSION)
    assert oof._base.fit_info_ == before
    oof._base.fit_info_.update(
        calibration="isotonic_strict_past_oof",
        calibration_split_unit=None,
        calibration_protocol={"protocol": "strict_past_oof_isotonic_v1", "n_oof_blocks": 8},
    )
    assert fitted_contract(oof._base, feature_version=FEATURE_VERSION) == got


def test_unrecognised_derived_builders_and_teacher_signals_cannot_claim_identity():
    from horseracing_training.calib_split import OofCalibratedPredictor
    from horseracing_training.predictor import LightGBMPredictor
    from horseracing_training.recipe import ModelRecipe

    class ChangedPredictor(LightGBMPredictor):
        pass

    class ChangedOof(OofCalibratedPredictor):
        pass

    derived = ChangedPredictor(None)
    derived.fit_info_ = predictor().fit_info_
    assert fitted_contract(derived, feature_version=FEATURE_VERSION) is None
    oof = ChangedOof(None, ModelRecipe(), method="isotonic")
    oof._base = predictor()
    assert fitted_contract(oof, feature_version=FEATURE_VERSION) is None
    base_oof = OofCalibratedPredictor(None, ModelRecipe(), method="isotonic")
    base_oof._base = derived
    assert fitted_contract(base_oof, feature_version=FEATURE_VERSION) is None
    for name in ("ev_weight", "market_offset", "margin_teacher"):
        p = predictor()
        p.fit_info_[name] = {"configured": True}
        assert fitted_contract(p, feature_version=FEATURE_VERSION) is None


def test_hpo_compares_selection_procedure_not_fold_specific_best_parameters():
    from horseracing_training.win_model import DEFAULT_PARAMS

    first, final = predictor(), predictor()
    for p in (first, final):
        p.hpo = True
        p.param_grid = [{"num_leaves": 15}, {"num_leaves": 31}]
    first.fit_info_["params"] = {**DEFAULT_PARAMS, "num_leaves": 15}
    final.fit_info_["params"] = {**DEFAULT_PARAMS, "num_leaves": 31}
    one = fitted_contract(first, feature_version=FEATURE_VERSION)
    assert one is not None and fitted_contract(final, feature_version=FEATURE_VERSION) == one
    final.param_grid = list(reversed(final.param_grid))
    assert fitted_contract(final, feature_version=FEATURE_VERSION) != one
    final.param_grid = [{"num_leaves": 63}]
    assert fitted_contract(final, feature_version=FEATURE_VERSION) is None


def test_explicit_ineligibility_cannot_be_upgraded_by_confirmation_stamp():
    report = confirmed_report()
    report["can_adopt"] = False
    stamped = stamp_report(
        report,
        factory(42),
        factory(43),
        cfg=CFG,
        confirmed=True,
        expected_hash=gate_config_hash(CFG),
        window=WINDOW,
        feature_version=FEATURE_VERSION,
    )
    assert stamped["can_adopt"] is False and not promote(stamped).promotable
