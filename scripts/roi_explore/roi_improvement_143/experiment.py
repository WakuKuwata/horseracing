"""Run frozen 143 research stages with at most two single-thread subprocesses."""
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
_spec = importlib.util.spec_from_file_location("roi143_models", HERE / "models.py")
models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(models)


def configuration(out: Path, stage: str) -> tuple[dict, dict]:
    protocol_path = out / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    paths = {key: models.ROOT / protocol[key] for key in ("rows", "additions", "weights", "comparison_marker")}
    models.verify_protocol(protocol_path, paths["rows"], paths["additions"], paths["weights"])
    if stage == "weighted":
        models.verify_comparison_marker(paths["comparison_marker"], protocol_path)
    return protocol, paths


def job_args(out: Path, stage: str, year: int, seed: int, paths: dict) -> SimpleNamespace:
    weighted = stage == "weighted"
    return SimpleNamespace(protocol=out / "protocol.json", rows=paths["rows"], additions=paths["additions"],
                           weights=paths["weights"] if weighted else None,
                           comparison_marker=paths["comparison_marker"] if weighted else None,
                           year=year, seed=seed, arms=["weighted_baseline"] if weighted else ["speed", "pace"],
                           out=out / ("weighted_models" if weighted else "models") / f"seed_{seed:02d}" / f"year_{year}")


def command(args, resume=False) -> list[str]:
    cmd = [sys.executable, str(HERE / "models.py"), "evaluate", "--protocol", str(args.protocol),
           "--rows", str(args.rows), "--additions", str(args.additions), "--out", str(args.out),
           "--year", str(args.year), "--seed", str(args.seed), "--arms", *args.arms]
    if args.weights:
        cmd += ["--weights", str(args.weights), "--comparison-marker", str(args.comparison_marker)]
    if resume:
        cmd += ["--resume"]
    return cmd


def verify_completed(args, protocol: dict) -> None:
    expected = models.run_identity(args, protocol)
    actual = json.loads((args.out / "evaluation.json").read_text())
    if models.canonical(actual["identity"]) != models.canonical(expected) or set(actual["arms"]) != set(args.arms):
        raise ValueError("completed evaluation identity differs")
    if models.canonical(json.loads((args.out / "identity.json").read_text())) != models.canonical(expected):
        raise ValueError("completed directory identity differs")
    for arm in args.arms:
        receipt = json.loads((args.out / arm / "receipt.json").read_text())
        if models.canonical(receipt["recipe"]["identity"]) != models.canonical(expected):
            raise ValueError("completed fit identity differs")
        recipe = receipt["recipe"]
        expected_params = dict(models.base.BASE_PARAMS, seed=args.seed, objective="binary", metric="binary_logloss")
        expected_features = models.feature_contract(arm, protocol["bundles"])[2]
        for key, value in dict(arm=arm, seed=args.seed, rounds=models.base.FIXED_ROUNDS,
                               params=expected_params, features=expected_features).items():
            if models.canonical(recipe.get(key)) != models.canonical(value):
                raise ValueError(f"completed fit recipe differs: {key}")
        expected_record = dict(metrics=receipt["metrics"], elapsed_s=receipt["elapsed_s"], actual_iterations=receipt["actual_iterations"])
        if models.canonical(actual["arms"][arm]) != models.canonical(expected_record):
            raise ValueError("completed evaluation/receipt differs")
        for name, digest in receipt["hashes"].items():
            if models.sha(args.out / arm / name) != digest:
                raise ValueError("completed model artifact changed")


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("stage", choices=["features", "weighted"])
    ap.add_argument("--out", type=Path, default=models.ROOT / "artifacts/143-existing-feature-roi")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    protocol, paths = configuration(args.out, args.stage)
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    jobs = [(year, seed) for year in protocol["window"]["model_outer"] for seed in protocol["seeds"]]
    logs = args.out / f"{args.stage}_logs"
    logs.mkdir(exist_ok=True)
    started = time.monotonic()

    def job(year, seed):
        options = job_args(args.out, args.stage, year, seed, paths)
        if args.resume and (options.out / "evaluation.json").exists():
            verify_completed(options, protocol)
            return year, seed
        log = logs / f"year_{year}_seed_{seed:02d}.log"
        attempt = 1
        while args.resume and log.exists():
            log = logs / f"year_{year}_seed_{seed:02d}_attempt_{attempt}.log"
            attempt += 1
        with log.open("x") as stream:
            subprocess.run(command(options, resume=args.resume and options.out.exists()), check=True, env=env,
                           stdout=stream, stderr=subprocess.STDOUT)
        verify_completed(options, protocol)
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
    complete = args.out / f"{args.stage}_models_complete.json"
    result = dict(stage=args.stage, jobs=len(jobs), fits=len(jobs)*(1 if args.stage == "weighted" else 2),
                  elapsed_s=time.monotonic()-started, protocol_sha256=models.sha(args.out / "protocol.json"), can_adopt=False)
    if complete.exists():
        if not args.resume:
            raise ValueError("model stage already complete")
        previous = json.loads(complete.read_text())
        if any(previous.get(key) != result[key] for key in ("stage", "jobs", "fits", "protocol_sha256", "can_adopt")):
            raise ValueError("model stage completion identity differs")
    else:
        models.save_json(complete, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
