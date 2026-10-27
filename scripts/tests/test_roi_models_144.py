"""144's weight decomposition, kernel parity and immutable-run contracts."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_144/models.py"
_spec = importlib.util.spec_from_file_location("test_models144", SOURCE)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
_espec = importlib.util.spec_from_file_location("test_experiment144", SOURCE.with_name("experiment.py"))
e = importlib.util.module_from_spec(_espec)
_espec.loader.exec_module(e)


def data(years=(2018, 2019), races=3):
    rows = []
    for year in years:
        for race in range(races):
            for horse in range(4):
                row = {c: float(horse+race) for c in m.base.NUM}
                row.update({c: f"category_{race % 2}" for c in m.base.CAT})
                row.update(race_id=f"{year}0101{race+1:04d}", horse_id=f"h{horse}", horse_number=float(horse+1),
                           race_date=f"{year}-01-03", year=year, won=horse == race % 4, odds=4.+horse,
                           field_size=4., race_ok=True, dead_heat=False)
                rows.append(row)
    return pd.DataFrame(rows)


def oof(d):
    result = d[m.KEYS].copy()
    result["p_oof"] = np.tile([.3, .1, .2, .05], len(d)//4)
    result["oof_train_through"] = d.year.to_numpy()-1
    # Give alternating fields different mean weights, exercising the split.
    result.loc[(np.arange(len(d))//4) % 2 == 1, "p_oof"] = .01
    return result


def test_exact_decomposition_and_no_second_global_normalization():
    d = data((2019,))
    predictions = oof(d)
    original, parent_info = m.parent.training_weights(d, predictions)
    b, info_b = m.training_weights(d, predictions, "within_race")
    a, info_a = m.training_weights(d, predictions, "race_only")
    starts, counts = m.base.segments(d.race_id)
    np.testing.assert_allclose(np.add.reduceat(b, starts)/counts, 1., atol=1e-12, rtol=0)
    np.testing.assert_allclose(a*b, original, atol=1e-12, rtol=0)
    np.testing.assert_array_equal(a, np.repeat(np.add.reduceat(original, starts)/counts, counts))
    assert len(set(a)) > 1
    assert info_a["original_weight_info"] == info_b["original_weight_info"] == parent_info
    assert info_a["original_vector_sha256"] == m.vector_sha(original)
    assert info_b["vector_sha256"] == m.vector_sha(b)
    assert info_a["global_renormalization"] is False


@pytest.mark.parametrize("arm", m.ARMS)
def test_neutral_whole_fields_label_independence_and_oof_order(arm):
    d = data()
    predictions = oof(d[d.year == 2019]).iloc[1:]
    w, info = m.training_weights(d, predictions, arm)
    np.testing.assert_array_equal(w[:16], np.ones(16))
    altered = d.copy()
    altered["won"] = ~altered.won
    second, second_info = m.training_weights(altered, predictions.sample(frac=1., random_state=4), arm)
    np.testing.assert_array_equal(w, second)
    assert info == second_info


@pytest.mark.parametrize("problem", ["missing_horse", "wrong_size", "duplicate", "order", "date"])
def test_incomplete_or_reordered_fields_rejected(problem):
    d = data((2019,))
    if problem == "missing_horse":
        d = d.iloc[1:]
    elif problem == "wrong_size":
        d.loc[0, "field_size"] = 5
    elif problem == "duplicate":
        d.loc[0, "horse_id"] = "h1"
    elif problem == "order":
        d = d.iloc[::-1]
    else:
        d.loc[0, "race_date"] = "2019-01-04"
    with pytest.raises(ValueError):
        m.weight_components(d, np.ones(len(d)))


@pytest.mark.parametrize("problem", ["future", "probability", "provenance", "duplicate"])
def test_parent_oof_provenance_failures_remain_failures(problem):
    d = data((2019,))
    predictions = oof(d)
    if problem == "future":
        predictions.loc[0, "oof_train_through"] = 2019
    elif problem == "probability":
        predictions.loc[0, "p_oof"] = 1.1
    elif problem == "provenance":
        predictions.loc[0, "oof_train_through"] = np.nan
    else:
        predictions = pd.concat([predictions, predictions.iloc[:1]])
    with pytest.raises(ValueError):
        m.training_weights(d, predictions, "within_race")


@pytest.mark.parametrize("invalid", [np.nan, np.inf, 0., -1.])
def test_invalid_original_vectors_rejected(invalid):
    d = data((2019,))
    weights = np.ones(len(d))
    weights[0] = invalid
    with pytest.raises(ValueError, match="finite positive"):
        m.weight_components(d, weights)


@pytest.mark.parametrize("arm", m.ARMS)
def test_2019_neutral_fit_is_bitwise_parent143_and142(arm, tmp_path):
    d = data((2018, 2019), races=12)
    train, target = d[d.year == 2018], d[d.year == 2019]
    missing = pd.DataFrame(columns=m.KEYS + ["p_oof", "oof_train_through"])
    weights, info = m.training_weights(train, missing, arm)
    original, original_info = m.parent.training_weights(train, missing)
    overrides = {"num_leaves": 7, "min_data_in_leaf": 2}
    old, _ = m.parent.fit_model(train, target, arm="weighted_baseline", seed=1, rounds=4,
                               bundles=m.BUNDLES, output=tmp_path/"parent", identity={},
                               weights=original, weight_info=original_info, overrides=overrides)
    new, receipt = m.fit_model(train, target, arm=arm, seed=1, rounds=4,
                               output=tmp_path/"new", identity={}, weights=weights,
                               weight_info=info, overrides=overrides)
    baseline, _ = m.base.fit_model(train, target, arm="binary", seed=1, rounds=4,
                                   output=tmp_path/"baseline", identity={}, overrides=overrides)
    np.testing.assert_array_equal(new, old)
    np.testing.assert_array_equal(new, baseline)
    assert receipt["recipe"]["arm"] == "weighted_baseline"
    assert receipt["recipe"]["identity"]["research_arm"] == arm
    resumed, same = m.fit_model(train, target, arm=arm, seed=1, rounds=4,
                                output=tmp_path/"new", identity={}, weights=weights,
                                weight_info=info, overrides=overrides)
    np.testing.assert_array_equal(new, resumed)
    assert same == receipt
    (tmp_path/"new/model.txt").write_text("tampered")
    with pytest.raises(ValueError, match="hash differs"):
        m.fit_model(train, target, arm=arm, seed=1, rounds=4, output=tmp_path/"new", identity={},
                    weights=weights, weight_info=info, overrides=overrides)


def test_protocol_binds_actual_sources_recipe_and_input_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    source, parent_source, old_source, legacy = [tmp_path/name for name in ("models.py", "models143.py", "models142.py", "legacy.py")]
    monkeypatch.setattr(m, "SOURCE", source)
    monkeypatch.setattr(m.parent, "__file__", str(parent_source))
    monkeypatch.setattr(m.base, "__file__", str(old_source))
    monkeypatch.setattr(m.base.legacy, "__file__", str(legacy))
    rows, weights = tmp_path/"rows.parquet", tmp_path/"weights.parquet"
    files = m.bound_dependencies(rows, weights)
    for file in files:
        file.write_text(file.name)
    protocol = dict(feature="144-race-normalized-weight", version=1, seeds=list(range(1, 16)), max_workers=2,
                    threads_each=1, can_adopt=False, untouched_holdout=False, arms=list(m.ARMS),
                    rows="rows.parquet", weights="weights.parquet",
                    window=dict(training_from=2007, through=m.base.UPPER, model_outer=list(m.base.OUTER_YEARS)),
                    models=dict(objective="binary", rounds=300, params=m.base.BASE_PARAMS),
                    weight_recipe=m.WEIGHT_RECIPE, weight_transforms=m.TRANSFORMS,
                    inputs_and_source_sha256={str(file.relative_to(tmp_path)): m.sha(file) for file in files})
    path = tmp_path/"protocol.json"
    def save():
        path.write_text(json.dumps(protocol))
        path.with_suffix(".sha256").write_text(m.sha(path))
    save()
    assert m.verify_protocol(path, rows, weights) == protocol
    with pytest.raises(ValueError, match="input path differs"):
        m.verify_protocol(path, rows, tmp_path/"alternate.parquet")
    protocol["weight_transforms"] = dict(m.TRANSFORMS, within_race="alternate")
    save()
    with pytest.raises(ValueError, match="weight_transforms"):
        m.verify_protocol(path, rows, weights)
    protocol["weight_transforms"] = m.TRANSFORMS
    save()
    legacy.write_text("changed")
    with pytest.raises(ValueError, match="input/source hash mismatch"):
        m.verify_protocol(path, rows, weights)
    path.write_text("changed protocol")
    with pytest.raises(ValueError, match="sidecar hash mismatch"):
        m.verify_protocol(path, rows, weights)


def test_completed_verification_checks_recipe_weight_hash_eval_and_artifacts(tmp_path, monkeypatch):
    d = data()
    train, target = d[d.year == 2018], d[d.year == 2019]
    rows, weights_path, protocol_path = tmp_path/"rows.parquet", tmp_path/"weights.parquet", tmp_path/"protocol.json"
    d.to_parquet(rows)
    oof(d).to_parquet(weights_path)
    protocol_path.write_text("frozen")
    args = SimpleNamespace(protocol=protocol_path, rows=rows, weights=weights_path,
                           year=2019, seed=1, arms=list(m.ARMS), out=tmp_path/"artifacts/models")
    monkeypatch.setattr(e.models.base, "ROOT", tmp_path)
    identity = e.models.run_identity(args, {})
    e.models.base.prepare_run(args.out, identity, False)
    records, expected = {}, {}
    for arm in m.ARMS:
        weights, info = e.models.training_weights(train, oof(d), arm)
        _, receipt = e.models.fit_model(train, target, arm=arm, seed=1, output=args.out/arm,
                                        identity=identity, weights=weights, weight_info=info)
        records[arm] = dict(metrics=receipt["metrics"], elapsed_s=receipt["elapsed_s"], actual_iterations=receipt["actual_iterations"])
        expected[arm] = {key: receipt["recipe"][key] for key in ("train_start", "train_end", "target_start", "target_end", "train_rows", "target_rows", "weights")}
    evaluation = dict(identity=identity, year=2019, seed=1, arms=records, can_adopt=False,
                      untouched_holdout=False, production_changed=False)
    e.models.save_json(args.out/"evaluation.json", evaluation)
    e.verify_completed(args, {}, expected)
    path = args.out/"within_race/receipt.json"
    original_receipt = path.read_text()
    changed = json.loads(original_receipt)
    changed["recipe"]["weights"]["vector_sha256"] = "forged"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="fit recipe differs"):
        e.verify_completed(args, {}, expected)
    path.write_text(original_receipt)
    evaluation["seed"] = 2
    (args.out/"evaluation.json").write_text(json.dumps(evaluation))
    with pytest.raises(ValueError, match="year/seed differs"):
        e.verify_completed(args, {}, expected)
    evaluation["seed"] = 1
    (args.out/"evaluation.json").write_text(json.dumps(evaluation))
    (args.out/"within_race/model.txt").write_text("changed body")
    with pytest.raises(ValueError, match="artifact changed"):
        e.verify_completed(args, {}, expected)


def test_cli_rejects_single_arm_before_reading_inputs(tmp_path):
    required = ["evaluate", "--protocol", str(tmp_path/"p"), "--rows", str(tmp_path/"r"),
                "--weights", str(tmp_path/"w"), "--out", str(tmp_path/"o"), "--year", "2019", "--seed", "1"]
    with pytest.raises(SystemExit):
        m.main(required + ["--arms", "within_race"])
