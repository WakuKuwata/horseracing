"""Run either frozen 146 stage using at most two single-thread workers."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("roi146_models", HERE / "models.py")
models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(models)


def configuration(out: Path, stage: str) -> tuple[dict, dict]:
    protocol_path = out / models.protocol_name(stage)
    protocol = json.loads(protocol_path.read_text())
    paths = {"rows": models.ROOT / protocol["rows"],
             "weights": models.ROOT / protocol["weights"] if stage == "candidate" else None}
    models.verify_protocol(protocol_path, paths["rows"], paths["weights"])
    if protocol["stage"] != stage:
        raise ValueError("requested stage differs from protocol")
    return protocol, paths


def job_args(out: Path, year: int, seed: int, paths: dict, stage: str) -> SimpleNamespace:
    directory = "mentor_models" if stage == "mentor" else "models"
    return SimpleNamespace(stage=stage, protocol=out / models.protocol_name(stage), rows=paths["rows"],
                           weights=paths["weights"], year=year, seed=seed,
                           out=out / directory / f"seed_{seed:02d}" / f"year_{year}")


def command(args, resume=False) -> list[str]:
    result = [sys.executable, str(HERE / "models.py"), "evaluate", "--stage", args.stage,
              "--protocol", str(args.protocol), "--rows", str(args.rows), "--out", str(args.out),
              "--year", str(args.year), "--seed", str(args.seed)]
    if args.weights is not None:
        result += ["--weights", str(args.weights)]
    return result + (["--resume"] if resume else [])


def expected_recipes(paths: dict, years, stage: str) -> dict:
    """Retain only per-year metadata; recompute actual weights before resuming."""
    data = models.base.read_rows(paths["rows"], upper=f"{max(years)}-12-31")
    oof = models.pd.read_parquet(paths["weights"]) if stage == "candidate" else None
    result = {}
    for year in years:
        train, target = models.annual_split(data, year)
        common = dict(train_start=str(train.race_date.min()), train_end=str(train.race_date.max()),
                      target_start=str(target.race_date.min()), target_end=str(target.race_date.max()),
                      train_rows=len(train), target_rows=len(target))
        if stage == "mentor":
            common.update(excluded_races=train.attrs.get("excluded_races", target.attrs.get("excluded_races", [])))
        else:
            weights, info = models.training_weights(train, oof)
            common["weights"] = info
            del weights
        result[year] = {models.stage_arm(stage): common}
    return result


def verify_completed(args, protocol: dict, expected: dict | None = None) -> None:
    identity = models.run_identity(args, protocol)
    actual = json.loads((args.out / "evaluation.json").read_text())
    arm = models.stage_arm(args.stage)
    if models.canonical(actual["identity"]) != models.canonical(identity) or set(actual["arms"]) != {arm}:
        raise ValueError("completed evaluation identity differs")
    if actual.get("stage") != args.stage or actual.get("year") != args.year or actual.get("seed") != args.seed:
        raise ValueError("completed evaluation stage/year/seed differs")
    if any(actual.get(key) is not False for key in ("can_adopt", "untouched_holdout", "production_changed")):
        raise ValueError("completed evaluation research constraints differ")
    if models.canonical(json.loads((args.out / "identity.json").read_text())) != models.canonical(identity):
        raise ValueError("completed directory identity differs")
    if expected is None:
        expected = expected_recipes({"rows": args.rows, "weights": args.weights}, [args.year], args.stage)[args.year]
    receipt = json.loads((args.out / arm / "receipt.json").read_text())
    recipe = dict(arm="baseline" if args.stage == "mentor" else models.KERNEL_ARM,
                  seed=args.seed, rounds=models.base.FIXED_ROUNDS, identity=identity, **expected[arm])
    if args.stage == "mentor":
        recipe.update(overrides={}, earlystop=False)
    else:
        recipe.update(params=dict(models.base.BASE_PARAMS, seed=args.seed, objective="binary", metric="binary_logloss"),
                      features=models.FEATURES)
    if models.canonical(receipt["recipe"]) != models.canonical(recipe):
        raise ValueError("completed fit recipe differs")
    if receipt.get("actual_iterations") != models.base.FIXED_ROUNDS or receipt.get("roundtrip_max_abs_raw_difference", 1.) > 1e-12:
        raise ValueError("completed fit iterations/roundtrip differs")
    if args.stage == "mentor" and receipt.get("best_iteration") != models.base.FIXED_ROUNDS:
        raise ValueError("completed mentor best iteration differs")
    record = dict(metrics=receipt["metrics"], elapsed_s=receipt["elapsed_s"], actual_iterations=receipt["actual_iterations"])
    if models.canonical(actual["arms"][arm]) != models.canonical(record):
        raise ValueError("completed evaluation/receipt differs")
    if set(receipt["hashes"]) != {"model.txt", "predictions.parquet", "feature_spec.json"}:
        raise ValueError("completed artifact inventory differs")
    for name, digest in receipt["hashes"].items():
        if models.sha(args.out / arm / name) != digest:
            raise ValueError("completed model artifact changed")


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("stage", choices=["mentor", "candidate"])
    ap.add_argument("--out", type=Path, default=models.ROOT / "artifacts/146-old-oof-backfill")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    protocol, paths = configuration(args.out, args.stage)
    expected = expected_recipes(paths, protocol["window"]["model_outer"], args.stage)
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    jobs = [(year, seed) for year in protocol["window"]["model_outer"] for seed in protocol["seeds"]]
    logs = args.out / ("mentor_logs" if args.stage == "mentor" else "model_logs")
    logs.mkdir(exist_ok=True)
    started = time.monotonic()

    def job(year, seed):
        options = job_args(args.out, year, seed, paths, args.stage)
        if args.resume and (options.out / "evaluation.json").exists():
            verify_completed(options, protocol, expected[year])
            return year, seed
        log = logs / f"year_{year}_seed_{seed:02d}.log"
        attempt = 1
        while args.resume and log.exists():
            log = logs / f"year_{year}_seed_{seed:02d}_attempt_{attempt}.log"
            attempt += 1
        with log.open("x") as stream:
            subprocess.run(command(options, resume=args.resume and options.out.exists()), check=True, env=env,
                           stdout=stream, stderr=subprocess.STDOUT)
        verify_completed(options, protocol, expected[year])
        return year, seed

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(job, year, seed) for year, seed in jobs]
        try:
            for completed, future in enumerate(as_completed(futures), 1):
                year, seed = future.result()
                print(json.dumps(dict(stage=args.stage, completed=completed, total=len(jobs), year=year,
                                      seed=seed, elapsed_s=round(time.monotonic()-started, 1))), flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    configuration(args.out, args.stage)
    artifact_hashes = {}
    for year, seed in jobs:
        options = job_args(args.out, year, seed, paths, args.stage)
        for file in [options.out / "evaluation.json", options.out / "identity.json",
                     options.out / models.stage_arm(args.stage) / "receipt.json"]:
            artifact_hashes[str(file.relative_to(args.out))] = models.sha(file)
    complete = args.out / models.marker_name(args.stage)
    result = dict(stage=args.stage, jobs=len(jobs), fits=len(jobs), elapsed_s=time.monotonic()-started,
                  arm=models.stage_arm(args.stage), protocol_sha256=models.sha(args.out / models.protocol_name(args.stage)),
                  can_adopt=False, hashes=artifact_hashes)
    if complete.exists():
        if not args.resume:
            raise ValueError("model stage already complete")
        previous = json.loads(complete.read_text())
        if any(previous.get(key) != result[key] for key in ("stage", "arm", "jobs", "fits", "protocol_sha256", "can_adopt", "hashes")):
            raise ValueError("model stage completion identity differs")
    else:
        models.save_json(complete, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
