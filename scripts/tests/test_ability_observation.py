"""111: pure checks of addition scope, progression and cache fit identity."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_training.dataset import TrainingMatrix


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("ability_observation_test_driver", ROOT / "scripts/ability_observation.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.fixture
def cfg():
    return json.loads((ROOT / "specs/111-ability-observation/gate-config.json").read_text())


@pytest.fixture
def matrix(monkeypatch):
    cols = ["jockey_win_rate", "trainer_win_rate", "other"]
    monkeypatch.setattr(driver, "columns_from_model", lambda: cols.copy())
    return TrainingMatrix(pd.DataFrame(columns=cols + driver.OBSERVATION_COLUMNS), cols + driver.OBSERVATION_COLUMNS, [])


@pytest.mark.parametrize("drops", [("missing",), ("jockey_win_rate", "jockey_win_rate")])
def test_scope_refuses_unknown_or_duplicate_drops(matrix, cfg, drops):
    with pytest.raises(ValueError, match="Unknown or duplicate"):
        driver.validate_scope(matrix, driver.make_recipe(cfg, drops))


def test_scope_preserves_baseline_and_exact_column_order(matrix, cfg):
    assert driver.validate_scope(matrix, driver.make_recipe(cfg, ("trainer_win_rate",))) == [
        "jockey_win_rate", "other", *driver.OBSERVATION_COLUMNS
    ]
    assert matrix.feature_cols == ["jockey_win_rate", "trainer_win_rate", "other", *driver.OBSERVATION_COLUMNS]


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
    (work / "source-frames.pkl").write_bytes(b"raw source")
    meta = {"snapshot_sha256": driver.digest(work / "snapshot.pkl"),
            "config_hash": driver.gate_config_hash(cfg), "git_sha": "prepare-commit",
            "driver_sha256": "original-prepare-driver",
            "source_frames_sha256": driver.digest(work / "source-frames.pkl"),
            "prepare_source_hash": "execution-source"}
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
    path = tmp_path / "scripts/ability_observation.py"
    path.parent.mkdir()
    path.write_text("original driver")
    (path.parent / "ability_observation_features.py").write_text("builder source")
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


def _augmentation_fixture(monkeypatch):
    monkeypatch.setattr(driver, "columns_from_model", lambda: ["ability", "venue"])
    base = pd.DataFrame({"race_id": ["r2", "r1"], "horse_id": ["h2", "h1"],
                         "ability": [1.0, float("nan")],
                         "venue": pd.Categorical(["A", "B"]), "win": [1, 0]})
    matrix = TrainingMatrix(base, ["ability", "venue"], ["venue"])
    added = base[["race_id", "horse_id"]].iloc[::-1].copy()
    for c in driver.OBSERVATION_COLUMNS:
        added[c] = [float("nan"), 1.0]
    return matrix, added


def test_addition_join_keeps_baseline_labels_order_categories_and_arm_scope(monkeypatch, cfg):
    matrix, added = _augmentation_fixture(monkeypatch)
    augmented = driver.augment_matrix(matrix, added)
    pd.testing.assert_frame_equal(augmented.frame[matrix.frame.columns], matrix.frame, check_exact=True)
    assert augmented.categorical_cols == matrix.categorical_cols
    assert driver.validate_scope(augmented, driver.make_recipe(cfg, cfg["baseline_drop_features"])) == matrix.feature_cols
    for arm in cfg["candidates"]:
        assert driver.validate_scope(augmented, driver.make_recipe(cfg, arm["drop_features"])) == matrix.feature_cols + arm["add_features"]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "overwrite", "infinite", "negative", "dtype"])
def test_addition_join_fails_closed(monkeypatch, fault):
    matrix, added = _augmentation_fixture(monkeypatch)
    col = driver.OBSERVATION_COLUMNS[0]
    if fault == "missing":
        added = added.iloc[:1]
    elif fault == "duplicate":
        added = pd.concat([added, added])
    elif fault == "overwrite":
        matrix.frame[col] = 1.0
    elif fault == "infinite":
        added[col] = float("inf")
    elif fault == "negative":
        added[col] = -1.0
    else:
        added[col] = "1"
    with pytest.raises(ValueError):
        driver.augment_matrix(matrix, added)


def test_freeze_refuses_changed_feature_construction_source(frozen_run, monkeypatch, cfg):
    monkeypatch.setattr(driver, "source_hash", lambda: "changed builder")
    with pytest.raises(ValueError, match="construction source changed"):
        driver.freeze(cfg)


def test_freeze_refuses_changed_raw_source(frozen_run, cfg):
    work, _, _ = frozen_run
    (work / "source-frames.pkl").write_bytes(b"changed raw source")
    with pytest.raises(ValueError, match="Source frames changed"):
        driver.freeze(cfg)


@pytest.mark.parametrize("smoke", [True, False])
def test_identical_primary_losses_only_allowed_in_structural_smoke(smoke):
    report = SimpleNamespace(evidence=SimpleNamespace(rows=[SimpleNamespace(diff=0.0)]))
    if smoke:
        assert driver.check_effect_difference(report, smoke=True) is False
    else:
        with pytest.raises(ValueError, match="no efficacy verdict"):
            driver.check_effect_difference(report, smoke=False)


def test_nonzero_efficacy_difference_is_allowed():
    report = SimpleNamespace(evidence=SimpleNamespace(rows=[SimpleNamespace(diff=-0.01)]))
    assert driver.check_effect_difference(report, smoke=False) is True
