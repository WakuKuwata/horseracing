"""Precompute independent stage caches; the frozen driver remains the judge."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
from pathlib import Path
import pickle
import resource
import subprocess
import sys

import ability_observation as p
from horseracing_eval.splits import expanding_folds

STAGE = "full"
AREA = p.WORK / "prefill-full"
MANIFEST = AREA / "manifest.json"


def verify():
    cfg = p.load_config()
    meta = json.loads((p.WORK / "snapshot.json").read_text())
    frozen = json.loads((p.WORK / "run-freeze.json").read_text())
    observed = {"snapshot_sha256": p.digest(p.WORK / "snapshot.pkl"),
        "snapshot_metadata_sha256": p.digest(p.WORK / "snapshot.json"),
        "config_hash": p.gate_config_hash(cfg), "source_hash": p.source_hash(),
        "model_sha256": p.digest(p.MODEL / "model.txt")}
    if any(frozen[k] != v for k, v in observed.items()):
        raise ValueError("Original run freeze mismatch")
    return cfg, meta, frozen


def inputs(cfg):
    with (p.WORK / "snapshot.pkl").open("rb") as f:
        matrix, all_races = pickle.load(f)
    window = cfg["eval_window"] if STAGE == "full" else cfg[STAGE]["eval_window"]
    start = dt.date.fromisoformat(window["from"])
    end = dt.date.fromisoformat(window["to"])
    races = [r for r in all_races if r.context.race_date <= end]
    folds = {f.valid_year: f for f in expanding_folds(races, start.year, valid_from=start)}
    return matrix, races, folds


def factory(cfg, meta, frozen, matrix, races, arm):
    return p.CachedFactory(cfg=cfg, matrix=matrix, races=races,
        identity=[meta["snapshot_sha256"], frozen["source_hash"], STAGE],
        drops=arm["drop_features"], label=arm["id"])


def key_for(meta, frozen, recipe_hash, fold):
    train = [er.context for er in fold.train]
    assert max(r.race_date.year for r in train) + 1 == fold.valid_year
    train_hash = p.stable_hash([(r.race_id, str(r.race_date),
        [h.horse_id for h in r.started_horses]) for r in train])
    return p.stable_hash([[meta["snapshot_sha256"], frozen["source_hash"], STAGE],
        recipe_hash, train_hash, fold.valid_year])


def make_manifest():
    cfg, meta, frozen = verify()
    screen = {c["id"]: p.SPEC / "evidence" / f"screen-{c['id']}.json" for c in cfg["candidates"]}
    arms = [{"id": "baseline", "drop_features": cfg["baseline_drop_features"]}] + [c for c in cfg["candidates"]
        if STAGE == "screen" or json.loads(screen[c["id"]].read_text())["progression"] == "ADVANCE"]
    if STAGE == "screen":
        screen = {}
    if len(arms) == 1:
        raise ValueError("No advancing candidate")
    matrix, races, folds = inputs(cfg)
    recipes = {a["id"]: factory(cfg, meta, frozen, matrix, races, a).recipe_hash for a in arms}
    jobs = {}
    for year in sorted(folds, reverse=True):
        for arm in arms:
            key = key_for(meta, frozen, recipes[arm["id"]], folds[year])
            job = {"key": key, "year": year, "arm": arm}
            if key in jobs and jobs[key] != job:
                raise ValueError("Conflicting tasks share a cache key")
            jobs[key] = job
    p.write_json(MANIFEST, {"orchestrator_sha256": p.digest(__file__),
        "baseline_equivalence_sha256": (p.digest(p.WORK / "baseline-equivalence.json")
            if STAGE == "full" and (p.WORK / "baseline-equivalence.json").exists() else None),
        "run_freeze_sha256": p.digest(p.WORK / "run-freeze.json"),
        "screen_sha256": {k: p.digest(v) for k, v in screen.items()},
        "jobs": list(jobs.values()), "model_threads": 1, "stage": STAGE,
        "note": "Execution-order change only. Original stage driver must run after all workers exit."})
    p.write_json(AREA / "manifest-freeze.json", {"manifest_sha256": p.digest(MANIFEST)})
    print(f"MANIFEST jobs={len(jobs)}", flush=True)


def manifest():
    expected = json.loads((AREA / "manifest-freeze.json").read_text())["manifest_sha256"]
    if p.digest(MANIFEST) != expected:
        raise ValueError("Prefill job manifest changed")
    m = json.loads(MANIFEST.read_text())
    if m["stage"] != STAGE:
        raise ValueError("Prefill stage changed")
    if m.get("baseline_equivalence_sha256") is not None:
        certificate = p.WORK / "baseline-equivalence.json"
        if p.digest(certificate) != m["baseline_equivalence_sha256"]:
            raise ValueError("Baseline equivalence certificate changed")
        if json.loads(certificate.read_text())["method_sha256"] != p.digest(p.ROOT / "scripts/ability_observation_reuse.py"):
            raise ValueError("Baseline equivalence method changed")
    if m["orchestrator_sha256"] != p.digest(__file__) or m["run_freeze_sha256"] != p.digest(p.WORK / "run-freeze.json"):
        raise ValueError("Prefill execution freeze changed")
    for label, sha in m["screen_sha256"].items():
        if sha != p.digest(p.SPEC / "evidence" / f"screen-{label}.json"):
            raise ValueError("Screen evidence changed")
    return m


def receipt_path(key):
    return AREA / f"{key}.json"


def completed(job):
    path = receipt_path(job["key"])
    if not path.exists():
        return False
    result = json.loads(path.read_text())
    if result["job"] != job or result["cache_sha256"] != p.digest(p.WORK / "cache" / f"{job['key']}.pkl"):
        raise ValueError("Completed cache or receipt changed")
    if result.get("imported"):
        certificate = p.WORK / "baseline-equivalence.json"
        if result["import_certificate_sha256"] != p.digest(certificate):
            raise ValueError("Imported cache certificate changed")
        cert = json.loads(certificate.read_text())
        source = next(row for row in cert["records"] if row["new_key"] == job["key"])
        if job["arm"]["id"] != "baseline" or p.digest(Path(source["old_path"])) != source["old_sha256"]:
            raise ValueError("Imported baseline source changed")
    return True


def worker(key):
    m = manifest()
    cfg, meta, frozen = verify()
    job = next(j for j in m["jobs"] if j["key"] == key)
    if completed(job):
        return
    cache_path = p.WORK / "cache" / f"{key}.pkl"
    def quarantine():
        if cache_path.exists():
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            cache_path.rename(AREA / f"unverified-{key}-{stamp}.pkl")
    # An interrupted/unverified worker must not silently bless its previous cache on retry.
    quarantine()
    matrix, races, folds = inputs(cfg)
    f = factory(cfg, meta, frozen, matrix, races, job["arm"])
    fold = folds[job["year"]]
    if key != key_for(meta, frozen, f.recipe_hash, fold):
        raise ValueError("Cache key does not match original factory inputs")
    try:
        predictor = f.fit([er.context for er in fold.train], num_threads=1)
        # The original factory validates metadata and prediction populations on cache replay.
        for er in fold.valid:
            predictor.predict_race(er.context)
        verify()
        manifest()
    except BaseException:
        quarantine()
        raise
    p.write_json(receipt_path(key), {"job": job,
        "cache_sha256": p.digest(p.WORK / "cache" / f"{key}.pkl"),
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
        "model_threads": 1})


def launch(job, run_id):
    log = AREA / f"{run_id}-{job['key']}.log"
    print(f"START {job['arm']['id']} {job['year']}", flush=True)
    with log.open("x") as f:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", job["key"], "--stage", STAGE],
            cwd=p.ROOT, stdout=f, stderr=subprocess.STDOUT, check=True)
    if not completed(job):
        raise ValueError("Worker exited without a valid completion receipt")
    print(f"DONE {job['arm']['id']} {job['year']}", flush=True)


def run(workers, probe):
    verify()
    m = manifest()
    jobs = [j for j in m["jobs"] if not completed(j)]
    if probe:
        jobs = jobs[:1]  # Largest/latest baseline, to measure peak memory before parallel work.
        workers = 1
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    lock = AREA / "running.lock"
    lock.mkdir()  # one orchestrator at a time; never run original full concurrently
    try:
        p.write_json(AREA / f"{run_id}-run.json", {"workers": workers, "probe": probe,
            "jobs": jobs, "manifest_sha256": p.digest(MANIFEST)})
        # Threads launch isolated exec processes only; no model or OOF patch runs in a thread.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(launch, j, run_id) for j in jobs]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        verify()
        manifest()
        print(f"PREFILL COMPLETE jobs={len(jobs)}; run the original stage driver after all jobs are complete", flush=True)
    finally:
        lock.rmdir()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["screen", "full"], default="full")
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--worker")
    ap.add_argument("--workers", type=int, choices=[1, 2], default=1)
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    STAGE = args.stage
    AREA = p.WORK / f"prefill-{STAGE}"
    MANIFEST = AREA / "manifest.json"
    if args.manifest:
        make_manifest()
    elif args.worker:
        worker(args.worker)
    else:
        run(args.workers, args.probe)
