"""131: the mixture model_version rides the ordinary serving path without touching the booster one."""
import datetime as dt
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from horseracing_eval.predictor import Prediction
from horseracing_eval.stage_discount import StageDiscount

from horseracing_serving import mixture_model as mm
from horseracing_serving import mixture_serving as ms
from horseracing_serving import pipeline
from horseracing_serving.model_loader import ServingError


def _fake_bundle(valid_year=2026, sha="a" * 64):
    members = [{"id": m["id"], "correction": {"valid_year": valid_year}} for m in mm.IDENTITIES]
    return SimpleNamespace(sha256=sha, manifest={"members": members}, members=tuple(range(6)))


def _model(bundle=None):
    return ms.MixtureServingModel(
        model_version="mix-test", bundle=bundle or _fake_bundle(), feature_cols=["weight", "x"],
        categorical_cols=[], feature_version="features-021", feature_hash=mm.FULL_HASH, metadata={},
    )


@pytest.mark.parametrize(
    "meta",
    [
        {"artifact_kind": "lgbm"},
        {"artifact_kind": ms.ARTIFACT_KIND, "bundle_sha256": "zz"},
        {"artifact_kind": ms.ARTIFACT_KIND, "bundle_sha256": "a" * 64, "feature_version": "features-020",
         "feature_hash": mm.FULL_HASH},
        {"artifact_kind": ms.ARTIFACT_KIND, "bundle_sha256": "a" * 64, "feature_version": "features-021",
         "feature_hash": "b" * 64},
    ],
)
def test_loader_rejects_invalid_mixture_metadata(tmp_path, monkeypatch, meta):
    calls = []
    monkeypatch.setattr(ms, "load_mixture_bundle", lambda *a, **k: calls.append(a))
    with pytest.raises(ServingError):
        ms.load_mixture_serving_model("mix-test", tmp_path, meta)
    assert calls == []  # nothing is deserialised before the metadata is accepted


def test_loader_binds_bundle_sha_and_full_profile(tmp_path, monkeypatch):
    seen = {}

    def fake_load(path, *, expected_sha256=None):
        seen["path"], seen["sha"] = path, expected_sha256
        return _fake_bundle(sha=expected_sha256)

    monkeypatch.setattr(ms, "load_mixture_bundle", fake_load)
    monkeypatch.setattr(ms, "feature_profile", lambda: {"full_columns": ["weight", "sex", "race_class"]})
    meta = {"artifact_kind": ms.ARTIFACT_KIND, "bundle_sha256": "c" * 64,
            "feature_version": "features-021", "feature_hash": mm.FULL_HASH}
    model = ms.load_mixture_serving_model("mix-test", tmp_path, meta)
    assert seen == {"path": tmp_path / "bundle.json", "sha": "c" * 64}
    assert model.feature_cols == ["weight", "sex", "race_class"]
    assert model.bundle_sha12 == "c" * 12 and model.coefficient_year == 2026
    assert model.market_offset is None and model.booster is None and ms.is_mixture(model)


def test_model_loader_dispatches_on_artifact_kind(tmp_path, monkeypatch):
    from horseracing_serving import model_loader

    art = tmp_path / "mix"
    art.mkdir()
    (art / "bundle.json").write_text("{}")
    (art / "metadata.json").write_text(json.dumps({"artifact_kind": ms.ARTIFACT_KIND}))
    row = SimpleNamespace(model_version="mix-test", weights_uri=str(art / "bundle.json"),
                          calibrator_uri=str(art / "bundle.json"))
    session = SimpleNamespace(get=lambda cls, key: row if key == "mix-test" else None)
    sentinel = object()
    monkeypatch.setattr(ms, "load_mixture_serving_model", lambda mv, d, meta: sentinel)
    assert model_loader.load_serving_model(session, "mix-test") is sentinel


@pytest.mark.parametrize("valid,target,grace,ok", [
    (2026, 2026, 0, True), (2026, 2027, 0, False), (2026, 2027, 1, True),
    (2026, 2028, 1, False), (2026, 2025, 1, False),
])
def test_coefficient_year_grace(valid, target, grace, ok):
    if ok:
        assert mm.coefficient_year_offset(valid, target, grace) == target - valid
    else:
        with pytest.raises(ValueError):
            mm.coefficient_year_offset(valid, target, grace)


def test_ensure_race_date_adds_or_checks_the_day():
    rows = pd.DataFrame({"race_id": ["r1", "r1"], "horse_id": ["a", "b"], "weight": [1.0, 2.0]})
    dated = ms.ensure_race_date(rows, ["r1"], dt.date(2026, 9, 5))
    assert (dated.race_date == dt.date(2026, 9, 5)).all() and "race_date" not in rows.columns
    assert ms.ensure_race_date(dated, ["r1"], dt.date(2026, 9, 5)) is dated
    with pytest.raises(ServingError):
        ms.ensure_race_date(dated, ["r1"], dt.date(2026, 9, 6))


def test_predict_mixture_race_keeps_win_and_derives_display_heads(monkeypatch):
    ids = ["a", "b", "c", "d"]
    win = np.array([0.4, 0.3, 0.2, 0.1])
    rows = pd.DataFrame({"race_id": ["r1"] * 4, "horse_id": ids, "race_date": [dt.date(2026, 9, 5)] * 4,
                         "weight": [480.0, 470.0, 500.0, 460.0], "x": [1, 2, 3, 4]})
    monkeypatch.setattr(ms, "prepare_race_inputs", lambda fr, rid, regime: (rows, {"regime": regime}))
    monkeypatch.setattr(ms, "history_for", lambda rows, raw: pd.DataFrame(columns=["race_id", "horse_id", "race_date"]))
    captured = {}

    def fake_predict(bundle, rid, fr, history, regime, *, coefficient_grace_years):
        captured["regime"], captured["grace"] = regime, coefficient_grace_years
        preds = {h: Prediction(w, min(1.0, w + .3), min(1.0, w + .5)) for h, w in zip(ids, win, strict=True)}
        return SimpleNamespace(predictions=preds, audit={"bundle_sha256": "a" * 64, "coefficient_stale_years": 0})

    monkeypatch.setattr(ms, "predict_mixture", fake_predict)
    model = _model()
    sd = StageDiscount(lambda2=0.85, lambda3=0.7, n_races_l2=100, n_races_l3=100)
    plain, snaps, expl, audit = ms.predict_mixture_race(model, "r1", rows, pd.DataFrame(), stage_discount=None)
    disc, _, _, _ = ms.predict_mixture_race(model, "r1", rows, pd.DataFrame(), stage_discount=sd)
    assert captured == {"regime": "serving", "grace": ms.COEFFICIENT_GRACE_YEARS}
    # WIN is the mixture's own vector under both display settings; top2/top3 follow the stage rule.
    assert np.allclose([plain[h].win for h in ids], win) and np.allclose([disc[h].win for h in ids], win)
    assert plain["a"].top2 != disc["a"].top2 and plain["a"].top3 != disc["a"].top3
    assert abs(sum(p.top2 for p in plain.values()) - 2.0) < 1e-9 and abs(sum(p.top3 for p in plain.values()) - 3.0) < 1e-9
    assert set(snaps) == set(ids) and snaps["a"]["_raw_win"] == snaps["a"]["_calibrated_win"] == 0.4
    assert snaps["a"]["weight"] == 480.0 and expl == {h: None for h in ids}
    assert audit["regime_audit"] == {"regime": "serving"} and audit["n_rows"] == 4


def test_logic_version_carries_bundle_identity_and_coefficient_year(monkeypatch):
    monkeypatch.setattr(pipeline, "FEATURE_VERSION", "features-021")
    model = _model()
    lv = pipeline._base_logic_version(model)
    assert ";mix=6:" + "a" * 12 + ";coef=2026" in lv and ";mkt=" not in lv


def test_predict_persist_requires_history_for_mixture():
    with pytest.raises(ServingError):
        pipeline._predict_persist(None, _model(), "r1", pd.DataFrame(), "lv", history=None)
