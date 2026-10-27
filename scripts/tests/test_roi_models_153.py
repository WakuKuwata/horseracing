"""153 preserves the exact74 matrix slice and original 143 weight vector."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

SOURCE = (
    Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_153/models.py"
)
_spec = importlib.util.spec_from_file_location("test_models153", SOURCE)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
_espec = importlib.util.spec_from_file_location(
    "test_experiment153", SOURCE.with_name("experiment.py")
)
e = importlib.util.module_from_spec(_espec)
_espec.loader.exec_module(e)


def data(years=(2018, 2019), races=12):
    rows = []
    for year in years:
        for race in range(races):
            for horse in range(4):
                row = {c: float(horse + race) for c in m.base.NUM}
                row.update({c: f"category_{race % 2}" for c in m.base.CAT})
                row.update({c: float(horse - race) for c in []})
                row.update(
                    race_id=f"{year}0101{race + 1:04d}",
                    horse_id=f"h{horse}",
                    horse_number=float(horse + 1),
                    race_date=f"{year}-01-03",
                    year=year,
                    won=horse == race % 4,
                    odds=4.0 + horse,
                    field_size=4.0,
                    race_ok=True,
                    dead_heat=False,
                )
                rows.append(row)
    return pd.DataFrame(rows)


def oof(d):
    result = d[m.KEYS].copy()
    result["p_oof"] = np.tile([0.3, 0.1, 0.2, 0.05], len(d) // 4)
    result["oof_train_through"] = d.year.to_numpy() - 1
    return result


def test_original_weights_exact_parent_and_label_independent():
    d = data()
    predictions = oof(d[d.year == 2019]).iloc[1:]
    w, info = m.training_weights(d, predictions)
    original, original_info = m.parent.training_weights(d, predictions)
    np.testing.assert_array_equal(w, original)
    assert info == original_info
    altered = d.copy()
    altered["won"] = ~altered.won
    second, second_info = m.training_weights(
        altered, predictions.sample(frac=1.0, random_state=4)
    )
    np.testing.assert_array_equal(w, second)
    assert info == second_info
    np.testing.assert_array_equal(w[:52], np.ones(52))


def test_feature_contract_removes_only_exact_three_columns_and_keeps_metadata():
    d = data()
    numeric, cats, features = m.feature_contract(m.BUNDLES)
    assert features == [c for c in m.base.FEATURES if c not in m.DROPPED]
    assert len(features) == 74
    assert set(m.base.FEATURES) - set(features) == {"odds", "popularity", "odds_rank"}
    assert cats == m.base.CAT
    assert "q" in features and "odds" in d and "won" in d
    maps = m.base.category_maps(d[d.year == 2018])
    full = m.base.design_matrix(d, maps)
    dropped = m.parent.design_matrix(d, maps, numeric)
    keep = [m.base.FEATURES.index(c) for c in features]
    np.testing.assert_array_equal(dropped, full[:, keep])
    altered = d.copy()
    altered[m.DROPPED] = 123456.0
    np.testing.assert_array_equal(
        dropped, m.parent.design_matrix(altered, maps, numeric)
    )
    with pytest.raises(ValueError, match="feature contract differs"):
        m.feature_contract({"drop": m.DROPPED[::-1]})


@pytest.mark.parametrize(
    "problem", ["future", "probability", "provenance", "duplicate"]
)
def test_parent_oof_failures_remain_failures(problem):
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
        m.training_weights(d, predictions)


def test_neutral_fit_native_matrix_slice_roundtrip_and_resume(tmp_path):
    d = data()
    train, target = d[d.year == 2018], d[d.year == 2019]
    missing = pd.DataFrame(columns=m.KEYS + ["p_oof", "oof_train_through"])
    weights, info = m.training_weights(train, missing)
    np.testing.assert_array_equal(weights, np.ones(len(train)))
    overrides = {"num_leaves": 7, "min_data_in_leaf": 2}
    args = dict(
        arm=m.KERNEL_ARM,
        seed=1,
        rounds=4,
        bundles=m.BUNDLES,
        output=tmp_path / "new",
        identity={},
        weights=weights,
        weight_info=info,
        overrides=overrides,
    )
    new, receipt = m.fit_model(train, target, **args)
    # Independent native LightGBM fit on a slice of the original77 matrix.
    maps = m.base.category_maps(train)
    keep = [m.base.FEATURES.index(c) for c in m.FEATURES]
    xtr = m.base.design_matrix(train, maps)[:, keep]
    xte = m.base.design_matrix(target, maps)[:, keep]
    params = dict(
        m.base.BASE_PARAMS,
        seed=1,
        objective="binary",
        metric="binary_logloss",
        **overrides,
    )
    ds = m.lgb.Dataset(
        xtr,
        train.won.to_numpy(float),
        weight=weights,
        feature_name=m.FEATURES,
        categorical_feature=list(range(len(m.NUM), len(m.FEATURES))),
    )
    booster = m.lgb.train(params, ds, num_boost_round=4)
    np.testing.assert_array_equal(
        new, m.expit(booster.predict(xte, raw_score=True, num_threads=1))
    )
    assert receipt["actual_iterations"] == 4
    assert receipt["roundtrip_max_abs_raw_difference"] == 0.0
    assert receipt["recipe"]["features"] == m.FEATURES
    resumed, same = m.fit_model(train, target, **args)
    np.testing.assert_array_equal(new, resumed)
    assert same == receipt
    changed = dict(info, vector_sha256="wrong")
    with pytest.raises(ValueError, match="weight identity differs"):
        m.fit_model(train, target, **dict(args, weight_info=changed))
    (tmp_path / "new/model.txt").write_text("tampered")
    with pytest.raises(ValueError, match="hash differs"):
        m.fit_model(train, target, **args)


@pytest.mark.parametrize("problem", ["arm", "missing", "nan", "zero"])
def test_market_drop3_fit_contract_rejects_invalid_weights(problem, tmp_path):
    d = data()
    train, target = d[d.year == 2018], d[d.year == 2019]
    weights, info = m.training_weights(train, oof(d))
    arm = m.KERNEL_ARM
    if problem == "arm":
        arm = "pace"
    elif problem == "missing":
        weights = None
    elif problem == "nan":
        weights[0] = np.nan
    else:
        weights[0] = 0.0
    with pytest.raises(ValueError):
        m.fit_model(
            train,
            target,
            arm=arm,
            seed=1,
            bundles=m.BUNDLES,
            output=tmp_path / "bad",
            identity={},
            weights=weights,
            weight_info=info,
        )
    assert not (tmp_path / "bad").exists()


def test_protocol_binds_sources_recipe_and_input_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    for module, name in (
        (m, "models.py"),
        (m.parent, "models143.py"),
        (m.base, "models142.py"),
        (m.base.legacy, "legacy.py"),
    ):
        monkeypatch.setattr(
            module, "SOURCE" if module is m else "__file__", tmp_path / name
        )
    rows, additions, weights = [
        tmp_path / name
        for name in ("rows.parquet", "additions.parquet", "weights.parquet")
    ]
    files = m.bound_dependencies(rows, additions, weights)
    for file in files:
        file.write_text(file.name)
    protocol = dict(
        feature="153-market-three-column-ablation",
        version=1,
        seeds=list(range(1, 16)),
        max_workers=2,
        threads_each=1,
        can_adopt=False,
        untouched_holdout=False,
        arms=list(m.ARMS),
        bundles=m.BUNDLES,
        rows="rows.parquet",
        additions="additions.parquet",
        weights="weights.parquet",
        window=dict(
            training_from=2007,
            through=m.base.UPPER,
            model_outer=list(m.base.OUTER_YEARS),
        ),
        models=dict(objective="binary", rounds=300, params=m.base.BASE_PARAMS),
        weight_recipe=m.WEIGHT_RECIPE,
        inputs_and_source_sha256={
            str(file.relative_to(tmp_path)): m.sha(file) for file in files
        },
    )
    path = tmp_path / "protocol.json"

    def save():
        path.write_text(json.dumps(protocol))
        path.with_suffix(".sha256").write_text(m.sha(path))

    save()
    assert m.verify_protocol(path, rows, additions, weights) == protocol
    with pytest.raises(ValueError, match="input path differs"):
        m.verify_protocol(path, rows, tmp_path / "alternate.parquet", weights)
    for key, replacement in (
        ("bundles", {"drop": []}),
        ("arms", ["pace"]),
        ("weight_recipe", {}),
        ("models", {}),
        ("seeds", [1]),
    ):
        original = protocol[key]
        protocol[key] = replacement
        save()
        with pytest.raises(ValueError, match=key):
            m.verify_protocol(path, rows, additions, weights)
        protocol[key] = original
    save()
    (tmp_path / "legacy.py").write_text("changed")
    with pytest.raises(ValueError, match="input/source hash mismatch"):
        m.verify_protocol(path, rows, additions, weights)
    path.write_text("changed protocol")
    with pytest.raises(ValueError, match="sidecar hash mismatch"):
        m.verify_protocol(path, rows, additions, weights)


def test_completed_verification_checks_recipe_eval_and_artifacts(tmp_path, monkeypatch):
    d = data()
    train, target = d[d.year == 2018], d[d.year == 2019]
    rows, additions, weights_path, protocol_path = [
        tmp_path / name
        for name in (
            "rows.parquet",
            "additions.parquet",
            "weights.parquet",
            "protocol.json",
        )
    ]
    d.to_parquet(rows)
    d[m.KEYS].to_parquet(additions)
    oof(d).to_parquet(weights_path)
    protocol_path.write_text("frozen")
    protocol = dict(bundles=m.BUNDLES)
    args = SimpleNamespace(
        protocol=protocol_path,
        rows=rows,
        additions=additions,
        weights=weights_path,
        year=2019,
        seed=1,
        arms=list(m.ARMS),
        out=tmp_path / "artifacts/models",
    )
    monkeypatch.setattr(e.models.base, "ROOT", tmp_path)
    identity = e.models.run_identity(args, protocol)
    e.models.base.prepare_run(args.out, identity, False)
    weights, info = e.models.training_weights(train, oof(d))
    _, receipt = e.models.fit_model(
        train,
        target,
        arm=m.KERNEL_ARM,
        seed=1,
        bundles=m.BUNDLES,
        output=args.out / m.KERNEL_ARM,
        identity=identity,
        weights=weights,
        weight_info=info,
    )
    # Tiny synthetic fields can finish before 300 trees when no split is legal.
    monkeypatch.setattr(e.models.base, "FIXED_ROUNDS", receipt["actual_iterations"])
    receipt["recipe"]["rounds"] = receipt["actual_iterations"]
    receipt_path = args.out / m.KERNEL_ARM / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    record = dict(
        metrics=receipt["metrics"],
        elapsed_s=receipt["elapsed_s"],
        actual_iterations=receipt["actual_iterations"],
    )
    expected = {
        m.KERNEL_ARM: {
            key: receipt["recipe"][key]
            for key in (
                "train_start",
                "train_end",
                "target_start",
                "target_end",
                "train_rows",
                "target_rows",
                "weights",
            )
        }
    }
    evaluation = dict(
        identity=identity,
        year=2019,
        seed=1,
        arms={m.KERNEL_ARM: record},
        can_adopt=False,
        untouched_holdout=False,
        production_changed=False,
    )
    e.models.save_json(args.out / "evaluation.json", evaluation)
    e.verify_completed(args, protocol, expected)
    original_receipt = receipt_path.read_text()
    changed = json.loads(original_receipt)
    changed["recipe"]["weights"]["vector_sha256"] = "forged"
    receipt_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="fit recipe differs"):
        e.verify_completed(args, protocol, expected)
    receipt_path.write_text(original_receipt)
    evaluation["seed"] = 2
    (args.out / "evaluation.json").write_text(json.dumps(evaluation))
    with pytest.raises(ValueError, match="year/seed differs"):
        e.verify_completed(args, protocol, expected)
    evaluation["seed"] = 1
    (args.out / "evaluation.json").write_text(json.dumps(evaluation))
    (args.out / m.KERNEL_ARM / "model.txt").write_text("changed body")
    with pytest.raises(ValueError, match="artifact changed"):
        e.verify_completed(args, protocol, expected)
