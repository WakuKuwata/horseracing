"""146 checks strict old teachers, unchanged weighting, saved bodies and resume."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_146/models.py"
_spec = importlib.util.spec_from_file_location("test_models146", SOURCE)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
_espec = importlib.util.spec_from_file_location("test_experiment146", SOURCE.with_name("experiment.py"))
e = importlib.util.module_from_spec(_espec)
_espec.loader.exec_module(e)


def data(years=(2007, 2012, 2013, 2014), races=12):
    rows = []
    for year in years:
        for race in range(races):
            for horse in range(4):
                row = {c: float(horse + race) for c in m.base.NUM}
                row.update({c: f"category_{race % 2}" for c in m.base.CAT})
                row.update(race_id=f"{year}0101{race+1:04d}", horse_id=f"h{horse}", horse_number=float(horse+1),
                           race_date=f"{year}-01-03", year=year, won=horse == race % 4, odds=4.+horse,
                           field_size=4., race_ok=True, dead_heat=False)
                rows.append(row)
    return pd.DataFrame(rows)


def oof(d):
    d = d[d.year.between(2013, 2025)]
    result = d[m.KEYS].copy()
    result["p_oof"] = np.tile([.3, .1, .2, .05], len(d)//4)
    result["oof_train_through"] = d.year.to_numpy()-1
    return result


def test_extended_weights_exact_parent_and_cold_period_neutral():
    d = data()
    weights, info = m.training_weights(d, oof(d))
    original, original_info = m.parent.training_weights(d, oof(d))
    np.testing.assert_array_equal(weights, original)
    assert info == original_info
    np.testing.assert_array_equal(weights[d.year < 2013], np.ones((d.year < 2013).sum()))
    assert not np.array_equal(weights[d.year >= 2013], np.ones((d.year >= 2013).sum()))
    assert info["informative_rows"] == (d.year >= 2013).sum()
    changed = d.copy()
    changed["won"] = ~changed.won
    repeated, repeated_info = m.training_weights(changed, oof(d).sample(frac=1, random_state=8))
    np.testing.assert_array_equal(repeated, weights)
    assert repeated_info == info


@pytest.mark.parametrize("problem", ["missing", "future", "older", "cold", "duplicate", "probability"])
def test_backfill_requires_complete_previous_year_teachers(problem):
    d = data()
    predictions = oof(d)
    if problem == "missing":
        predictions = predictions.iloc[1:]
    elif problem == "future":
        predictions.iloc[0, predictions.columns.get_loc("oof_train_through")] = 2013
    elif problem == "older":
        predictions.iloc[0, predictions.columns.get_loc("oof_train_through")] = 2011
    elif problem == "cold":
        cold = d.loc[d.year == 2012, m.KEYS].copy()
        cold["p_oof"], cold["oof_train_through"] = .1, 2011
        predictions = pd.concat([predictions, cold])
    elif problem == "duplicate":
        predictions = pd.concat([predictions, predictions.iloc[:1]])
    else:
        predictions.iloc[0, predictions.columns.get_loc("p_oof")] = 1.1
    with pytest.raises(ValueError):
        m.training_weights(d, predictions)


def test_annual_split_is_strict_past_and_complete():
    d = data()
    train, target = m.annual_split(d, 2013)
    assert train.year.max() == 2012 and target.year.min() == 2013
    with pytest.raises(ValueError, match="missing strict annual split"):
        m.annual_split(d[d.year != 2012], 2013)
    with pytest.raises(ValueError, match="strictly later"):
        m.annual_split(d.assign(race_date="2013-01-03"), 2013)
    with pytest.raises(ValueError, match="incomplete race"):
        m.annual_split(d.drop(d[d.year == 2013].index[0]), 2013)


@pytest.mark.parametrize("stage", ["mentor", "candidate"])
def test_saved_fit_exact_original_kernel_roundtrip_and_resume(stage, tmp_path):
    d = data(races=24)
    train, target = m.annual_split(d, 2014)
    identity = {"stage": stage, "research_arm": m.stage_arm(stage)}
    overrides = {"num_leaves": 7, "min_data_in_leaf": 2}
    kwargs = dict(seed=1, rounds=4, identity=identity, overrides=overrides)
    weights, info = (None, None) if stage == "mentor" else m.training_weights(train, oof(d))
    if stage == "mentor":
        original, old_receipt = m.base.fit_model(train, target, arm="baseline", output=tmp_path/"old", **kwargs)
    else:
        original, old_receipt = m.parent.fit_model(train, target, arm="weighted_baseline", bundles={},
                                                  output=tmp_path/"old", weights=weights, weight_info=info, **kwargs)
    fit_args = dict(stage=stage, output=tmp_path/"new", weights=weights, weight_info=info, **kwargs)
    actual, receipt = m.fit_model(train, target, **fit_args)
    np.testing.assert_array_equal(actual, original)
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path/"old/predictions.parquet"),
                                  pd.read_parquet(tmp_path/"new/predictions.parquet"), check_exact=True)
    assert receipt["actual_iterations"] == old_receipt["actual_iterations"] == 4
    assert receipt["roundtrip_max_abs_raw_difference"] == 0.
    spec = json.loads((tmp_path/"new/feature_spec.json").read_text())
    assert spec["features"] + spec["cats"] == m.FEATURES
    assert len(m.FEATURES) == 77
    resumed, same = m.fit_model(train, target, **fit_args)
    np.testing.assert_array_equal(actual, resumed)
    assert same == receipt
    with pytest.raises(ValueError, match="recipe differs"):
        m.fit_model(train, target, **dict(fit_args, identity={"changed": True}))
    (tmp_path/"new/model.txt").write_text("changed body")
    with pytest.raises(ValueError, match="hash differs"):
        m.fit_model(train, target, **fit_args)


def test_stage_weight_mismatch_rejected_without_output(tmp_path):
    d = data()
    train, target = m.annual_split(d, 2014)
    weights, info = m.training_weights(train, oof(d))
    for stage, w, i in (("mentor", weights, info), ("candidate", None, None)):
        with pytest.raises(ValueError):
            m.fit_model(train, target, stage=stage, seed=1, output=tmp_path/stage, identity={}, weights=w, weight_info=i)
        assert not (tmp_path/stage).exists()


@pytest.mark.parametrize("stage", ["mentor", "candidate"])
def test_protocol_binds_sources_stage_recipe_and_inputs(stage, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    for module, name in ((m, "models.py"), (m.parent, "models143.py"), (m.base, "models142.py"), (m.base.legacy, "legacy.py")):
        monkeypatch.setattr(module, "SOURCE" if module is m else "__file__", tmp_path/name)
    rows = tmp_path/"rows.parquet"
    weights = tmp_path/"weights.parquet" if stage == "candidate" else None
    files = m.bound_dependencies(rows, weights)
    files += [tmp_path/name for name in ("mentor_protocol.json", "mentor_protocol.sha256", "mentor_models_complete.json", "weights_manifest.json")] if stage == "candidate" else []
    for file in files:
        file.write_text(file.name)
    protocol = dict(feature="146-old-oof-backfill", version=1, stage=stage, seeds=list(range(1, 16)), max_workers=2,
                    threads_each=1, can_adopt=False, untouched_holdout=False, arms=[m.stage_arm(stage)], features=m.FEATURES,
                    mentor_years=list(m.MENTOR_YEARS), candidate_years=list(m.CANDIDATE_YEARS), rows="rows.parquet",
                    window=dict(training_from=2007, through=m.base.UPPER, model_outer=list(m.stage_years(stage))),
                    models=dict(objective="binary", rounds=300, params=m.base.BASE_PARAMS), weight_recipe=m.WEIGHT_RECIPE,
                    inputs_and_source_sha256={str(file.relative_to(tmp_path)): m.sha(file) for file in files})
    if weights:
        protocol["weights"] = weights.name
    path = tmp_path/"protocol.json"
    def save():
        path.write_text(json.dumps(protocol))
        path.with_suffix(".sha256").write_text(m.sha(path))
    save()
    assert m.verify_protocol(path, rows, weights) == protocol
    for key, value in (("features", m.FEATURES[::-1]), ("arms", ["pace"]), ("seeds", [1]), ("models", {}),
                       ("mentor_years", [2012]), ("weight_recipe", {})):
        old = protocol[key]
        protocol[key] = value
        save()
        with pytest.raises(ValueError, match=key):
            m.verify_protocol(path, rows, weights)
        protocol[key] = old
    save()
    with pytest.raises(ValueError, match="input path differs"):
        m.verify_protocol(path, tmp_path/"wrong.parquet", weights)
    if stage == "candidate":
        omitted = protocol["inputs_and_source_sha256"].pop("weights_manifest.json")
        save()
        with pytest.raises(ValueError, match="does not bind weights_manifest"):
            m.verify_protocol(path, rows, weights)
        protocol["inputs_and_source_sha256"]["weights_manifest.json"] = omitted
        save()
    (tmp_path/"legacy.py").write_text("changed source")
    with pytest.raises(ValueError, match="input/source hash mismatch"):
        m.verify_protocol(path, rows, weights)


@pytest.mark.parametrize("stage", ["mentor", "candidate"])
def test_complete_resume_checks_recipe_eval_and_saved_hashes(stage, tmp_path, monkeypatch):
    d = data()
    train, target = m.annual_split(d, 2014)
    rows, weights_path, protocol_path = [tmp_path/name for name in ("rows.parquet", "weights.parquet", "protocol.json")]
    d.to_parquet(rows)
    oof(d).to_parquet(weights_path)
    protocol_path.write_text("frozen")
    protocol = dict(stage=stage, models={})
    year = 2014 if stage == "mentor" else 2019
    if stage == "candidate":
        train, target = train.copy(), target.copy()
        target["year"] = year
        target["race_date"] = "2019-01-03"
    args = SimpleNamespace(stage=stage, protocol=protocol_path, rows=rows, weights=weights_path if stage == "candidate" else None,
                           year=year, seed=1, out=tmp_path/"artifacts/models")
    monkeypatch.setattr(e.models.base, "ROOT", tmp_path)
    identity = e.models.run_identity(args, protocol)
    e.models.base.prepare_run(args.out, identity, False)
    weights, info = (None, None) if stage == "mentor" else e.models.training_weights(train, oof(d))
    arm = e.models.stage_arm(stage)
    _, receipt = e.models.fit_model(train, target, stage=stage, seed=1, output=args.out/arm,
                                    identity=identity, weights=weights, weight_info=info)
    # Synthetic no-split fields may legally finish before 300 trees.
    monkeypatch.setattr(e.models.base, "FIXED_ROUNDS", receipt["actual_iterations"])
    receipt["recipe"]["rounds"] = receipt["actual_iterations"]
    receipt_path = args.out/arm/"receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    keys = ["train_start", "train_end", "target_start", "target_end", "train_rows", "target_rows"]
    keys += ["excluded_races"] if stage == "mentor" else ["weights"]
    expected = {arm: {key: receipt["recipe"][key] for key in keys}}
    record = dict(metrics=receipt["metrics"], elapsed_s=receipt["elapsed_s"], actual_iterations=receipt["actual_iterations"])
    evaluation = dict(identity=identity, stage=stage, year=year, seed=1, arms={arm: record},
                      can_adopt=False, untouched_holdout=False, production_changed=False)
    e.models.save_json(args.out/"evaluation.json", evaluation)
    e.verify_completed(args, protocol, expected)
    original = receipt_path.read_text()
    changed = copy.deepcopy(receipt)
    changed["recipe"]["seed"] = 2
    receipt_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="fit recipe differs"):
        e.verify_completed(args, protocol, expected)
    receipt_path.write_text(original)
    evaluation["seed"] = 2
    (args.out/"evaluation.json").write_text(json.dumps(evaluation))
    with pytest.raises(ValueError, match="stage/year/seed differs"):
        e.verify_completed(args, protocol, expected)
    evaluation["seed"] = 1
    (args.out/"evaluation.json").write_text(json.dumps(evaluation))
    (args.out/arm/"model.txt").write_text("changed body")
    with pytest.raises(ValueError, match="artifact changed"):
        e.verify_completed(args, protocol, expected)
