from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accuracy_style_recheck as d
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_training.dataset import TrainingMatrix
from horseracing_training.predictor import LightGBMPredictor


def toy():
    races, rows = [], []
    for i in range(4):
        rid, day = f"20240501{i + 1:02d}01", dt.date(2024, 1, i + 1)
        ctx = RaceContext(rid, day, (HorseEntry("A"), HorseEntry("B")))
        races.append(EvalRace(ctx, (ScoringLabel("A", 1, 1, 1), ScoringLabel("B", 0, 1, 1)), 2))
        for h in ("A", "B"):
            rows.append({"race_id": rid, "horse_id": h, "race_date": day, "win": int(h == "A"),
                         **{c: 0. for c in d.features.OVERLAY_COLUMNS},
                         **{c: 0. for c in d.OBSERVATION_COLUMNS}})
    frame = pd.DataFrame(rows)
    matrix = TrainingMatrix(frame, d.features.OVERLAY_COLUMNS + d.OBSERVATION_COLUMNS, [])
    overlay = frame[d.features.KEYS + d.features.OVERLAY_COLUMNS].copy()
    overlay["front_runner_rate"] = np.nan
    return matrix, overlay, races


def test_candidate_overlay_is_used_by_final_and_every_oof_base(monkeypatch):
    matrix, overlay, races = toy()
    seen = []
    def spy_fit(self, train, **kwargs):
        seen.append((self._data.frame.front_runner_rate.copy(), list(self._data.feature_cols),
                     [r.race_date for r in train]))
        return self
    monkeypatch.setattr(LightGBMPredictor, "fit", spy_fit)
    monkeypatch.setattr(LightGBMPredictor, "raw_win_probs", lambda self, ctx:
                        ([h.horse_id for h in ctx.started_horses], np.array([.6, .4])))
    p = d.make_predictor(matrix, "candidate", overlay, smoke=True)
    with d.frozen_outcomes(races):
        p.fit([r.context for r in races], num_threads=1)
    assert len(seen) == 2  # final full fit + the one 2-block strict-past OOF fit
    assert all(s.isna().all() for s, _, _ in seen)
    assert all(not set(cols) & set(d.OBSERVATION_COLUMNS) for _, cols, _ in seen)
    assert max(seen[1][2]) < dt.date(2024, 1, 3)
    assert matrix.frame.front_runner_rate.eq(0).all()
    baseline = d.make_predictor(matrix, "baseline", overlay, smoke=True)
    assert baseline._shared is matrix


def test_unknown_oof_request_does_not_fallback_to_db():
    _, _, races = toy()
    import horseracing_training.calib_split as cs
    with d.frozen_outcomes(races):
        assert cs._started_all_outcomes(None, [races[0].context.race_id])[races[0].context.race_id] == (2, {"A"})
        with pytest.raises(ValueError, match="outside"):
            cs._started_all_outcomes(None, ["not-frozen"])
        with pytest.raises(ValueError, match="outside"):
            cs._started_all_outcomes(object(), [races[0].context.race_id])


def test_metadata_cutoff_is_rejected_before_loading_binary_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "INPUT", tmp_path)
    (tmp_path / "snapshot.json").write_text(json.dumps({"data_through": "2026-09-07"}))
    monkeypatch.setattr(d, "digest", lambda p: pytest.fail("Forbidden metadata must stop before binary reads"))
    with pytest.raises(ValueError, match="boundary"):
        d.input_manifest()


def test_recipe_metadata_survives_json_and_has_fixed_training_fields():
    assert d.recipe_meta() == json.loads(json.dumps(d.recipe_meta()))
    r = d.recipe()
    assert r.seed == 42 and r.params == (("n_estimators", 900),)
    assert r.weight_mask_rate == .5 and r.weight_mask_seed == 20260810
    assert r.te_smoothing == 10 and r.drop_features == tuple(d.OBSERVATION_COLUMNS)
    assert d.REGIMES["full"] is None and d.REGIMES["preweight"].rate == 1


def test_prediction_coverage_order_and_three_head_constraints():
    _, _, races = toy()
    p = {r.context.race_id: {"A": (.6, 1., 1.), "B": (.4, 1., 1.)} for r in races}
    d.validate_predictions(p, races)
    with pytest.raises(ValueError, match="coverage"):
        d.validate_predictions({}, races)
    p[races[0].context.race_id]["A"] = (.6, .2, 1.)
    with pytest.raises(ValueError, match="constraints"):
        d.validate_predictions(p, races)


def test_contrast_reproduces_hand_nll_difference():
    _, _, races = toy()
    b = {r.context.race_id: {"A": (.6, 1., 1.), "B": (.4, 1., 1.)} for r in races}
    c = {r.context.race_id: {"A": (.7, 1., 1.), "B": (.3, 1., 1.)} for r in races}
    report = d.contrast(races, b, c, {"b": 20, "alpha": .05, "seed": 7})
    assert report["winner_nll_diff"] == pytest.approx(np.log(.6 / .7))
    assert report["top2_diff"] == 0 and report["top3_diff"] == 0
    assert report["n_eligible"] == 4
    assert all(r["diff"] == pytest.approx(np.log(.6 / .7)) for r in report["rows"])


def test_missing_predictions_raise_without_starting_a_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "WORK", tmp_path)
    (tmp_path / "baseline-reuse.json").write_text(json.dumps({"status": "REFIT_REQUIRED"}))
    monkeypatch.setattr(d, "fit", lambda *a, **k: pytest.fail("Read path must never fit"))
    with pytest.raises(FileNotFoundError):
        d.read_predictions("candidate", 2024, {}, toy()[2])


def test_cutoff_and_matrix_eval_population_are_checked():
    matrix, _, races = toy()
    d.validate_matrix_races(matrix, races)
    frame = matrix.frame.copy()
    frame.loc[0, "race_date"] = dt.date(2026, 9, 7)  # Synthetic guard only.
    with pytest.raises(ValueError, match="forbidden"):
        d.validate_matrix_races(TrainingMatrix(frame, matrix.feature_cols, []), races)
