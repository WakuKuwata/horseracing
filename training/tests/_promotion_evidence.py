"""Synthetic fitted metadata and in-memory registry for promotion boundary tests; no training/DB."""

from __future__ import annotations

from types import SimpleNamespace

from horseracing_db.models import ModelVersion
from horseracing_eval.decision import EVALUATION_CONTRACT_VERSION, gate_config_hash
from horseracing_eval.hashing import stable_hash
from horseracing_features.registry import FEATURE_VERSION, model_input_features
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.selectable import Select

from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.promotion_evidence import fitted_contract, stamp_report
from horseracing_training.win_model import DEFAULT_PARAMS

CFG = {
    "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
    "seed_noise": {"sd_fold": 0.001816},
    "eval_window": {"from": "2021-01-01", "to": "2021-12-31"},
}
WINDOW = {"from": "2021-01-01", "to": "2021-12-31"}


def predictor(*, seed=42):
    p = LightGBMPredictor(None, calibration="none", seed=seed)
    p.fit_info_ = {
        "seed": seed,
        "objective": "binary",
        "params": dict(DEFAULT_PARAMS),
        "feature_cols": model_input_features(),
        "categorical_cols": [],
        "race_class_representation": "canonical-v1",
        "target_encode_cols": [],
        "te_smoothing": None,
        "calib_frac": p.calib_frac,
        "calibration": "none",
        "calibration_split_unit": p.calibration_split_unit,
        "weight_mask": None,
        "model_degenerate": True,
    }
    return p


def factory(seed=42):
    meta = {"seed": seed, "objective": "binary", "calibration": "none"}
    return SimpleNamespace(
        _pred=predictor(seed=seed), recipe_meta=meta, recipe_hash=stable_hash(meta)
    )


def contracts():
    return {
        "candidate_contract": fitted_contract(predictor(seed=42), feature_version=FEATURE_VERSION),
        "active_contract": fitted_contract(predictor(seed=43), feature_version=FEATURE_VERSION),
    }


def confirmed_report(*, regime=False, status="ADOPT", assurance="full"):
    raw = (
        {
            "verdict": {"status": status, "subgroup_assurance": assurance},
            "gate_config": CFG,
            "artifact_kind": "full_walk_forward",
        }
        if regime
        else {
            "decision": status,
            "decision_reason": {"subgroup_assurance": assurance},
            "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        }
    )
    return stamp_report(
        raw,
        factory(42),
        factory(43),
        cfg=CFG,
        confirmed=True,
        expected_hash=gate_config_hash(CFG),
        window=WINDOW,
        feature_version=FEATURE_VERSION,
    )


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class Registry:
    def __init__(self):
        self.rows = {}
        self.statements = []
        self.commits = 0

    def get(self, model, key, *, populate_existing=False):
        return self.rows.get(key)

    def execute(self, stmt):
        self.statements.append(stmt)
        if isinstance(stmt, TextClause):
            assert str(stmt) == "LOCK TABLE model_versions IN SHARE ROW EXCLUSIVE MODE"
            return Result([])
        if isinstance(stmt, Select):
            active = [r for r in self.rows.values() if r.adoption_status == "active"]
            if stmt.column_descriptions[0]["name"] == "model_version":
                return Result([r.model_version for r in active])
            return Result(active)
        values = stmt.compile().params
        self.rows[values["model_version"]] = ModelVersion(
            **{k: v for k, v in values.items() if k in ModelVersion.__table__.columns}
        )
        return Result([])

    def commit(self):
        self.commits += 1


class StubEval:
    valid_years = []

    def to_summary(self):
        return {"eval": {"overall": {}}}
