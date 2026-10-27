"""Frozen 146: strictly past old OOF mentors, then original 143 weighting."""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_key] = "1"

import argparse
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
_spec = importlib.util.spec_from_file_location("roi146_parent143", SOURCE.parents[1] / "roi_improvement_143/models.py")
parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parent)
base = parent.base
sha, canonical, save_json = base.sha, base.canonical, base.save_json
KEYS, PRED_KEYS = parent.KEYS, parent.PRED_KEYS
FEATURES = base.FEATURES
WEIGHT_RECIPE = parent.WEIGHT_RECIPE
MENTOR_YEARS = tuple(range(2013, 2019))
CANDIDATE_YEARS = base.OUTER_YEARS
ARMS = ("weighted_backfill",)
KERNEL_ARM = "weighted_baseline"


def stage_years(stage: str) -> tuple:
    if stage not in ("mentor", "candidate"):
        raise ValueError("unknown 146 stage")
    return MENTOR_YEARS if stage == "mentor" else CANDIDATE_YEARS


def stage_arm(stage: str) -> str:
    stage_years(stage)
    return "baseline" if stage == "mentor" else ARMS[0]


def protocol_name(stage: str) -> str:
    stage_years(stage)
    return "mentor_protocol.json" if stage == "mentor" else "protocol.json"


def marker_name(stage: str) -> str:
    stage_years(stage)
    return "mentor_models_complete.json" if stage == "mentor" else "models_complete.json"


def bound_dependencies(rows: Path, weights: Path | None = None) -> list[Path]:
    files = [rows.resolve(), SOURCE, SOURCE.with_name("experiment.py"), Path(parent.__file__).resolve(),
             Path(base.__file__).resolve(), Path(base.legacy.__file__).resolve()]
    return files + ([weights.resolve()] if weights is not None else [])


def verify_protocol(path: Path, rows: Path, weights: Path | None = None) -> dict:
    if sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("protocol sidecar hash mismatch")
    protocol = json.loads(path.read_text())
    stage = protocol.get("stage")
    years = stage_years(stage)
    expected = dict(feature="146-old-oof-backfill", version=1, seeds=list(range(1, 16)),
                    max_workers=2, threads_each=1, can_adopt=False, untouched_holdout=False,
                    models=dict(objective="binary", rounds=base.FIXED_ROUNDS, params=base.BASE_PARAMS),
                    features=FEATURES, weight_recipe=WEIGHT_RECIPE, arms=[stage_arm(stage)],
                    mentor_years=list(MENTOR_YEARS), candidate_years=list(CANDIDATE_YEARS),
                    window=dict(training_from=2007, through=base.UPPER, model_outer=list(years)))
    for key, value in expected.items():
        if canonical(protocol.get(key)) != canonical(value):
            raise ValueError(f"protocol constraint differs: {key}")
    if (stage == "mentor") != (weights is None):
        raise ValueError("protocol stage/weights differs")
    paths = {"rows": rows}
    if weights is not None:
        paths["weights"] = weights
    for key, value in paths.items():
        declared = (ROOT / protocol[key]).resolve()
        if declared != value.resolve() or not declared.is_relative_to(ROOT):
            raise ValueError(f"protocol input path differs: {key}")
    hashes = protocol["inputs_and_source_sha256"]
    for dependency in bound_dependencies(rows, weights):
        if str(dependency.relative_to(ROOT)) not in hashes:
            raise ValueError(f"protocol does not bind {dependency}")
    if stage == "candidate":
        for name in ("mentor_protocol.json", "mentor_protocol.sha256", "mentor_models_complete.json", "weights_manifest.json"):
            dependency = (path.parent / name).resolve()
            if str(dependency.relative_to(ROOT)) not in hashes:
                raise ValueError(f"candidate protocol does not bind {name}")
    for name, digest in hashes.items():
        dependency = (ROOT / name).resolve()
        if not dependency.is_relative_to(ROOT) or sha(dependency) != digest:
            raise ValueError(f"protocol input/source hash mismatch: {name}")
    return protocol


def run_identity(args, protocol: dict) -> dict:
    stage = args.stage
    if stage != protocol["stage"] or args.year not in stage_years(stage):
        raise ValueError("run stage/year differs")
    return dict(version=1, stage=stage, protocol_sha256=sha(args.protocol), rows_sha256=sha(args.rows),
                weights_sha256=sha(args.weights) if args.weights is not None else None,
                year=args.year, seed=args.seed, arms=[stage_arm(stage)], features=FEATURES,
                research_arm=stage_arm(stage), kernel_arm="baseline" if stage == "mentor" else KERNEL_ARM,
                source_sha256=sha(SOURCE),
                dependencies={"models143": sha(Path(parent.__file__)), "models142": sha(Path(base.__file__)),
                              "legacy": sha(Path(base.legacy.__file__))},
                model_recipe=protocol["models"], weight_recipe=WEIGHT_RECIPE,
                python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, lightgbm=lgb.__version__,
                research_only=True, can_adopt=False, untouched_holdout=False)


def annual_split(data: pd.DataFrame, year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    train, target = data.loc[data.year < year], data.loc[data.year == year]
    if train.empty or target.empty or train.year.min() != 2007 or train.year.max() != year - 1:
        raise ValueError("missing strict annual split")
    if train.race_date.max() >= target.race_date.min():
        raise ValueError("fit target must be strictly later than training")
    base.validate_races(train)
    base.validate_races(target)
    return train, target


def training_weights(train: pd.DataFrame, oof: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """Preserve 143 arithmetic, and require complete 2013+ past-teacher coverage."""
    weights, info = parent.training_weights(train, oof)
    required = KEYS + ["p_oof", "oof_train_through"]
    matched = train[KEYS + ["year"]].merge(oof[required], on=KEYS, how="left", validate="one_to_one", sort=False)
    covered = matched.year.between(2013, 2025)
    if not matched.loc[covered, "p_oof"].notna().all():
        raise ValueError("backfilled OOF coverage is incomplete")
    if matched.loc[~covered, "p_oof"].notna().any():
        raise ValueError("cold-start years must stay neutral")
    if not (matched.loc[covered, "oof_train_through"].to_numpy() == matched.loc[covered, "year"].to_numpy() - 1).all():
        raise ValueError("backfilled OOF must be trained through previous year")
    return weights, info


def fit_model(train: pd.DataFrame, target: pd.DataFrame, *, stage: str, seed: int, output: Path,
              identity: dict, weights=None, weight_info=None, rounds=base.FIXED_ROUNDS, overrides=None):
    if train.empty or target.empty or train.race_date.max() >= target.race_date.min():
        raise ValueError("fit target must be strictly later than training")
    stage_years(stage)
    if stage == "mentor":
        if weights is not None or weight_info is not None:
            raise ValueError("mentor must be an unweighted baseline")
        return base.fit_model(train, target, arm="baseline", seed=seed, rounds=rounds, output=output,
                              identity=identity, overrides=overrides, earlystop=False)
    if weights is None:
        raise ValueError("candidate requires backfilled weights")
    return parent.fit_model(train, target, arm=KERNEL_ARM, seed=seed, bundles={}, output=output,
                            identity=identity, weights=weights, weight_info=weight_info, rounds=rounds,
                            overrides=overrides)


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("command", choices=["evaluate"])
    ap.add_argument("--stage", choices=["mentor", "candidate"], required=True)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--weights", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--seed", type=int, choices=range(1, 16), required=True)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    if args.year not in stage_years(args.stage):
        ap.error("year outside fixed stage range")
    protocol = verify_protocol(args.protocol, args.rows, args.weights)
    identity = run_identity(args, protocol)
    base.prepare_run(args.out, identity, args.resume)
    evaluation = args.out / "evaluation.json"
    if evaluation.exists():
        raise ValueError("run already complete; outputs are never overwritten")
    data = base.read_rows(args.rows, upper=f"{args.year}-12-31")
    train, target = annual_split(data, args.year)
    weights, info = (None, None) if args.stage == "mentor" else training_weights(train, pd.read_parquet(args.weights))
    arm = stage_arm(args.stage)
    _, receipt = fit_model(train, target, stage=args.stage, seed=args.seed, output=args.out / arm,
                           identity=identity, weights=weights, weight_info=info)
    records = {arm: dict(metrics=receipt["metrics"], elapsed_s=receipt["elapsed_s"],
                         actual_iterations=receipt["actual_iterations"])}
    verify_protocol(args.protocol, args.rows, args.weights)
    save_json(evaluation, dict(identity=identity, stage=args.stage, year=args.year, seed=args.seed, arms=records,
                              can_adopt=False, untouched_holdout=False, production_changed=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
