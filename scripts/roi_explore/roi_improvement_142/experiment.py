"""Run the frozen 142 model experiments, with at most two single-thread workers."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as research
import models


def command(out: Path, year: int | None = None, seed: int = 1) -> list[str]:
    cmd = [sys.executable, str(HERE / "models.py"), "screen" if year is None else "evaluate",
           "--protocol", str(out / "protocol.json"), "--seed", str(seed)]
    if year is None:
        cmd += ["--out", str(out / "screen_seed1")]
    else:
        cmd += ["--year", str(year), "--out", str(out / "models" / f"seed_{seed:02d}" / f"year_{year}"),
                "--selection", str(out / "screen_seed1/selection.json")]
    return cmd


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("stage", choices=["screen", "evaluate"])
    ap.add_argument("--out", type=Path, default=research.DEFAULT_OUT)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    protocol = research.verify(args.out)
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    if args.stage == "screen":
        cmd = command(args.out)
        if args.resume:
            cmd.append("--resume")
        subprocess.run(cmd, check=True, env=env)
    else:
        jobs = [(year, seed) for year in protocol["window"]["model_outer"] for seed in protocol["seeds"]]
        logs = args.out / "logs"
        logs.mkdir(exist_ok=True)
        start = time.monotonic()
        def job(year, seed):
            directory = args.out / "models" / f"seed_{seed:02d}" / f"year_{year}"
            if args.resume and (directory / "evaluation.json").exists():
                expected = models.run_identity(SimpleNamespace(command="evaluate", rows=research.ROWS,
                    seed=seed, year=year, arms=["baseline", "earlystop", "softmax"], rounds=10,
                    protocol=args.out / "protocol.json", selection=args.out / "screen_seed1/selection.json"))
                actual = json.loads((directory / "evaluation.json").read_text())
                if models.canonical(expected) != models.canonical(actual["identity"]):
                    raise ValueError("Completed evaluation identity differs")
                for arm in ["baseline", "earlystop_selector", "earlystop", "softmax"]:
                    receipt = json.loads((directory / arm / "receipt.json").read_text())
                    if models.canonical(receipt["recipe"]["identity"]) != models.canonical(expected):
                        raise ValueError("Completed fit identity differs")
                    for filename, digest in receipt["hashes"].items():
                        if research.sha256(directory / arm / filename) != digest:
                            raise ValueError("Completed model artifact changed")
                return year, seed
            log = logs / f"year_{year}_seed_{seed:02d}.log"
            if args.resume:
                attempt = 1
                while log.exists():
                    log = logs / f"year_{year}_seed_{seed:02d}_attempt_{attempt}.log"
                    attempt += 1
            cmd = command(args.out, year, seed)
            if args.resume and directory.exists():
                cmd.append("--resume")
            with log.open("x") as stream:
                subprocess.run(cmd, check=True, env=env, stdout=stream, stderr=subprocess.STDOUT)
            return year, seed
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = {pool.submit(job, year, seed): (year, seed) for year, seed in jobs}
            try:
                for completed, future in enumerate(as_completed(pending), 1):
                    year, seed = future.result()
                    print(json.dumps({"completed": completed, "total": len(jobs), "year": year,
                                      "seed": seed, "elapsed_s": round(time.monotonic()-start, 1)}), flush=True)
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
        research.verify(args.out)
        complete = args.out / "models_complete.json"
        if complete.exists():
            raise ValueError("Model comparison already complete and verified")
        research.write_new(complete, {
            "jobs": len(jobs), "elapsed_s": time.monotonic()-start,
            "protocol_sha256": research.sha256(args.out / "protocol.json"), "can_adopt": False})


if __name__ == "__main__":
    main()
