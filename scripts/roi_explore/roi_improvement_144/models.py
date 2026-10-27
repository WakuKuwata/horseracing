"""Frozen 144 decomposition of 143's OOF weights; research only."""
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

import lightgbm as lgb
import numpy as np
import pandas as pd

SOURCE = Path(__file__).resolve()
ROOT = SOURCE.parents[3]
_spec = importlib.util.spec_from_file_location("roi144_parent143", SOURCE.parents[1] / "roi_improvement_143/models.py")
parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parent)
base = parent.base
sha, canonical, save_json = base.sha, base.canonical, base.save_json
KEYS, PRED_KEYS = parent.KEYS, parent.PRED_KEYS
ARMS = ("within_race", "race_only")
WEIGHT_RECIPE = parent.WEIGHT_RECIPE
TRANSFORMS = {"within_race": "w_i / arithmetic_mean_race(w)", "race_only": "arithmetic_mean_race(w)"}
BUNDLES = {"speed": [], "pace": []}
KERNEL_ARM = "weighted_baseline"


def vector_sha(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype=float).tobytes()).hexdigest()


def weight_components(train: pd.DataFrame, original: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Decompose w=a*b using complete, contiguous, explicitly ordered fields."""
    required = KEYS + ["horse_number", "field_size", "year", "race_date"]
    if any(column not in train for column in required) or train[required].isna().any().any():
        raise ValueError("missing complete-race identity")
    if train.duplicated(KEYS).any() or train.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("duplicate starter")
    ordered = train.sort_values(["race_id", "horse_number"], kind="stable")
    if not train[KEYS].reset_index(drop=True).equals(ordered[KEYS].reset_index(drop=True)):
        raise ValueError("training starter order differs from 142")
    starts, counts = base.segments(train.race_id)
    if np.any(counts < 2) or not np.array_equal(np.repeat(counts, counts), train.field_size.to_numpy()):
        raise ValueError("incomplete race field")
    if any(train.groupby("race_id")[column].nunique().max() != 1 for column in ("year", "race_date")):
        raise ValueError("race spans dates or years")
    original = np.asarray(original, dtype=float)
    if original.shape != (len(train),) or not np.isfinite(original).all() or np.any(original <= 0):
        raise ValueError("original weights must be finite positive row vector")
    means = np.add.reduceat(original, starts) / counts
    a = np.repeat(means, counts)
    b = original / a
    if not np.isfinite(a).all() or not np.isfinite(b).all() or np.any(a <= 0) or np.any(b <= 0):
        raise ValueError("transformed weights must be finite positive")
    mean_error = float(np.max(np.abs(np.add.reduceat(b, starts) / counts - 1.)))
    product_error = float(np.max(np.abs(a*b-original)))
    if mean_error > 1e-12 or product_error > 1e-12:
        raise ValueError("weight decomposition identity failed")
    neutral = np.repeat(np.logical_and.reduceat(original == 1., starts), counts)
    if not np.array_equal(a[neutral], np.ones(neutral.sum())) or not np.array_equal(b[neutral], np.ones(neutral.sum())):
        raise ValueError("whole neutral fields must remain exactly one")
    stats = dict(races=len(counts), neutral_exact_one_rows=int(neutral.sum()),
                 race_mean_minimum=float(means.min()), race_mean_maximum=float(means.max()),
                 race_mean_unweighted_mean=float(means.mean()), race_mean_row_weighted_mean=float(a.mean()),
                 within_race_mean_max_abs_error=mean_error, product_max_abs_error=product_error)
    return a, b, stats


def training_weights(train: pd.DataFrame, oof: pd.DataFrame, arm: str) -> tuple[np.ndarray, dict]:
    if arm not in ARMS:
        raise ValueError("unknown 144 weight arm")
    original, parent_info = parent.training_weights(train, oof)
    a, b, stats = weight_components(train, original)
    transformed = b if arm == "within_race" else a
    info = dict(arm=arm, transform=TRANSFORMS[arm], kernel_arm=KERNEL_ARM,
                original_weight_info=parent_info, original_vector_sha256=vector_sha(original),
                vector_sha256=vector_sha(transformed), mean=float(transformed.mean()),
                minimum=float(transformed.min()), maximum=float(transformed.max()),
                global_renormalization=False, decomposition=stats)
    return transformed, info


def bound_dependencies(rows: Path, weights: Path) -> list[Path]:
    return [rows.resolve(), weights.resolve(), SOURCE, SOURCE.with_name("experiment.py"),
            Path(parent.__file__).resolve(), Path(base.__file__).resolve(), Path(base.legacy.__file__).resolve()]


def verify_protocol(path: Path, rows: Path, weights: Path) -> dict:
    if sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("protocol sidecar hash mismatch")
    protocol = json.loads(path.read_text())
    expected = dict(feature="144-race-normalized-weight", version=1, seeds=list(range(1, 16)),
                    max_workers=2, threads_each=1, can_adopt=False, untouched_holdout=False,
                    models=dict(objective="binary", rounds=base.FIXED_ROUNDS, params=base.BASE_PARAMS),
                    weight_recipe=WEIGHT_RECIPE, weight_transforms=TRANSFORMS,
                    arms=list(ARMS))
    for key, value in expected.items():
        if canonical(protocol.get(key)) != canonical(value):
            raise ValueError(f"protocol constraint differs: {key}")
    if protocol.get("window") != dict(training_from=2007, through=base.UPPER, model_outer=list(base.OUTER_YEARS)):
        raise ValueError("protocol window differs")
    for key, value in (("rows", rows), ("weights", weights)):
        declared = (ROOT / protocol[key]).resolve()
        if declared != value.resolve() or not declared.is_relative_to(ROOT):
            raise ValueError(f"protocol input path differs: {key}")
    hashes = protocol["inputs_and_source_sha256"]
    for dependency in bound_dependencies(rows, weights):
        if str(dependency.relative_to(ROOT)) not in hashes:
            raise ValueError(f"protocol does not bind {dependency}")
    for name, digest in hashes.items():
        dependency = (ROOT / name).resolve()
        if not dependency.is_relative_to(ROOT) or sha(dependency) != digest:
            raise ValueError(f"protocol input/source hash mismatch: {name}")
    return protocol


def run_identity(args, protocol: dict) -> dict:
    return dict(version=1, protocol_sha256=sha(args.protocol), rows_sha256=sha(args.rows),
                weights_sha256=sha(args.weights), year=args.year, seed=args.seed, arms=args.arms,
                weight_transforms=TRANSFORMS, kernel_arm=KERNEL_ARM, source_sha256=sha(SOURCE),
                dependencies={"models143": sha(Path(parent.__file__)), "models142": sha(Path(base.__file__)),
                              "legacy": sha(Path(base.legacy.__file__))},
                python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, lightgbm=lgb.__version__,
                research_only=True, can_adopt=False, untouched_holdout=False)


def fit_model(train, target, *, arm, seed, output, identity, weights, weight_info,
              rounds=base.FIXED_ROUNDS, overrides=None):
    if arm not in ARMS or weight_info.get("arm") != arm or weight_info.get("transform") != TRANSFORMS[arm]:
        raise ValueError("weight arm/transform identity differs")
    if weight_info.get("vector_sha256") != vector_sha(weights):
        raise ValueError("weight vector identity differs")
    return parent.fit_model(train, target, arm=KERNEL_ARM, seed=seed, bundles=BUNDLES, output=output,
                            identity=dict(identity, research_arm=arm, weight_transform=TRANSFORMS[arm]),
                            weights=weights, weight_info=weight_info, rounds=rounds, overrides=overrides)


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("command", choices=["evaluate"])
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, choices=base.OUTER_YEARS, required=True)
    ap.add_argument("--seed", type=int, choices=range(1, 16), required=True)
    ap.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    if args.arms != list(ARMS):
        ap.error("144 requires both fixed arms in their declared order")
    protocol = verify_protocol(args.protocol, args.rows, args.weights)
    identity = run_identity(args, protocol)
    base.prepare_run(args.out, identity, args.resume)
    evaluation = args.out / "evaluation.json"
    if evaluation.exists():
        raise ValueError("run already complete; outputs are never overwritten")
    data = base.read_rows(args.rows, upper=f"{args.year}-12-31")
    train, target = data.loc[data.year < args.year], data.loc[data.year == args.year]
    oof = pd.read_parquet(args.weights)
    records = {}
    for arm in args.arms:
        weights, info = training_weights(train, oof, arm)
        _, receipt = fit_model(train, target, arm=arm, seed=args.seed, output=args.out / arm,
                               identity=identity, weights=weights, weight_info=info)
        records[arm] = dict(metrics=receipt["metrics"], elapsed_s=receipt["elapsed_s"],
                            actual_iterations=receipt["actual_iterations"])
    verify_protocol(args.protocol, args.rows, args.weights)
    save_json(evaluation, dict(identity=identity, year=args.year, seed=args.seed, arms=records,
                              can_adopt=False, untouched_holdout=False, production_changed=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
