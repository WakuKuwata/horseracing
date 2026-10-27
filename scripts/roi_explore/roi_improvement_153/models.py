# ruff: noqa: E402
# Thread limits must precede native numerical imports.
"""Frozen153: remove exactly three columns from original143 weighted binary77."""

from __future__ import annotations

import os

for _key in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
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
_spec = importlib.util.spec_from_file_location(
    "roi153_parent143", SOURCE.parents[1] / "roi_improvement_143/models.py"
)
parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parent)
base = parent.base
sha, canonical, save_json = base.sha, base.canonical, base.save_json
KEYS, PRED_KEYS = parent.KEYS, parent.PRED_KEYS
ARMS = ("market_drop3",)
KERNEL_ARM = "market_drop3"
WEIGHT_RECIPE = parent.WEIGHT_RECIPE
DROPPED = ["odds", "popularity", "odds_rank"]
BUNDLES = {"drop": DROPPED}
NUM = [c for c in base.NUM if c not in DROPPED]
FEATURES = NUM + base.CAT


def feature_contract(bundles: dict) -> tuple[list[str], list[str], list[str]]:
    if canonical(bundles) != canonical(BUNDLES):
        raise ValueError("market drop3 feature contract differs")
    if (
        len(FEATURES) != 74
        or [c for c in base.FEATURES if c not in DROPPED] != FEATURES
    ):
        raise ValueError("market drop3 feature order differs")
    return NUM, base.CAT, FEATURES


def training_weights(train: pd.DataFrame, oof: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """Use the original 143 vector verbatim; no 144 race normalization."""
    return parent.training_weights(train, oof)


def bound_dependencies(rows: Path, additions: Path, weights: Path) -> list[Path]:
    return [
        rows.resolve(),
        additions.resolve(),
        weights.resolve(),
        SOURCE,
        SOURCE.with_name("experiment.py"),
        Path(parent.__file__).resolve(),
        Path(base.__file__).resolve(),
        Path(base.legacy.__file__).resolve(),
    ]


def verify_protocol(path: Path, rows: Path, additions: Path, weights: Path) -> dict:
    if sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("protocol sidecar hash mismatch")
    protocol = json.loads(path.read_text())
    expected = dict(
        feature="153-market-three-column-ablation",
        version=1,
        seeds=list(range(1, 16)),
        max_workers=2,
        threads_each=1,
        can_adopt=False,
        untouched_holdout=False,
        models=dict(
            objective="binary", rounds=base.FIXED_ROUNDS, params=base.BASE_PARAMS
        ),
        weight_recipe=WEIGHT_RECIPE,
        arms=list(ARMS),
        bundles=BUNDLES,
    )
    for key, value in expected.items():
        if canonical(protocol.get(key)) != canonical(value):
            raise ValueError(f"protocol constraint differs: {key}")
    if protocol.get("window") != dict(
        training_from=2007, through=base.UPPER, model_outer=list(base.OUTER_YEARS)
    ):
        raise ValueError("protocol window differs")
    for key, value in (("rows", rows), ("additions", additions), ("weights", weights)):
        declared = (ROOT / protocol[key]).resolve()
        if declared != value.resolve() or not declared.is_relative_to(ROOT):
            raise ValueError(f"protocol input path differs: {key}")
    hashes = protocol["inputs_and_source_sha256"]
    for dependency in bound_dependencies(rows, additions, weights):
        if str(dependency.relative_to(ROOT)) not in hashes:
            raise ValueError(f"protocol does not bind {dependency}")
    for name, digest in hashes.items():
        dependency = (ROOT / name).resolve()
        if not dependency.is_relative_to(ROOT) or sha(dependency) != digest:
            raise ValueError(f"protocol input/source hash mismatch: {name}")
    return protocol


def run_identity(args, protocol: dict) -> dict:
    return dict(
        version=1,
        protocol_sha256=sha(args.protocol),
        rows_sha256=sha(args.rows),
        additions_sha256=sha(args.additions),
        weights_sha256=sha(args.weights),
        year=args.year,
        seed=args.seed,
        arms=args.arms,
        bundles=protocol["bundles"],
        kernel_arm=KERNEL_ARM,
        source_sha256=sha(SOURCE),
        dependencies={
            "models143": sha(Path(parent.__file__)),
            "models142": sha(Path(base.__file__)),
            "legacy": sha(Path(base.legacy.__file__)),
        },
        python=platform.python_version(),
        numpy=np.__version__,
        pandas=pd.__version__,
        lightgbm=lgb.__version__,
        research_only=True,
        can_adopt=False,
        untouched_holdout=False,
    )


def fit_model(
    train: pd.DataFrame,
    target: pd.DataFrame,
    *,
    arm: str,
    seed: int,
    bundles: dict,
    output: Path,
    identity: dict,
    weights=None,
    weight_info=None,
    rounds=base.FIXED_ROUNDS,
    overrides=None,
) -> tuple[np.ndarray, dict]:
    if train.empty or target.empty or train.race_date.max() >= target.race_date.min():
        raise ValueError("fit target must be strictly later than training")
    if arm != KERNEL_ARM or weights is None:
        raise ValueError("training weight/arm contract differs")
    if weights is not None:
        weights = np.asarray(weights, dtype=float)
        if (
            len(weights) != len(train)
            or not np.isfinite(weights).all()
            or np.any(weights <= 0)
        ):
            raise ValueError("invalid training weights")
        if (
            not weight_info
            or weight_info.get("vector_sha256")
            != hashlib.sha256(weights.tobytes()).hexdigest()
        ):
            raise ValueError("training weight identity differs")
    numeric, cats, features = feature_contract(bundles)
    params = dict(
        base.BASE_PARAMS,
        seed=seed,
        objective="binary",
        metric="binary_logloss",
        **(overrides or {}),
    )
    recipe = dict(
        arm=arm,
        seed=seed,
        rounds=rounds,
        params=params,
        features=features,
        train_start=str(train.race_date.min()),
        train_end=str(train.race_date.max()),
        target_start=str(target.race_date.min()),
        target_end=str(target.race_date.max()),
        train_rows=len(train),
        target_rows=len(target),
        weights=weight_info,
        identity=identity,
    )
    receipt_path = output / "receipt.json"
    if output.exists():
        if not receipt_path.exists():
            raise ValueError("incomplete fit output; cannot safely resume")
        receipt = json.loads(receipt_path.read_text())
        if canonical(receipt["recipe"]) != canonical(recipe):
            raise ValueError("resumed fit recipe differs")
        if set(receipt["hashes"]) != {
            "model.txt",
            "predictions.parquet",
            "feature_spec.json",
        }:
            raise ValueError("resumed artifact inventory differs")
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
    xtr, xte = (
        parent.design_matrix(train, maps, numeric),
        parent.design_matrix(target, maps, numeric),
    )
    dataset = lgb.Dataset(
        xtr,
        train.won.to_numpy(float),
        weight=weights,
        feature_name=features,
        categorical_feature=list(range(len(numeric), len(features))),
    )
    booster = lgb.train(params, dataset, num_boost_round=rounds)
    raw = booster.predict(xte, raw_score=True, num_threads=1)
    p = expit(raw)
    booster.save_model(str(output / "model.txt"))
    loaded = lgb.Booster(model_file=str(output / "model.txt"))
    difference = float(
        np.max(np.abs(raw - loaded.predict(xte, raw_score=True, num_threads=1)))
    )
    if difference > 1e-12:
        raise ValueError("booster roundtrip prediction mismatch")
    _, counts = base.segments(target.race_id)
    predictions = target[PRED_KEYS].copy()
    predictions["p"], predictions["p_race"] = p, base.race_normalize(p, counts)
    predictions["pred"] = 100.0 * (p * target.odds.to_numpy(float) - 1.0)
    predictions.to_parquet(output / "predictions.parquet", index=False)
    save_json(
        output / "feature_spec.json",
        dict(
            features=numeric,
            cats=cats,
            cat_maps=maps,
            feature_order=features,
            feature_hash=hashlib.sha256(canonical(features).encode()).hexdigest(),
            objective="binary",
            params=params,
            best_iteration=int(booster.current_iteration()),
            train_from=2007,
            train_through=int(train.year.max()),
            trained_for_year=int(target.year.min()),
            training_cutoff=str(train.race_date.max()),
            research_only=True,
            can_adopt=False,
        ),
    )
    receipt = dict(
        recipe=recipe,
        actual_iterations=int(booster.current_iteration()),
        elapsed_s=time.monotonic() - started,
        metrics=base.metrics(target, p),
        roundtrip_max_abs_raw_difference=difference,
        hashes={
            name: sha(output / name)
            for name in ("model.txt", "predictions.parquet", "feature_spec.json")
        },
    )
    save_json(receipt_path, receipt)
    print(
        f"{arm}: n={len(train):,} rounds={receipt['actual_iterations']} seconds={receipt['elapsed_s']:.1f}",
        flush=True,
    )
    return p, receipt


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("command", choices=["evaluate"])
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--additions", type=Path, required=True)
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, choices=base.OUTER_YEARS, required=True)
    ap.add_argument("--seed", type=int, choices=range(1, 16), required=True)
    ap.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    if args.arms != list(ARMS):
        ap.error("153 requires its single fixed market_drop3 arm")
    protocol = verify_protocol(args.protocol, args.rows, args.additions, args.weights)
    identity = run_identity(args, protocol)
    base.prepare_run(args.out, identity, args.resume)
    evaluation = args.out / "evaluation.json"
    if evaluation.exists():
        raise ValueError("run already complete; outputs are never overwritten")
    data = base.read_rows(args.rows, upper=f"{args.year}-12-31")
    train, target = data.loc[data.year < args.year], data.loc[data.year == args.year]
    weights, info = training_weights(train, pd.read_parquet(args.weights))
    _, receipt = fit_model(
        train,
        target,
        arm=KERNEL_ARM,
        seed=args.seed,
        bundles=protocol["bundles"],
        output=args.out / KERNEL_ARM,
        identity=identity,
        weights=weights,
        weight_info=info,
    )
    records = {
        KERNEL_ARM: dict(
            metrics=receipt["metrics"],
            elapsed_s=receipt["elapsed_s"],
            actual_iterations=receipt["actual_iterations"],
        )
    }
    verify_protocol(args.protocol, args.rows, args.additions, args.weights)
    save_json(
        evaluation,
        dict(
            identity=identity,
            year=args.year,
            seed=args.seed,
            arms=records,
            can_adopt=False,
            untouched_holdout=False,
            production_changed=False,
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
