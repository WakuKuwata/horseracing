"""Frozen research-only binary market fits with existing numeric features (143)."""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_key] = "1"

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit

SOURCE = Path(__file__).resolve()
ROOT = SOURCE.parents[3]
_base_spec = importlib.util.spec_from_file_location("roi143_legacy_models", SOURCE.parents[1] / "roi_improvement_142/models.py")
base = importlib.util.module_from_spec(_base_spec)
_base_spec.loader.exec_module(base)
KEYS = ["race_id", "horse_id"]
PRED_KEYS = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
ARMS = ("speed", "pace", "weighted_baseline")
WEIGHT_RECIPE = {"center": 1.2, "width": .2, "amplitude": 1., "unavailable": 1.,
                 "normalization": "informative_complete_races_mean_one_neutral_exact_one"}

sha = base.sha
canonical = base.canonical
save_json = base.save_json


def load_data(rows_path: Path, additions_path: Path, bundles: dict, upper: str = base.UPPER) -> pd.DataFrame:
    """Keep 142's exact starters/order and require a one-to-one feature join."""
    columns = [column for arm in ("speed", "pace") for column in bundles[arm]]
    if not columns or len(columns) != len(set(columns)) or set(columns) & set(base.FEATURES + PRED_KEYS):
        raise ValueError("extra feature contract overlaps or is empty")
    data = base.read_rows(rows_path, upper=upper)
    attrs = dict(data.attrs)
    additions = pd.read_parquet(additions_path, columns=KEYS + columns)
    if additions[KEYS].isna().any().any() or additions.duplicated(KEYS).any():
        raise ValueError("duplicate or missing additions starter key")
    result = data.merge(additions, on=KEYS, how="left", validate="one_to_one", sort=False, indicator=True)
    if not result["_merge"].eq("both").all():
        raise ValueError("additions starter coverage is incomplete")
    result = result.drop(columns="_merge")
    if not result[KEYS].equals(data[KEYS]):
        raise ValueError("additions join changed starter order")
    values = result[columns].to_numpy(dtype=np.float32)
    if np.isinf(values).any():
        raise ValueError("extra features contain infinity")
    result.attrs = attrs
    return result


def feature_contract(arm: str, bundles: dict) -> tuple[list[str], list[str], list[str]]:
    if arm not in ARMS:
        raise ValueError("unknown research arm")
    extra = [] if arm == "weighted_baseline" else bundles[arm]
    numeric = base.NUM + extra
    return numeric, base.CAT, numeric + base.CAT


def design_matrix(data: pd.DataFrame, maps: dict, numeric: list[str]) -> np.ndarray:
    numbers = data[numeric].to_numpy(dtype=np.float32, copy=True)
    cats = [data[c].astype("string").map(maps[c]).to_numpy(dtype=np.float32, na_value=np.nan) for c in base.CAT]
    return np.column_stack([numbers] + cats)


def training_weights(train: pd.DataFrame, oof: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """Label-independent OOF-EV weights; unavailable past predictions stay neutral."""
    required = KEYS + ["p_oof", "oof_train_through"]
    if any(c not in oof for c in required) or oof[KEYS].isna().any().any() or oof.duplicated(KEYS).any():
        raise ValueError("invalid OOF columns or duplicate starter")
    matched = train[KEYS + ["year", "odds"]].merge(oof[required], on=KEYS, how="left", validate="one_to_one", sort=False)
    if not matched[KEYS].equals(train[KEYS].reset_index(drop=True)):
        raise ValueError("OOF join changed training order")
    present = matched.p_oof.notna()
    if not (matched.p_oof.notna() == matched.oof_train_through.notna()).all():
        raise ValueError("OOF probability/provenance availability differs")
    probabilities = matched.loc[present, "p_oof"].to_numpy(float)
    cutoff = matched.loc[present, "oof_train_through"].to_numpy(float)
    if not np.isfinite(probabilities).all() or np.any((probabilities < 0) | (probabilities > 1)):
        raise ValueError("invalid OOF probability")
    if not np.isfinite(cutoff).all() or np.any(cutoff != np.floor(cutoff)) or np.any(cutoff >= matched.loc[present, "year"].to_numpy()):
        raise ValueError("OOF prediction is not strictly past")
    informative = present.groupby(matched.race_id).transform("all")
    weights = np.ones(len(train), dtype=float)
    ev = matched.loc[informative, "p_oof"].to_numpy(float) * matched.loc[informative, "odds"].to_numpy(float)
    weights[informative.to_numpy()] += np.exp(-.5 * ((ev - WEIGHT_RECIPE["center"]) / WEIGHT_RECIPE["width"]) ** 2)
    unnormalized_mean = float(weights[informative].mean()) if informative.any() else 1.
    weights[informative.to_numpy()] /= unnormalized_mean
    return weights, dict(recipe=WEIGHT_RECIPE, available=int(present.sum()), unavailable=int((~present).sum()),
                         informative_rows=int(informative.sum()), informative_races=int(matched.loc[informative, "race_id"].nunique()),
                         neutral_rows=int((~informative).sum()), neutral_races=int(matched.loc[~informative, "race_id"].nunique()),
                         raw_mean=unnormalized_mean, mean=float(weights.mean()), minimum=float(weights.min()),
                         maximum=float(weights.max()), vector_sha256=hashlib.sha256(weights.tobytes()).hexdigest())


def verify_protocol(path: Path, rows: Path, additions: Path, weights: Path | None = None) -> dict:
    if sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("protocol sidecar hash mismatch")
    protocol = json.loads(path.read_text())
    expected = dict(feature="143-existing-feature-roi", version=1, seeds=list(range(1, 16)),
                    max_workers=2, threads_each=1, can_adopt=False, untouched_holdout=False)
    for key, value in expected.items():
        if canonical(protocol.get(key)) != canonical(value):
            raise ValueError(f"protocol constraint differs: {key}")
    window = protocol["window"]
    if window.get("training_from") != 2007 or window.get("through") != base.UPPER or window.get("model_outer") != list(base.OUTER_YEARS):
        raise ValueError("protocol window differs")
    if protocol.get("models") != {"objective": "binary", "rounds": base.FIXED_ROUNDS, "params": base.BASE_PARAMS}:
        raise ValueError("protocol model recipe differs")
    if protocol.get("weight_recipe") != WEIGHT_RECIPE:
        raise ValueError("protocol weight recipe differs")
    bundles = protocol["bundles"]
    if set(bundles) != {"speed", "pace"}:
        raise ValueError("protocol bundles differ")
    columns = bundles["speed"] + bundles["pace"]
    if not columns or len(set(columns)) != len(columns) or set(columns) & set(base.FEATURES + PRED_KEYS):
        raise ValueError("invalid protocol extra features")
    hashes = protocol["inputs_and_source_sha256"]
    required = [rows.resolve(), additions.resolve(), SOURCE, SOURCE.with_name("experiment.py"), Path(base.__file__).resolve(), Path(base.legacy.__file__).resolve()]
    if weights is not None:
        required.append(weights.resolve())
    for dependency in required:
        if str(dependency.relative_to(ROOT)) not in hashes:
            raise ValueError(f"protocol does not bind {dependency}")
    for name, digest in hashes.items():
        dependency = (ROOT / name).resolve()
        if not dependency.is_relative_to(ROOT) or sha(dependency) != digest:
            raise ValueError(f"protocol input/source hash mismatch: {name}")
    return protocol


def verify_comparison_marker(path: Path, protocol: Path) -> dict:
    if sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("comparison marker sidecar differs")
    marker = json.loads(path.read_text())
    if marker.get("protocol_sha256") != sha(protocol) or marker.get("stage") != "features_complete":
        raise ValueError("weighted stage requires completed first comparison")
    hashes = dict(marker.get("hashes", {}))
    if "summary_path" in marker and "summary_sha256" in marker:
        hashes[marker["summary_path"]] = marker["summary_sha256"]
    for name, digest in hashes.items():
        dependency = (ROOT / name).resolve()
        if not dependency.is_relative_to(ROOT) or sha(dependency) != digest:
            raise ValueError("first comparison artifact hash differs")
    if not hashes:
        raise ValueError("comparison marker must bind comparison artifacts")
    return marker


def run_identity(args, protocol: dict) -> dict:
    return dict(version=1, protocol_sha256=sha(args.protocol), rows_sha256=sha(args.rows), additions_sha256=sha(args.additions),
                weights_sha256=sha(args.weights) if args.weights else None,
                comparison_marker_sha256=sha(args.comparison_marker) if args.comparison_marker else None,
                year=args.year, seed=args.seed, arms=args.arms, bundles=protocol["bundles"],
                source_sha256=sha(SOURCE), dependencies={"models142": sha(Path(base.__file__)), "legacy": sha(Path(base.legacy.__file__))},
                python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, lightgbm=lgb.__version__,
                research_only=True, can_adopt=False, untouched_holdout=False)


def fit_model(train: pd.DataFrame, target: pd.DataFrame, *, arm: str, seed: int, bundles: dict,
              output: Path, identity: dict, weights=None, weight_info=None, rounds=base.FIXED_ROUNDS,
              overrides=None) -> tuple[np.ndarray, dict]:
    if train.empty or target.empty or train.race_date.max() >= target.race_date.min():
        raise ValueError("fit target must be strictly later than training")
    if arm == "weighted_baseline" and weights is None or arm != "weighted_baseline" and weights is not None:
        raise ValueError("training weight/arm contract differs")
    if weights is not None:
        weights = np.asarray(weights, dtype=float)
        if len(weights) != len(train) or not np.isfinite(weights).all() or np.any(weights <= 0):
            raise ValueError("invalid training weights")
        if not weight_info or weight_info.get("vector_sha256") != hashlib.sha256(weights.tobytes()).hexdigest():
            raise ValueError("training weight identity differs")
    numeric, cats, features = feature_contract(arm, bundles)
    params = dict(base.BASE_PARAMS, seed=seed, objective="binary", metric="binary_logloss", **(overrides or {}))
    recipe = dict(arm=arm, seed=seed, rounds=rounds, params=params, features=features,
                  train_start=str(train.race_date.min()), train_end=str(train.race_date.max()),
                  target_start=str(target.race_date.min()), target_end=str(target.race_date.max()),
                  train_rows=len(train), target_rows=len(target), weights=weight_info, identity=identity)
    receipt_path = output / "receipt.json"
    if output.exists():
        if not receipt_path.exists():
            raise ValueError("incomplete fit output; cannot safely resume")
        receipt = json.loads(receipt_path.read_text())
        if canonical(receipt["recipe"]) != canonical(recipe):
            raise ValueError("resumed fit recipe differs")
        for name, digest in receipt["hashes"].items():
            if sha(output / name) != digest:
                raise ValueError("resumed fit file hash differs")
        predictions = pd.read_parquet(output / "predictions.parquet")
        if not predictions[PRED_KEYS].equals(target[PRED_KEYS].reset_index(drop=True)):
            raise ValueError("resumed target rows differ")
        return predictions.p.to_numpy(), receipt
    output.mkdir(parents=True)
    started = time.monotonic()
    maps = base.category_maps(train)
    xtr, xte = design_matrix(train, maps, numeric), design_matrix(target, maps, numeric)
    dataset = lgb.Dataset(xtr, train.won.to_numpy(float), weight=weights, feature_name=features,
                          categorical_feature=list(range(len(numeric), len(features))))
    booster = lgb.train(params, dataset, num_boost_round=rounds)
    raw = booster.predict(xte, raw_score=True, num_threads=1)
    p = expit(raw)
    booster.save_model(str(output / "model.txt"))
    loaded = lgb.Booster(model_file=str(output / "model.txt"))
    difference = float(np.max(np.abs(raw - loaded.predict(xte, raw_score=True, num_threads=1))))
    if difference > 1e-12:
        raise ValueError("booster roundtrip prediction mismatch")
    _, counts = base.segments(target.race_id)
    predictions = target[PRED_KEYS].copy()
    predictions["p"], predictions["p_race"] = p, base.race_normalize(p, counts)
    predictions["pred"] = 100. * (p * target.odds.to_numpy(float) - 1.)
    predictions.to_parquet(output / "predictions.parquet", index=False)
    save_json(output / "feature_spec.json", dict(features=numeric, cats=cats, cat_maps=maps, feature_order=features,
              feature_hash=hashlib.sha256(canonical(features).encode()).hexdigest(), objective="binary", params=params,
              best_iteration=int(booster.current_iteration()), train_from=2007, train_through=int(train.year.max()),
              trained_for_year=int(target.year.min()), training_cutoff=str(train.race_date.max()),
              research_only=True, can_adopt=False))
    receipt = dict(recipe=recipe, actual_iterations=int(booster.current_iteration()), elapsed_s=time.monotonic()-started,
                   metrics=base.metrics(target, p), roundtrip_max_abs_raw_difference=difference,
                   hashes={name: sha(output / name) for name in ("model.txt", "predictions.parquet", "feature_spec.json")})
    save_json(receipt_path, receipt)
    print(f"{arm}: n={len(train):,} rounds={receipt['actual_iterations']} seconds={receipt['elapsed_s']:.1f}", flush=True)
    return p, receipt


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("command", choices=["evaluate"])
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--additions", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, choices=base.OUTER_YEARS, required=True)
    ap.add_argument("--seed", type=int, choices=range(1, 16), required=True)
    ap.add_argument("--arms", nargs="+", choices=ARMS, default=["speed", "pace"])
    ap.add_argument("--weights", type=Path)
    ap.add_argument("--comparison-marker", type=Path)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    weighted = args.arms == ["weighted_baseline"]
    if not weighted and ("weighted_baseline" in args.arms or args.weights or args.comparison_marker):
        ap.error("weighted baseline must be a separate later stage")
    if weighted and (not args.weights or not args.comparison_marker):
        ap.error("weighted stage requires --weights and --comparison-marker")
    protocol = verify_protocol(args.protocol, args.rows, args.additions, args.weights)
    if weighted:
        verify_comparison_marker(args.comparison_marker, args.protocol)
    identity = run_identity(args, protocol)
    base.prepare_run(args.out, identity, args.resume)
    evaluation = args.out / "evaluation.json"
    if evaluation.exists():
        raise ValueError("run already complete; outputs are never overwritten")
    data = load_data(args.rows, args.additions, protocol["bundles"], upper=f"{args.year}-12-31")
    train, target = data.loc[data.year < args.year], data.loc[data.year == args.year]
    weights, weight_info = training_weights(train, pd.read_parquet(args.weights)) if weighted else (None, None)
    records = {}
    for arm in args.arms:
        _, receipt = fit_model(train, target, arm=arm, seed=args.seed, bundles=protocol["bundles"],
                              output=args.out / arm, identity=identity, weights=weights, weight_info=weight_info)
        records[arm] = dict(metrics=receipt["metrics"], elapsed_s=receipt["elapsed_s"], actual_iterations=receipt["actual_iterations"])
    verify_protocol(args.protocol, args.rows, args.additions, args.weights)
    if weighted:
        verify_comparison_marker(args.comparison_marker, args.protocol)
    save_json(evaluation, dict(identity=identity, year=args.year, seed=args.seed, arms=records,
                              can_adopt=False, untouched_holdout=False, production_changed=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
