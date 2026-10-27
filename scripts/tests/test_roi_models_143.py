"""Coverage, past-only OOF weighting, frozen recipe and saved body checks (143)."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_143/models.py"
_spec = importlib.util.spec_from_file_location("test_models143", SOURCE)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
BUNDLES = {"speed": ["extra_speed"], "pace": ["extra_pace"]}


def data(years=(2018, 2019), races=3):
    rows = []
    for year in years:
        for race in range(races):
            for horse in range(4):
                row = {c: float(horse+race) for c in m.base.NUM}
                row.update({c: f"category_{race % 2}" for c in m.base.CAT})
                row.update(race_id=f"{year}0101{race+1:04d}", horse_id=f"h{horse}", horse_number=float(horse+1),
                           race_date=f"{year}-01-03", year=year, won=horse == race % 4, odds=4.+horse,
                           field_size=4., race_ok=True, dead_heat=False, extra_speed=float(horse), extra_pace=float(race))
                rows.append(row)
    return pd.DataFrame(rows)


def oof(data):
    result = data[m.KEYS].copy()
    result["p_oof"] = np.tile([.3, .1, .2, .05], len(data)//4)
    result["oof_train_through"] = data.year.to_numpy()-1
    return result


def test_features_preserve_baseline_and_extras_are_separate():
    assert m.base.BASE_PARAMS["num_threads"] == 1
    assert len(m.base.FEATURES) == 77
    for arm in ("speed", "pace"):
        numbers, cats, features = m.feature_contract(arm, BUNDLES)
        assert numbers == m.base.NUM + BUNDLES[arm]
        assert cats == m.base.CAT and features == numbers + cats
    assert m.feature_contract("weighted_baseline", BUNDLES)[2] == m.base.FEATURES


def test_additions_join_is_full_one_to_one_and_order_preserving(tmp_path):
    original = data()
    rows, additions = tmp_path / "rows.parquet", tmp_path / "extras.parquet"
    original.to_parquet(rows, index=False)
    extras = original[m.KEYS + ["extra_speed", "extra_pace"]].sample(frac=1., random_state=1)
    extras.loc[extras.index[0], "extra_speed"] = np.nan
    extras.to_parquet(additions, index=False)
    result = m.load_data(rows, additions, BUNDLES)
    assert result[m.KEYS].equals(original[m.KEYS])
    assert len(result) == len(original)
    assert result.extra_speed.isna().sum() == 1
    extras.iloc[:-1].to_parquet(additions, index=False)
    with pytest.raises(ValueError, match="coverage"):
        m.load_data(rows, additions, BUNDLES)
    pd.concat([extras, extras.iloc[:1]]).to_parquet(additions, index=False)
    with pytest.raises(ValueError, match="duplicate"):
        m.load_data(rows, additions, BUNDLES)


def test_infinite_addition_and_feature_overlap_rejected(tmp_path):
    original = data()
    rows, additions = tmp_path / "rows.parquet", tmp_path / "extras.parquet"
    original.to_parquet(rows, index=False)
    extras = original[m.KEYS + ["extra_speed", "extra_pace"]].copy()
    extras.loc[0, "extra_speed"] = np.inf
    extras.to_parquet(additions, index=False)
    with pytest.raises(ValueError, match="infinity"):
        m.load_data(rows, additions, BUNDLES)
    with pytest.raises(ValueError, match="overlaps"):
        m.load_data(rows, additions, {"speed": ["won"], "pace": ["extra_pace"]})


def test_categories_only_training_and_unknowns_remain_missing():
    d = data()
    train, target = d[d.year == 2018], d[d.year == 2019].copy()
    target["venue_code"] = "new_year_only"
    maps = m.base.category_maps(train)
    numeric, _, features = m.feature_contract("speed", BUNDLES)
    x = m.design_matrix(target, maps, numeric)
    assert np.isnan(x[:, features.index("venue_code")]).all()
    assert "new_year_only" not in maps["venue_code"]


def test_weights_are_label_independent_and_neutral_cohort_stays_exact_one():
    train = data((2018, 2019))
    predictions = oof(train[train.year == 2019])
    weights, info = m.training_weights(train, predictions)
    np.testing.assert_array_equal(weights[train.year == 2018], np.ones((train.year == 2018).sum()))
    assert weights[train.year == 2019].mean() == pytest.approx(1.)
    assert weights.mean() == pytest.approx(1.)
    changed = train.copy()
    changed["won"] = ~changed.won
    second, second_info = m.training_weights(changed, predictions)
    np.testing.assert_array_equal(weights, second)
    assert info == second_info
    assert info["informative_rows"] == 12 and info["neutral_rows"] == 12
    expected = 1 + np.exp(-.5*((np.array([.3, .1, .2, .05])*np.array([4., 5., 6., 7.])-1.2)/.2)**2)
    np.testing.assert_allclose(weights[12:16], expected/expected.mean())


def test_partially_missing_race_is_neutral_whole_field():
    train = data((2019,))
    predictions = oof(train).iloc[1:]
    weights, info = m.training_weights(train, predictions)
    np.testing.assert_array_equal(weights[:4], np.ones(4))
    assert info["informative_races"] == 2 and info["neutral_races"] == 1


@pytest.mark.parametrize("problem", ["future", "probability", "provenance", "duplicate"])
def test_invalid_oof_is_rejected(problem):
    train = data((2019,))
    predictions = oof(train)
    if problem == "future":
        predictions.loc[0, "oof_train_through"] = 2019
    elif problem == "probability":
        predictions.loc[0, "p_oof"] = 1.1
    elif problem == "provenance":
        predictions.loc[0, "oof_train_through"] = np.nan
    else:
        predictions = pd.concat([predictions, predictions.iloc[:1]])
    with pytest.raises(ValueError):
        m.training_weights(train, predictions)


def test_all_unavailable_oof_weights_are_ones():
    train = data((2018,))
    predictions = pd.DataFrame(columns=m.KEYS + ["p_oof", "oof_train_through"])
    weights, info = m.training_weights(train, predictions)
    np.testing.assert_array_equal(weights, np.ones(len(train)))
    assert info["informative_races"] == 0 and info["raw_mean"] == 1.


@pytest.mark.parametrize("arm", ["speed", "pace", "weighted_baseline"])
def test_saved_fit_roundtrip_resume_and_tampering(arm, tmp_path):
    d = data((2018, 2019), races=12)
    train, target = d[d.year == 2018], d[d.year == 2019]
    weights, info = m.training_weights(train, oof(train)) if arm == "weighted_baseline" else (None, None)
    kwargs = dict(arm=arm, seed=1, bundles=BUNDLES, output=tmp_path / arm, identity={"frozen": True},
                  weights=weights, weight_info=info, rounds=4, overrides={"num_leaves": 7, "min_data_in_leaf": 2})
    p, receipt = m.fit_model(train, target, **kwargs)
    saved, second = m.fit_model(train, target, **kwargs)
    np.testing.assert_array_equal(p, saved)
    assert receipt == second and receipt["roundtrip_max_abs_raw_difference"] <= 1e-12
    spec = json.loads((kwargs["output"] / "feature_spec.json").read_text())
    assert spec["train_through"] == 2018 and spec["trained_for_year"] == 2019
    assert spec["params"]["num_threads"] == 1
    with pytest.raises(ValueError, match="recipe differs"):
        m.fit_model(train, target, **dict(kwargs, seed=2))
    (kwargs["output"] / "model.txt").write_text("tampered")
    with pytest.raises(ValueError, match="hash differs"):
        m.fit_model(train, target, **kwargs)


def test_nonpast_and_incomplete_output_and_weight_identity_fail(tmp_path):
    d = data()
    train, target = d[d.year == 2018], d[d.year == 2019]
    kwargs = dict(arm="speed", seed=1, bundles=BUNDLES, output=tmp_path / "fit", identity={})
    with pytest.raises(ValueError, match="strictly later"):
        m.fit_model(d, d, **kwargs)
    kwargs["output"].mkdir()
    with pytest.raises(ValueError, match="incomplete fit"):
        m.fit_model(train, target, **kwargs)
    with pytest.raises(ValueError, match="weight identity"):
        m.fit_model(train, target, **dict(kwargs, arm="weighted_baseline", weights=np.ones(len(train))))


def test_neutral_weighted_baseline_matches_original_binary_exactly(tmp_path):
    d = data((2018, 2019), races=12)
    train, target = d[d.year == 2018], d[d.year == 2019]
    predictions = pd.DataFrame(columns=m.KEYS + ["p_oof", "oof_train_through"])
    weights, info = m.training_weights(train, predictions)
    overrides = {"num_leaves": 7, "min_data_in_leaf": 2}
    old, _ = m.base.fit_model(train, target, arm="binary", seed=1, rounds=4, output=tmp_path/"old",
                             identity={}, overrides=overrides)
    new, _ = m.fit_model(train, target, arm="weighted_baseline", seed=1, rounds=4, output=tmp_path/"new",
                         identity={}, bundles=BUNDLES, weights=weights, weight_info=info, overrides=overrides)
    np.testing.assert_array_equal(old, new)


def test_protocol_binds_recipe_and_actual_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    source, old_source, legacy = tmp_path/"models143.py", tmp_path/"models142.py", tmp_path/"legacy.py"
    monkeypatch.setattr(m, "SOURCE", source)
    monkeypatch.setattr(m.base, "__file__", str(old_source))
    monkeypatch.setattr(m.base.legacy, "__file__", str(legacy))
    rows, additions, weights = tmp_path/"rows.parquet", tmp_path/"extras.parquet", tmp_path/"weights.parquet"
    files = [source, old_source, legacy, tmp_path/"experiment.py", rows, additions, weights]
    for file in files:
        file.write_text(file.name)
    protocol = dict(feature="143-existing-feature-roi", version=1, seeds=list(range(1, 16)), max_workers=2,
                    threads_each=1, can_adopt=False, untouched_holdout=False,
                    window=dict(training_from=2007, through=m.base.UPPER, model_outer=list(m.base.OUTER_YEARS)),
                    models=dict(objective="binary", rounds=300, params=m.base.BASE_PARAMS),
                    weight_recipe=m.WEIGHT_RECIPE, bundles=BUNDLES,
                    inputs_and_source_sha256={str(file.relative_to(tmp_path)): m.sha(file) for file in files})
    path = tmp_path/"protocol.json"
    path.write_text(json.dumps(protocol))
    path.with_suffix(".sha256").write_text(m.sha(path))
    assert m.verify_protocol(path, rows, additions, weights) == protocol
    legacy.write_text("changed")
    with pytest.raises(ValueError, match="input/source hash mismatch"):
        m.verify_protocol(path, rows, additions, weights)
    path.write_text("changed protocol")
    with pytest.raises(ValueError, match="sidecar hash mismatch"):
        m.verify_protocol(path, rows, additions, weights)


def test_comparison_marker_requires_completed_hashed_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    protocol, summary, marker = tmp_path / "protocol.json", tmp_path / "summary.json", tmp_path / "comparison_marker.json"
    protocol.write_text("frozen")
    summary.write_text("compared")
    record = dict(stage="features_complete", protocol_sha256=m.sha(protocol), summary_path="summary.json", summary_sha256=m.sha(summary))
    marker.write_text(json.dumps(record))
    marker.with_suffix(".sha256").write_text(m.sha(marker))
    assert m.verify_comparison_marker(marker, protocol) == record
    summary.write_text("changed")
    with pytest.raises(ValueError, match="artifact hash differs"):
        m.verify_comparison_marker(marker, protocol)


def test_stage3_cli_requires_separate_gate(tmp_path):
    required = ["evaluate", "--protocol", str(tmp_path/"p"), "--rows", str(tmp_path/"r"),
                "--additions", str(tmp_path/"a"), "--out", str(tmp_path/"out"), "--year", "2019", "--seed", "1"]
    with pytest.raises(SystemExit):
        m.main(required + ["--arms", "weighted_baseline"])
    with pytest.raises(SystemExit):
        m.main(required + ["--arms", "speed", "weighted_baseline"])
