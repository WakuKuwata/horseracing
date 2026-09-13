"""Real small fits + disposable Postgres: evaluated recipe, registered bytes, ACTIVE transition."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest
from horseracing_db.models import ModelVersion
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.decision import EVALUATION_CONTRACT_VERSION, gate_config_hash
from horseracing_features.registry import FEATURE_VERSION
from sqlalchemy import select, text, update

from horseracing_training import cli
from horseracing_training.adoption import AdoptionDecision, AdoptionGate
from horseracing_training.artifacts import save_model_version
from horseracing_training.promote import PromoteError, apply_promotion, plan_promotion
from horseracing_training.promotion_evidence import fitted_contract, registered_contract
from horseracing_training.recipe import ModelRecipe, RecipeFactory
from tests._synth import seed_learnable

pytestmark = pytest.mark.integration


def test_tiny_real_fit_cli_registration_and_postgres_promotion(session, tmp_path, monkeypatch):
    seed_learnable(session, years=(2007, 2008), races_per_year=6, field_size=4)
    params = {"learning_rate": 1.0, "min_child_samples": 1, "reg_lambda": 0.0, "num_leaves": 4}
    recipes = {
        name: ModelRecipe(
            objective="binary",
            calibration="none",
            calib_frac=0.0,
            target_encode_cols=(),
            params=tuple({**params, "n_estimators": n}.items()),
        )
        for name, n in (("candidate", 5), ("active", 1))
    }
    monkeypatch.setattr(
        cli, "_factory_from_spec", lambda sess, spec, **kw: RecipeFactory(sess, recipes[spec])
    )
    cfg = {
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "seed_noise": {"sd_fold": 0.001816},
        "bootstrap": {"b": 40, "seed": 9, "alpha": 0.05},
        "eval_window": {"from": "2008-01-01", "to": "2008-12-31", "min_eval_days": 2},
    }
    path, output = tmp_path / "gate.json", tmp_path / "report.json"
    path.write_text(json.dumps(cfg))
    args = SimpleNamespace(
        candidate="candidate",
        active="active",
        gate_config=str(path),
        from_=dt.date(2008, 1, 1),
        to=dt.date(2008, 12, 31),
        first_valid_year=2008,
        confirmatory=True,
        gate_config_hash=gate_config_hash(cfg),
        seed=9,
        bootstrap_b=40,
        num_threads=1,
        json_out=str(output),
    )
    assert cli._paired_eval(session, args) == 0
    report = json.loads(output.read_text())
    assert report["decision"] == "ADOPT"
    races = [r.context for r in load_eval_races(session)]
    result = SimpleNamespace(valid_years=[2008], to_summary=lambda: {"eval": {"overall": {}}})
    for name in ("active", "candidate"):
        final = RecipeFactory(session, recipes[name]).fit(races, num_threads=1)
        identity = fitted_contract(final, feature_version=FEATURE_VERSION)
        assert identity is not None
        assert identity == report["promotion_evidence"][name]["fitted_contract"]
        save_model_version(
            session,
            model_version=name,
            predictor=final,
            eval_result=result,
            decision=AdoptionDecision(True, {}),
            gate=AdoptionGate(0.05),
            artifacts_root=tmp_path,
            feature_version=FEATURE_VERSION,
            register_as_candidate=True,
        )
        row = session.get(ModelVersion, name)
        assert registered_contract(row) == (identity, None)
    # Bootstrap the first default using the preserved explicit override pathway.
    initial = plan_promotion(
        session,
        model_version="active",
        current_fv=FEATURE_VERSION,
        override_reason="synthetic test reference, no previous active",
    )
    apply_promotion(session, initial, at="2026-09-13T00:00:00Z")
    save_model_version(
        session,
        model_version="auto-candidate",
        predictor=final,
        eval_result=result,
        decision=AdoptionDecision(True, {}),
        gate=AdoptionGate(0.05),
        artifacts_root=tmp_path,
        feature_version=FEATURE_VERSION,
        verdict=report,
        reference_model_version="active",
    )
    automatic = session.get(ModelVersion, "auto-candidate")
    assert automatic.adoption_status == "candidate"
    assert (
        automatic.metrics_summary["promotion"]["reasons"]["cause"]
        == "existing_active_requires_explicit_promotion"
    )
    # The actual confirmed evaluation now justifies the ordinary transition, without override.
    plan = plan_promotion(
        session, model_version="candidate", verdict=report, current_fv=FEATURE_VERSION
    )
    assert plan.basis == "v3_verdict"
    apply_promotion(session, plan, at="2026-09-13T00:01:00Z")
    active = session.scalars(
        select(ModelVersion).where(ModelVersion.adoption_status == "active")
    ).all()
    assert [r.model_version for r in active] == ["candidate"]
    assert session.get(ModelVersion, "active").adoption_status == "candidate"


def test_export_holds_lock_and_refreshes_registry_after_concurrent_commit(
    session, tmp_path, monkeypatch
):
    import copy

    from horseracing_training import artifacts
    from tests._promotion_evidence import StubEval, confirmed_report, predictor

    original_write = artifacts._write_model
    writes = []

    def checked_write(pred, path):
        # Another promotion/registration cannot enter its critical section while files change.
        assert (
            session.execute(
                text("""SELECT count(*) FROM pg_locks
            WHERE pid = pg_backend_pid() AND relation = 'model_versions'::regclass
              AND mode = 'ShareRowExclusiveLock' AND granted""")
            ).scalar_one()
            == 1
        )
        writes.append(path)
        return original_write(pred, path)

    monkeypatch.setattr(artifacts, "_write_model", checked_write)

    def save(name, seed):
        save_model_version(
            session,
            model_version=name,
            predictor=predictor(seed=seed),
            eval_result=StubEval(),
            decision=AdoptionDecision(True, {}),
            gate=AdoptionGate(0.05),
            artifacts_root=tmp_path,
            feature_version=FEATURE_VERSION,
            register_as_candidate=True,
        )

    save("active", 43)
    save("candidate", 42)
    cached = session.get(ModelVersion, "candidate")
    assert cached.adoption_status == "candidate"
    # A separate connection commits a promotion while the saving Session still caches CANDIDATE.
    with session.get_bind().begin() as conn:
        conn.execute(
            update(ModelVersion)
            .where(ModelVersion.model_version == "candidate")
            .values(adoption_status="active")
        )
    assert cached.adoption_status == "candidate"
    before = {p.name: p.read_bytes() for p in (tmp_path / "model_versions/candidate").iterdir()}
    with pytest.raises(ValueError, match="overwrite ACTIVE"):
        save("candidate", 42)
    assert len(writes) == 2
    assert before == {
        p.name: p.read_bytes() for p in (tmp_path / "model_versions/candidate").iterdir()
    }
    session.rollback()

    with session.get_bind().begin() as conn:
        conn.execute(
            update(ModelVersion)
            .where(ModelVersion.model_version == "candidate")
            .values(adoption_status="candidate")
        )
        conn.execute(
            update(ModelVersion)
            .where(ModelVersion.model_version == "active")
            .values(adoption_status="active")
        )
    plan = plan_promotion(
        session, model_version="candidate", verdict=confirmed_report(), current_fv=FEATURE_VERSION
    )
    # A registry binding can change after preflight without changing files. Fresh ORM reads in
    # apply must see that committed update even while cached holds the original summary.
    summary = copy.deepcopy(cached.metrics_summary)
    summary["training"]["promotion_identity"]["changed_after_plan"] = True
    with session.get_bind().begin() as conn:
        conn.execute(
            update(ModelVersion)
            .where(ModelVersion.model_version == "candidate")
            .values(metrics_summary=summary)
        )
    assert "changed_after_plan" not in cached.metrics_summary["training"]["promotion_identity"]
    with pytest.raises(PromoteError, match="artifact changed"):
        apply_promotion(session, plan, at="now")
