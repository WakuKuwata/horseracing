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
                         "weight": [480.0, 470.0, 500.0, 460.0], "x": [1, 2, 3, 4], "career_starts": [0.0] * 4})
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


# --- 2026-09-13 review additions -------------------------------------------------------------

def test_loader_rejects_non_raw_registry_representation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ms, "load_mixture_bundle", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(ms.feature_registry, "RACE_CLASS_REPRESENTATION", "canonical-v1", raising=False)
    meta = {"artifact_kind": ms.ARTIFACT_KIND, "bundle_sha256": "c" * 64,
            "feature_version": "features-021", "feature_hash": mm.FULL_HASH}
    with pytest.raises(ServingError):
        ms.load_mixture_serving_model("mix-test", tmp_path, meta)
    assert calls == []


def _wired(monkeypatch, ids, win, day=dt.date(2026, 9, 5)):
    rows = pd.DataFrame({"race_id": ["r1"] * len(ids), "horse_id": ids, "race_date": [day] * len(ids),
                         "weight": [480.0] * len(ids), "x": list(range(len(ids))),
                         "career_starts": [0.0] * len(ids)})
    monkeypatch.setattr(ms, "prepare_race_inputs", lambda fr, rid, regime: (rows, {"regime": regime}))
    monkeypatch.setattr(ms, "history_for", lambda rows, raw: pd.DataFrame(columns=["race_id", "horse_id", "race_date"]))

    def fake_predict(bundle, rid, fr, history, regime, *, coefficient_grace_years):
        preds = {h: Prediction(w, min(1.0, w + .3), min(1.0, w + .5)) for h, w in zip(ids, win, strict=True)}
        return SimpleNamespace(predictions=preds, audit={"bundle_sha256": "a" * 64, "coefficient_stale_years": 0})

    monkeypatch.setattr(ms, "predict_mixture", fake_predict)
    return rows


def test_sub_clip_mean_win_is_persisted_exactly(monkeypatch):
    """A corrected six-member mean can sit below the booster path's DEFAULT_CLIP (1e-6). The
    display assembly must not clip it up (which renormalises the whole race): the persisted win is
    the mixture's own vector, bit for bit (2026 backfill audit: 27 races differed before the fix)."""
    from horseracing_eval.consistency import check_consistency

    ids = ["a", "b", "c", "d", "e"]
    win = np.array([0.6, 0.3, 0.05, 0.0499995, 5e-7])
    assert abs(win.sum() - 1.0) < 1e-12 and win.min() < 1e-6
    rows = _wired(monkeypatch, ids, win)
    sd = StageDiscount(lambda2=0.85, lambda3=0.7, n_races_l2=100, n_races_l3=100)
    for discount in (None, sd):
        preds, snaps, _, _ = ms.predict_mixture_race(_model(), "r1", rows, pd.DataFrame(), stage_discount=discount)
        assert [preds[h].win for h in ids] == win.tolist()          # exact, not merely close
        assert snaps["e"]["_raw_win"] == preds["e"].win == 5e-7
        check_consistency(preds)


def test_extreme_win_vector_keeps_consistency(monkeypatch):
    """18 runners, one near-certain winner: win untouched, heads finite/ordered/summing to 2 and 3."""
    from horseracing_eval.consistency import check_consistency

    ids = [f"h{i:02d}" for i in range(18)]
    win = np.full(18, (1 - 0.99999) / 17)
    win[0] = 1 - win[1:].sum()
    rows = _wired(monkeypatch, ids, win)
    sd = StageDiscount(lambda2=0.85, lambda3=0.7, n_races_l2=100, n_races_l3=100)
    preds, _, _, _ = ms.predict_mixture_race(_model(), "r1", rows, pd.DataFrame(), stage_discount=sd)
    assert [preds[h].win for h in ids] == win.tolist()
    assert all(np.isfinite([p.win, p.top2, p.top3]).all() and p.win <= p.top2 <= p.top3 <= 1 for p in preds.values())
    check_consistency(preds)


def test_history_for_started_strict_past_in_scope_only():
    target = pd.DataFrame({"horse_id": ["a", "b"], "race_date": [dt.date(2026, 9, 13)] * 2})
    raw = pd.DataFrame({
        "race_id": ["same", "future", "cancel", "pre2007", "ok1", "ok2", "other"],
        "horse_id": ["a", "a", "a", "a", "a", "b", "z"],
        "race_date": ["2026-09-13", "2026-09-20", "2026-09-06", "2006-12-31", "2026-08-30", "2026-01-05", "2026-08-30"],
        "entry_status": ["started", "started", "cancelled", "started", "started", "started", "started"],
    })
    out = ms.history_for(target, raw)
    assert out.race_id.tolist() == ["ok1", "ok2"] and "entry_status" not in out.columns
    with pytest.raises(ValueError):
        ms.history_for(target, raw.assign(entry_status=["bogus"] + ["started"] * 6))


def test_history_for_rejects_several_target_dates_per_horse():
    target = pd.DataFrame({"horse_id": ["a", "a"], "race_date": [dt.date(2026, 9, 6), dt.date(2026, 9, 13)]})
    raw = pd.DataFrame({"race_id": ["r"], "horse_id": ["a"], "race_date": ["2026-09-10"], "entry_status": ["started"]})
    with pytest.raises(ValueError):
        ms.history_for(target, raw)
    # the same horse twice on ONE day (duplicate rows of one race) is still a single target date
    assert ms.history_for(pd.DataFrame({"horse_id": ["a", "a"], "race_date": [dt.date(2026, 9, 13)] * 2}), raw).race_id.tolist() == ["r"]


def test_history_must_agree_with_career_starts():
    rows = pd.DataFrame({"race_id": ["r1"] * 3, "horse_id": ["a", "b", "c"],
                         "race_date": [dt.date(2026, 9, 13)] * 3, "career_starts": [2.0, 0.0, np.nan]})
    ok = pd.DataFrame({"race_id": ["p1", "p2"], "horse_id": ["a", "a"], "race_date": ["2026-08-01", "2026-08-20"]})
    ms.assert_history_matches_features(rows, ok)                       # a=2, b=0, c unknown -> fine
    ms.assert_history_matches_features(rows.assign(career_starts=[2.0, 0.0, 5.0]), ok.assign(
        race_id=["p1", "p2"]).pipe(lambda h: pd.concat([h, pd.DataFrame({"race_id": [f"q{i}" for i in range(5)],
        "horse_id": ["c"] * 5, "race_date": ["2026-01-01"] * 5})], ignore_index=True)))
    with pytest.raises(ServingError):                                  # feature says 2 starts, history has 1
        ms.assert_history_matches_features(rows, ok.iloc[:1])
    with pytest.raises(ServingError):                                  # feature says debut, history has rows
        ms.assert_history_matches_features(rows.assign(career_starts=[2.0, 1.0, np.nan]), ok)
    with pytest.raises(ServingError):                                  # column missing -> cannot cross-check
        ms.assert_history_matches_features(rows.drop(columns=["career_starts"]), ok)
    with pytest.raises(ServingError):                                  # history wired but empty for a raced horse
        ms.assert_history_matches_features(rows, ok.iloc[:0])
