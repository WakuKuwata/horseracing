"""Run the frozen 153 market drop3 arm with at most two single-thread workers."""

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
_spec = importlib.util.spec_from_file_location("roi153_models", HERE / "models.py")
models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(models)


def configuration(out: Path) -> tuple[dict, dict]:
    protocol_path = out / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    paths = {
        key: models.ROOT / protocol[key] for key in ("rows", "additions", "weights")
    }
    models.verify_protocol(
        protocol_path, paths["rows"], paths["additions"], paths["weights"]
    )
    return protocol, paths


def job_args(out: Path, year: int, seed: int, paths: dict) -> SimpleNamespace:
    return SimpleNamespace(
        protocol=out / "protocol.json",
        rows=paths["rows"],
        additions=paths["additions"],
        weights=paths["weights"],
        year=year,
        seed=seed,
        arms=list(models.ARMS),
        out=out / "models" / f"seed_{seed:02d}" / f"year_{year}",
    )


def command(args, resume=False) -> list[str]:
    result = [
        sys.executable,
        str(HERE / "models.py"),
        "evaluate",
        "--protocol",
        str(args.protocol),
        "--rows",
        str(args.rows),
        "--additions",
        str(args.additions),
        "--weights",
        str(args.weights),
        "--out",
        str(args.out),
        "--year",
        str(args.year),
        "--seed",
        str(args.seed),
        "--arms",
        *args.arms,
    ]
    return result + (["--resume"] if resume else [])


def expected_recipes(paths: dict, years, bundles: dict | None = None) -> dict:
    """Store only eight small metadata dictionaries, never per-year arrays."""
    data = models.base.read_rows(paths["rows"])
    oof = models.pd.read_parquet(paths["weights"])
    result = {}
    for year in years:
        train, target = data.loc[data.year < year], data.loc[data.year == year]
        if train.empty or target.empty:
            raise ValueError("missing expected annual split")
        common = dict(
            train_start=str(train.race_date.min()),
            train_end=str(train.race_date.max()),
            target_start=str(target.race_date.min()),
            target_end=str(target.race_date.max()),
            train_rows=len(train),
            target_rows=len(target),
        )
        result[year] = {}
        for arm in models.ARMS:
            weights, info = models.training_weights(train, oof)
            result[year][arm] = dict(common, weights=info)
            del weights
    return result


def verify_completed(args, protocol: dict, expected: dict | None = None) -> None:
    identity = models.run_identity(args, protocol)
    actual = json.loads((args.out / "evaluation.json").read_text())
    if models.canonical(actual["identity"]) != models.canonical(identity) or set(
        actual["arms"]
    ) != set(args.arms):
        raise ValueError("completed evaluation identity differs")
    if actual.get("year") != args.year or actual.get("seed") != args.seed:
        raise ValueError("completed evaluation year/seed differs")
    if any(
        actual.get(key) is not False
        for key in ("can_adopt", "untouched_holdout", "production_changed")
    ):
        raise ValueError("completed evaluation research constraints differ")
    if models.canonical(
        json.loads((args.out / "identity.json").read_text())
    ) != models.canonical(identity):
        raise ValueError("completed directory identity differs")
    if expected is None:
        expected = expected_recipes(
            {"rows": args.rows, "additions": args.additions, "weights": args.weights},
            [args.year],
            protocol["bundles"],
        )[args.year]
    for arm in args.arms:
        receipt = json.loads((args.out / arm / "receipt.json").read_text())
        params = dict(
            models.base.BASE_PARAMS,
            seed=args.seed,
            objective="binary",
            metric="binary_logloss",
        )
        recipe = dict(
            arm=models.KERNEL_ARM,
            seed=args.seed,
            rounds=models.base.FIXED_ROUNDS,
            params=params,
            features=models.FEATURES,
            identity=identity,
            **expected[arm],
        )
        if models.canonical(receipt["recipe"]) != models.canonical(recipe):
            raise ValueError("completed fit recipe differs")
        if (
            receipt.get("actual_iterations") != models.base.FIXED_ROUNDS
            or receipt.get("roundtrip_max_abs_raw_difference", 1.0) > 1e-12
        ):
            raise ValueError("completed fit iterations/roundtrip differs")
        record = dict(
            metrics=receipt["metrics"],
            elapsed_s=receipt["elapsed_s"],
            actual_iterations=receipt["actual_iterations"],
        )
        if models.canonical(actual["arms"][arm]) != models.canonical(record):
            raise ValueError("completed evaluation/receipt differs")
        if set(receipt["hashes"]) != {
            "model.txt",
            "predictions.parquet",
            "feature_spec.json",
        }:
            raise ValueError("completed artifact inventory differs")
        for name, digest in receipt["hashes"].items():
            if models.sha(args.out / arm / name) != digest:
                raise ValueError("completed model artifact changed")


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=models.ROOT / "artifacts/153-market-three-column-ablation",
    )
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    protocol, paths = configuration(args.out)
    expected = expected_recipes(
        paths, protocol["window"]["model_outer"], protocol["bundles"]
    )
    env = dict(
        os.environ,
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        VECLIB_MAXIMUM_THREADS="1",
    )
    jobs = [
        (year, seed)
        for year in protocol["window"]["model_outer"]
        for seed in protocol["seeds"]
    ]
    logs = args.out / "model_logs"
    logs.mkdir(exist_ok=True)
    started = time.monotonic()

    def job(year, seed):
        options = job_args(args.out, year, seed, paths)
        if args.resume and (options.out / "evaluation.json").exists():
            verify_completed(options, protocol, expected[year])
            return year, seed
        log = logs / f"year_{year}_seed_{seed:02d}.log"
        attempt = 1
        while args.resume and log.exists():
            log = logs / f"year_{year}_seed_{seed:02d}_attempt_{attempt}.log"
            attempt += 1
        with log.open("x") as stream:
            subprocess.run(
                command(options, resume=args.resume and options.out.exists()),
                check=True,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        verify_completed(options, protocol, expected[year])
        return year, seed

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(job, year, seed) for year, seed in jobs]
        try:
            for completed, future in enumerate(as_completed(futures), 1):
                year, seed = future.result()
                print(
                    json.dumps(
                        dict(
                            stage="models",
                            completed=completed,
                            total=len(jobs),
                            year=year,
                            seed=seed,
                            elapsed_s=round(time.monotonic() - started, 1),
                        )
                    ),
                    flush=True,
                )
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    configuration(args.out)
    artifact_hashes = {}
    for year, seed in jobs:
        options = job_args(args.out, year, seed, paths)
        for file in [options.out / "evaluation.json", options.out / "identity.json"] + [
            options.out / arm / "receipt.json" for arm in models.ARMS
        ]:
            artifact_hashes[str(file.relative_to(args.out))] = models.sha(file)
    complete = args.out / "models_complete.json"
    result = dict(
        stage="models",
        jobs=len(jobs),
        fits=len(models.ARMS) * len(jobs),
        elapsed_s=time.monotonic() - started,
        protocol_sha256=models.sha(args.out / "protocol.json"),
        can_adopt=False,
        hashes=artifact_hashes,
    )
    if complete.exists():
        if not args.resume:
            raise ValueError("model stage already complete")
        previous = json.loads(complete.read_text())
        if any(
            previous.get(key) != result[key]
            for key in (
                "stage",
                "jobs",
                "fits",
                "protocol_sha256",
                "can_adopt",
                "hashes",
            )
        ):
            raise ValueError("model stage completion identity differs")
    else:
        models.save_json(complete, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
