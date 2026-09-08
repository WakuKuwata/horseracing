"""Independent-exec smoke prediction parity; no model accuracy measurements."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import pickle
import subprocess
import sys

import feature_pruning as p
import feature_pruning_prefill as prefill
from horseracing_eval.splits import expanding_folds

AREA = p.WORK / "parallel-smoke-parity"
OUT = p.SPEC / "evidence/parallel-smoke-parity.json"


def worker(arm_id):
    cfg, meta, frozen = prefill.verify()
    start = dt.date.fromisoformat(cfg["smoke"]["eval_window"]["from"])
    end = dt.date.fromisoformat(cfg["smoke"]["eval_window"]["to"])
    with (p.WORK / "snapshot.pkl").open("rb") as stream:
        matrix, all_races = pickle.load(stream)
    races = [er for er in all_races if er.context.race_date <= end]
    folds = list(expanding_folds(races, start.year, valid_from=start))
    if len(folds) != 1:
        raise ValueError("Parity expects one official smoke fold")
    fold = folds[0]
    train = [er.context for er in fold.train]
    drops = [] if arm_id == "baseline" else next(
        c["drop_features"] for c in cfg["candidates"] if c["id"] == arm_id)
    original_identity = [meta["snapshot_sha256"], frozen["source_hash"], "smoke"]
    script_sha = p.digest(__file__)
    identity = [*original_identity, "independent_exec_parity", script_sha]
    factory = p.CachedFactory(cfg, matrix, races, identity, drops=drops,
                              smoke=True, label=f"parity-{arm_id}")
    train_hash = p.stable_hash([(rc.race_id, str(rc.race_date),
        [h.horse_id for h in rc.started_horses]) for rc in train])
    original_key = p.stable_hash([original_identity, factory.recipe_hash,
                                  train_hash, fold.valid_year])
    new_key = p.stable_hash([identity, factory.recipe_hash, train_hash, fold.valid_year])
    original_path = p.WORK / "cache" / f"{original_key}.pkl"
    new_path = p.WORK / "cache" / f"{new_key}.pkl"
    if new_path.exists():
        raise FileExistsError("Parity must refit; a parity cache already exists")
    original_sha = p.digest(original_path)
    with original_path.open("rb") as stream:
        original = pickle.load(stream)
    if original["key"] != original_key or original["recipe_meta"] != factory.recipe_meta:
        raise ValueError("Original smoke recipe/key mismatch")
    predictor = factory.fit(train, num_threads=1)
    new_predictions = {er.context.race_id: predictor.predict_race(er.context)
                       for er in fold.valid}
    if set(new_predictions) != set(original["predictions"]):
        raise ValueError("Original/new smoke race populations differ")
    mismatch = sum(new_predictions[rid] != original["predictions"][rid]
                   for rid in new_predictions)
    prefill.verify()
    if p.digest(original_path) != original_sha or p.digest(__file__) != script_sha:
        raise ValueError("Original cache or parity code changed during fit")
    result = {"arm": arm_id, "exact_prediction_parity": mismatch == 0,
        "mismatched_races": mismatch, "n_races": len(new_predictions),
        "n_horses": sum(map(len, new_predictions.values())),
        "recipe_hash": factory.recipe_hash, "train_hash": train_hash,
        "original_cache": str(original_path), "original_cache_sha256": original_sha,
        "independent_cache": str(new_path), "independent_cache_sha256": p.digest(new_path),
        "different_cache_identity": original_key != new_key,
        "script_sha256": script_sha}
    p.write_json(AREA / f"{arm_id}.json", result)


def main():
    if OUT.exists():
        raise FileExistsError("Parity evidence already exists")
    cfg, meta, frozen = prefill.verify()
    AREA.mkdir(parents=True, exist_ok=True)
    script_sha = p.digest(__file__)
    arms = ["baseline", "human_absolute"]
    results = []
    for arm in arms:
        with (AREA / f"{arm}.log").open("x") as log:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", arm],
                           cwd=p.ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        results.append(json.loads((AREA / f"{arm}.json").read_text()))
    prefill.verify()
    if p.digest(__file__) != script_sha:
        raise ValueError("Parity script changed during execution")
    result = {"exact_prediction_parity": all(r["exact_prediction_parity"] for r in results),
        "accuracy_measured": False, "can_adopt": False,
        "method": "Each arm refitted in its own exec process with official smoke fold, recipe, snapshot and one training thread; compared every win/top2/top3 Prediction value exactly against the original smoke cache. Dedicated cache identities prevent reuse of original predictions.",
        "smoke_window": cfg["smoke"]["eval_window"],
        "snapshot_sha256": meta["snapshot_sha256"], "source_hash": frozen["source_hash"],
        "config_hash": p.gate_config_hash(cfg), "script_path": str(Path(__file__).resolve()),
        "script_sha256": script_sha, "method_code": Path(__file__).read_text(), "arms": results}
    p.write_json(OUT, result)
    print(json.dumps({"exact_prediction_parity": result["exact_prediction_parity"],
                      "arms": [{k: r[k] for k in ("arm", "exact_prediction_parity", "n_races", "n_horses")}
                               for r in results]}))
    if not result["exact_prediction_parity"]:
        raise SystemExit("Independent-exec smoke predictions differ")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=["baseline", "human_absolute"])
    args = parser.parse_args()
    worker(args.worker) if args.worker else main()
