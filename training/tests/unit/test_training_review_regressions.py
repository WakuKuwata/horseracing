"""2026-07 training-logic review regressions (multi-codex, see memory training-logic-review).

Two confirmed bugs are locked here:
1. calib_split C/D: the "full-history" booster silently ran the default 70/30 calibration
   split, so it never learned the latest 30% of the train window (the arm's whole point).
2. artifacts.save_model_version: the FR-010 split-unit compatibility check ran AFTER the
   disk writes, so a refused overwrite still replaced the existing model's on-disk booster/
   calibrator while the DB row kept the old metrics.
"""

from __future__ import annotations

import datetime

import pytest

from horseracing_training.adoption import AdoptionDecision, AdoptionGate
from horseracing_training.artifacts import save_model_version
from horseracing_training.calib_split import OofCalibratedPredictor
from horseracing_training.calibration import split_train_by_day, split_train_by_time
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe


def test_cd_base_booster_carves_no_calibration_holdout():
    # Bug 1: _make_base must request calib_frac=0.0 — otherwise LightGBMPredictor.fit runs
    # its default 70/30 split and the "full-history" booster learns only the earliest 70%.
    pred = OofCalibratedPredictor(None, ModelRecipe())
    base = pred._make_base()
    assert base.calib_frac == 0.0
    assert base.calibration == "none"
    # recipe fidelity: drop_features flows through to the base booster
    dropped = OofCalibratedPredictor(
        None, ModelRecipe(drop_features=("f1", "f2"))
    )._make_base()
    assert dropped.drop_features == ("f1", "f2")


def test_zero_calib_frac_holds_out_nothing():
    # calib_frac=0.0 is an explicit no-holdout request; the n_calib=1 small-fraction
    # fallback must not fire (it previously stole 1 race / 1 day from the booster).
    race_ids = [f"r{i}" for i in range(10)]
    dates = {rid: datetime.date(2025, 1, 1 + i) for i, rid in enumerate(race_ids)}
    for fn in (split_train_by_time, split_train_by_day):
        model_mask, calib_mask = fn(race_ids, dates, calib_frac=0.0)
        assert model_mask.all(), fn.__name__
        assert calib_mask.sum() == 0, fn.__name__


def test_gamma_resets_when_refit_yields_no_samples(monkeypatch):
    # A factory-reused predictor must not carry a previous fold's γ into a fold whose OOF
    # sample set is empty — identity (γ=1) is the correct fallback.
    pred = OofCalibratedPredictor(None, ModelRecipe())

    class _DummyBase:
        def fit(self, races):
            return self

    monkeypatch.setattr(pred, "_make_base", lambda: _DummyBase())
    monkeypatch.setattr(pred, "_oof_samples", lambda races: [])
    pred.gamma_, pred.n_oof_samples_ = 1.7, 123  # pretend an earlier fold fit these
    pred.fit([])
    assert pred.gamma_ == 1.0
    assert pred.n_oof_samples_ == 0


class _FakeRow:
    """Duck-typed ModelVersion row: only metrics_summary is read before the FR-010 check."""

    def __init__(self, split_unit: str):
        self.metrics_summary = {"training": {"calibration_split_unit": split_unit}}


class _FakeSession:
    def __init__(self, row):
        self._row = row

    def get(self, _model, _key):
        return self._row


class _StubEval:
    valid_years: list = []

    def to_summary(self) -> dict:
        return {"eval": {"overall": {}}}


def test_split_unit_mismatch_rejects_before_any_disk_write(tmp_path):
    # Bug 2: the refusal must leave the existing model_version's artifacts untouched.
    predictor = LightGBMPredictor(None, calibration_split_unit="race_day_v1")
    predictor.fit_info_ = {
        "calibration_split_unit": "race_day_v1",
        "feature_cols": ["f1"],
    }
    with pytest.raises(ValueError, match="refusing to overwrite"):
        save_model_version(
            _FakeSession(_FakeRow("race_count_v1")),
            model_version="lgbm-ordertest",
            predictor=predictor,
            eval_result=_StubEval(),
            decision=AdoptionDecision(adopted=False, reasons={}),
            gate=AdoptionGate(ece_threshold=0.1),
            artifacts_root=tmp_path,
            feature_version="features-test",
        )
    art_dir = tmp_path / "model_versions" / "lgbm-ordertest"
    # nothing was written (previously model.txt/calibrator.pkl/preprocessor.pkl/metadata.json
    # had already replaced the existing artifacts by the time the check raised)
    assert not art_dir.exists()
