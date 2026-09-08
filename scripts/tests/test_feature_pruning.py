"""110: pure checks of ablation scope, progression and cache fit identity."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_training.dataset import TrainingMatrix


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("feature_pruning_test_driver", ROOT / "scripts/feature_pruning.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.fixture
def cfg():
    return json.loads((ROOT / "specs/110-feature-pruning/gate-config.json").read_text())


@pytest.fixture
def matrix(monkeypatch):
    cols = ["jockey_win_rate", "trainer_win_rate", "other"]
    monkeypatch.setattr(driver, "columns_from_model", lambda: cols.copy())
    return TrainingMatrix(pd.DataFrame(columns=cols), cols, [])


@pytest.mark.parametrize("drops", [("missing",), ("jockey_win_rate", "jockey_win_rate")])
def test_scope_refuses_unknown_or_duplicate_drops(matrix, cfg, drops):
    with pytest.raises(ValueError, match="Unknown or duplicate"):
        driver.validate_scope(matrix, driver.make_recipe(cfg, drops))


def test_scope_preserves_baseline_and_exact_column_order(matrix, cfg):
    assert driver.validate_scope(matrix, driver.make_recipe(cfg, ("trainer_win_rate",))) == [
        "jockey_win_rate", "other"
    ]
    assert matrix.feature_cols == ["jockey_win_rate", "trainer_win_rate", "other"]


def _report(**updates):
    values = dict(diff=-0.0001, top2_diff=0.0, top3_diff=0.0, cand_ece=0.0, act_ece=0.0)
    values.update(updates)
    return SimpleNamespace(periods={"all": {"diff": values.pop("diff")}},
                           gate=SimpleNamespace(reasons=values))


@pytest.mark.parametrize("updates, expected", [
    ({}, True),
    ({"diff": 0.0}, False),
    ({"diff": float("nan")}, False),
    ({"top2_diff": 0.0005}, True),
    ({"top2_diff": 0.000500001}, False),
    ({"top3_diff": 0.0005}, True),
    ({"top3_diff": 0.000500001}, False),
    ({"cand_ece": 0.001}, True),
    ({"cand_ece": 0.001000001}, False),
    ({"cand_ece": None}, False),
    ({"act_ece": None}, False),
    ({"cand_ece": float("nan")}, False),
])
def test_progression_boundaries(cfg, updates, expected):
    assert driver.advances(_report(**updates), cfg) is expected


def _context(rid, year):
    return RaceContext(rid, dt.date(year, 1, 6), (HorseEntry("horse1"), HorseEntry("horse2")))


def test_cache_reuses_same_fit_but_not_changed_training_set(tmp_path, monkeypatch, matrix, cfg):
    monkeypatch.setattr(driver, "WORK", tmp_path)
    fits = []

    class FakeFactory:
        def __init__(self, session, recipe, **kwargs):
            self.recipe = recipe
            self.recipe_meta = recipe.meta()
            self.recipe_hash = recipe.recipe_hash

        def fit(self, races, **kwargs):
            fits.append(tuple(r.race_id for r in races))
            return SimpleNamespace(
                _base=SimpleNamespace(feature_cols_=matrix.feature_cols),
                oof_info_={"sufficient": True},
                predict_race=lambda race: {
                    h.horse_id: Prediction(0.5, 1.0, 1.0) for h in race.started_horses
                },
            )

    monkeypatch.setattr(driver, "CalibSplitFactory", FakeFactory)
    target = _context("valid", 2018)
    factory = driver.CachedFactory(cfg, matrix,
        [SimpleNamespace(context=target, n_result_rows=2, labels=())], identity="snapshot")
    train = [_context("train1", 2017)]
    first = factory.fit(train).predict_race(target)
    second = factory.fit(train).predict_race(target)
    assert first == second
    assert len(fits) == 1
    factory.fit([*train, _context("train2", 2017)])
    assert len(fits) == 2
    assert len(list((tmp_path / "cache").glob("*.pkl"))) == 2


def test_replay_refuses_changed_started_population():
    replay = driver.ReplayPredictor({"valid": {"horse1": Prediction(1.0, 1.0, 1.0)}})
    with pytest.raises(ValueError, match="population mismatch"):
        replay.predict_race(_context("valid", 2018))


def test_frozen_oof_outcomes_keep_dead_heats_completeness_and_restore_helper(
    tmp_path, monkeypatch, matrix, cfg
):
    import horseracing_training.calib_split as calib_split

    monkeypatch.setattr(driver, "WORK", tmp_path)
    original = calib_split._started_all_outcomes
    train = _context("train", 2017)
    er = SimpleNamespace(context=train, n_result_rows=1,
        labels=(SimpleNamespace(horse_id="horse1", win=1),
                SimpleNamespace(horse_id="horse2", win=1)))

    class InspectFactory:
        def __init__(self, session, recipe, **kwargs):
            assert session is None
            assert kwargs["require_sufficient"] is True
            self.recipe = recipe
            self.recipe_meta = recipe.meta()
            self.recipe_hash = recipe.recipe_hash

        def fit(self, races, **kwargs):
            # Preserve the row count as supplied, even when below field size: the
            # existing calibrator, not the driver, decides partial-ingest eligibility.
            assert calib_split._started_all_outcomes(None, ["train", "absent"]) == {
                "train": (1, {"horse1", "horse2"})
            }
            raise RuntimeError("deliberate fit failure")

    monkeypatch.setattr(driver, "CalibSplitFactory", InspectFactory)
    factory = driver.CachedFactory(cfg, matrix, [er], identity="snapshot")
    with pytest.raises(RuntimeError, match="deliberate fit failure"):
        factory.fit([train])
    assert calib_split._started_all_outcomes is original
    assert not list(tmp_path.rglob("*.pkl"))


def test_frozen_oof_outcomes_refuse_unknown_completeness(matrix, cfg):
    er = SimpleNamespace(context=_context("train", 2017), n_result_rows=None, labels=())
    with pytest.raises(ValueError, match="result-row counts"):
        driver.CachedFactory(cfg, matrix, [er], identity="snapshot")


@pytest.fixture
def frozen_run(tmp_path, monkeypatch, cfg):
    work = tmp_path / "work"
    model = tmp_path / "model"
    work.mkdir()
    model.mkdir()
    (work / "snapshot.pkl").write_bytes(b"frozen input bytes")
    (model / "model.txt").write_text("model bytes")
    meta = {"snapshot_sha256": driver.digest(work / "snapshot.pkl"),
            "config_hash": driver.gate_config_hash(cfg), "git_sha": "prepare-commit",
            "driver_sha256": "original-prepare-driver"}
    (work / "snapshot.json").write_text(json.dumps(meta))
    monkeypatch.setattr(driver, "WORK", work)
    monkeypatch.setattr(driver, "MODEL", model)
    monkeypatch.setattr(driver, "source_hash", lambda: "execution-source")
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: None)
    driver.freeze(cfg)
    return work, model, meta


def test_run_freeze_preserves_preparation_provenance_and_refuses_overwrite(frozen_run, cfg):
    work, _, original = frozen_run
    assert json.loads((work / "snapshot.json").read_text()) == original
    before = (work / "run-freeze.json").read_bytes()
    with pytest.raises(FileExistsError):
        driver.freeze(cfg)
    assert (work / "run-freeze.json").read_bytes() == before


@pytest.mark.parametrize("changed", ["snapshot", "metadata", "model", "source", "config"])
def test_run_rejects_changed_frozen_input_before_unpickling(frozen_run, monkeypatch, cfg, changed):
    work, model, _ = frozen_run
    if changed == "snapshot":
        (work / "snapshot.pkl").write_bytes(b"changed")
    elif changed == "metadata":
        (work / "snapshot.json").write_text("{}")
    elif changed == "model":
        (model / "model.txt").write_text("changed")
    elif changed == "source":
        monkeypatch.setattr(driver, "source_hash", lambda: "changed")
    else:
        cfg["arms"]["seed"] += 1
    # snapshot.pkl deliberately is not a pickle; rejecting the freeze must happen first.
    with pytest.raises(ValueError, match="changed since run freeze"):
        driver.run(cfg, "smoke")


def test_source_hash_detects_driver_dependency_and_added_source(tmp_path, monkeypatch):
    path = tmp_path / "scripts/feature_pruning.py"
    path.parent.mkdir()
    path.write_text("original driver")
    source = tmp_path / "training/src/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("original module")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "__file__", str(path))
    original = driver.source_hash()
    path.write_text("changed driver")
    assert driver.source_hash() != original
    path.write_text("original driver")
    assert driver.source_hash() == original
    source.write_text("changed module")
    assert driver.source_hash() != original
    source.write_text("original module")
    source.with_name("new.py").write_text("new dependency")
    assert driver.source_hash() != original
