"""132 frozen, DB-free style-Unknown audit and paired research refits.

prepare never trains. run/fit are explicit; summarize refuses missing caches.
Historical data end at the sealed 111 snapshot (2026-08-23), below 2026-09-06.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import datetime as dt
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import pickle
import sys
import time
from unittest.mock import patch

import numpy as np
import pandas as pd

import pace_style_unknown_features as features
from ability_observation_features import OBSERVATION_COLUMNS
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import population_masks
from horseracing_eval.hashing import stable_hash
from horseracing_eval.paired import DEFAULT_BAND_EDGES, _score_arm
from horseracing_eval.predictor import Prediction
from horseracing_eval.splits import expanding_folds
from horseracing_features.weight_mask import MaskSpec
from horseracing_training.calib_split import OofCalibratedPredictor
from horseracing_training.recipe import ModelRecipe

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs/132-accuracy-first-batch"
WORK = ROOT / "artifacts/132-accuracy-first-batch/style"
INPUT = ROOT / "artifacts/111-ability-observation"
DATA_END = dt.date(2026, 8, 23)
YEARS = (2024, 2026)
ARMS = ("baseline", "candidate")
REGIMES = {"full": None, "preweight": MaskSpec(rate=1., seed=20260810)}
EXPECTED_STYLE = {"years": list(YEARS), "baseline": "raw138", "seed": 42,
                  "n_estimators": 900, "n_oof_blocks": 8, "te_smoothing": 10,
                  "weight_mask_rate": .5, "regimes": list(REGIMES)}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                         allow_nan=False, default=str) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as fh:
        fh.write(content)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as fh:
        pickle.dump(value, fh, protocol=5)


def load(path):
    with Path(path).open("rb") as fh:
        return pickle.load(fh)


def config():
    cfg = read_json(SPEC / "experiment.json")
    if (cfg["cutoff"] != str(features.CUTOFF) or cfg["source_end"] != str(DATA_END)
            or cfg["style"] != EXPECTED_STYLE or cfg["can_adopt"] is not False
            or cfg["eligible_for_verdict"] is not False):
        raise ValueError("132 registered style experiment changed")
    return {"cutoff": cfg["cutoff"], "source_end": cfg["source_end"],
            "style": cfg["style"], "bootstrap": cfg["bootstrap"],
            "semantics_id": features.SEMANTICS_ID, "known_styles": sorted(features.KNOWN_STYLES),
            "changed_columns": features.OVERLAY_COLUMNS, "can_adopt": False,
            "eligible_for_verdict": False, "material_change_tolerance": 1e-12}


def source_hash():
    paths = [Path(__file__).resolve(), Path(features.__file__),
             ROOT / "scripts/ability_observation_features.py"]
    for package in ("db", "features", "training", "eval", "probability"):
        paths.extend((ROOT / package / "src").rglob("*.py"))
    return stable_hash({str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))})


def runtime():
    return {"python": sys.version, "packages": {name: importlib.metadata.version(name) for name in
        ("numpy", "pandas", "lightgbm", "scikit-learn", "scipy", "SQLAlchemy", "joblib", "threadpoolctl")}}


def input_manifest():
    # Metadata is inspected BEFORE deserializing any real race data.
    meta = read_json(INPUT / "snapshot.json")
    if meta["data_through"] != str(DATA_END) or DATA_END > features.CUTOFF:
        raise ValueError("Source metadata exceeds registered race-date boundary")
    files = {"snapshot.pkl": meta["snapshot_sha256"],
             "source-frames.pkl": meta["source_frames_sha256"],
             "snapshot.json": digest(INPUT / "snapshot.json")}
    for name, sha in files.items():
        if digest(INPUT / name) != sha:
            raise ValueError(f"Sealed source changed: {name}")
    return {"data_through": meta["data_through"], "files": files,
            "capture_note": "Race-date cutoff, not an attestation of original publication-time availability"}


def validate_matrix_races(matrix, races):
    dates = pd.to_datetime(matrix.frame.race_date, errors="raise")
    if dates.isna().any() or dates.max() > pd.Timestamp(DATA_END):
        raise ValueError("Matrix includes missing or forbidden dates")
    if matrix.frame.duplicated(features.KEYS).any():
        raise ValueError("Duplicate matrix appearances")
    ids = set(matrix.frame.race_id)
    for er in races:
        if er.context.race_date > DATA_END or er.context.race_id not in ids or er.n_result_rows is None:
            raise ValueError("EvalRace outside snapshot or missing result completeness")
    if not races or len({r.context.race_id for r in races}) != len(races):
        raise ValueError("Empty or duplicate evaluation races")


def recipe(smoke=False):
    return ModelRecipe(objective="pl_topk", calibration="none", calib_frac=0., seed=42,
        drop_features=tuple(OBSERVATION_COLUMNS), params=(("n_estimators", 5 if smoke else 900),),
        weight_mask_rate=.5, weight_mask_seed=20260810,
        target_encode_cols=("jockey_id", "trainer_id"), te_smoothing=10.)


def recipe_meta(smoke=False):
    # JSON round-trips tuples to lists; compare the canonical stored representation.
    return json.loads(json.dumps(recipe(smoke).meta()))


def expected_columns(matrix):
    cols = [c for c in matrix.feature_cols if c not in OBSERVATION_COLUMNS]
    if len(cols) != 138 or not set(features.OVERLAY_COLUMNS) <= set(cols):
        raise ValueError("Expected raw138 including all eight overlay columns")
    return cols


def train_identity(train):
    return stable_hash([(r.context.race_id, str(r.context.race_date),
                         [h.horse_id for h in r.context.started_horses]) for r in train])


def fold_identity(fold):
    return {"n_train_races": len(fold.train), "n_valid_races": len(fold.valid),
            "train_hash": train_identity(fold.train),
            "valid_ids_sha256": stable_hash([r.context.race_id for r in fold.valid])}


def folds_for(races):
    result = {f.valid_year: f for f in expanding_folds(races, YEARS[0]) if f.valid_year in YEARS}
    if set(result) != set(YEARS):
        raise ValueError("Registered evaluation years missing")
    return result


def validate_predictions(predictions, valid):
    if set(predictions) != {r.context.race_id for r in valid}:
        raise ValueError("Prediction race coverage differs")
    for er in valid:
        ids = [h.horse_id for h in er.context.started_horses]
        p = predictions[er.context.race_id]
        if list(p) != ids or not ids:
            raise ValueError("Prediction started set/order differs")
        a = np.asarray(list(p.values()), dtype=float)
        if (a.shape != (len(ids), 3) or not np.isfinite(a).all()
                or (a < -1e-12).any() or (a > 1 + 1e-12).any()
                or (np.diff(a, axis=1) < -1e-10).any()
                or not np.allclose(a.sum(axis=0), [min(k, len(ids)) for k in (1, 2, 3)], atol=1e-8, rtol=0)):
            raise ValueError("Prediction probability constraints failed")


def certify_baseline130(matrix, folds, inputs):
    """Native source/recipe/input/receipt proof; never claims a new refit occurred."""
    import mixture_preweight_walkforward as old
    path = old.freeze_path()
    if not path.exists():
        return {"status": "REFIT_REQUIRED", "reason": "130 freeze absent"}
    frozen = read_json(path)
    if frozen.get("snapshot_sha256") != inputs["files"]["snapshot.pkl"]:
        return {"status": "REFIT_REQUIRED", "reason": "130 uses another snapshot"}
    # Native verification binds unchanged original implementation and runtime.
    frozen = old.verify()
    if old.build.make_recipe(old.build.member_for("anchor-42")) != recipe():
        return {"status": "REFIT_REQUIRED", "reason": "130 recipe differs"}
    columns = expected_columns(matrix)
    if old.build.expected_columns(old.build.member_for("anchor-42")) != columns:
        return {"status": "REFIT_REQUIRED", "reason": "130 feature profile differs"}
    items = {}
    for year, fold in folds.items():
        if frozen["population"][str(year)] != fold_identity(fold):
            return {"status": "REFIT_REQUIRED", "reason": f"130 fold differs: {year}"}
        if not old.completed("anchor-42", year, frozen):
            return {"status": "REFIT_REQUIRED", "reason": f"130 prediction absent: {year}"}
        cache = load(old.cache_path("anchor-42", year))
        if (cache["feature_cols"] != columns or cache["member"] != "anchor-42"
                or cache["year"] != year or cache["smoke"] is not False
                or not cache["oof_info"].get("sufficient")):
            raise ValueError("130 cache identity invalid")
        for name in REGIMES:
            validate_predictions(cache["predictions"][name], fold.valid)
        items[str(year)] = {
            "cache_path": str(old.cache_path("anchor-42", year)),
            "cache_sha256": digest(old.cache_path("anchor-42", year)),
            "receipt_path": str(old.receipt_path("anchor-42", year)),
            "receipt_sha256": digest(old.receipt_path("anchor-42", year)),
            "prediction_sha256": stable_hash(cache["predictions"]),
            "historical_fit_seconds": cache["elapsed_seconds"], "fold": fold_identity(fold)}
    return {"status": "REUSE_VERIFIED", "freeze_path": str(path), "freeze_sha256": digest(path),
            "native_source_hash": frozen["source_hash"], "recipe_meta": recipe_meta(),
            "feature_columns": columns, "years": items,
            "proof": "Exact sealed matrix, native runtime/source, recipe, folds, saved probabilities and receipt SHA; no new fit or independent prediction replication claimed"}


def prepare():
    if (WORK / "run-freeze.json").exists():
        raise FileExistsError("132 style freeze exists; use verify")
    started = time.monotonic()
    cfg, source, environment = config(), source_hash(), runtime()
    inputs = input_manifest()
    frames = load(INPUT / "source-frames.pkl")
    tokens = features.token_audit(frames)
    overlay, legacy, diag = features.build_overlay(frames)
    coverage = features.coverage_report(legacy, overlay, diag)
    del frames
    gc.collect()
    matrix, races = load(INPUT / "snapshot.pkl")
    validate_matrix_races(matrix, races)
    expected_columns(matrix)
    base8 = matrix.frame[features.KEYS + features.OVERLAY_COLUMNS].set_index(features.KEYS)
    old8 = legacy.set_index(features.KEYS).reindex(base8.index)
    pd.testing.assert_frame_equal(base8, old8, check_exact=True)
    fixed = features.apply_overlay(matrix, overlay)
    rest = [c for c in matrix.frame if c not in features.OVERLAY_COLUMNS]
    pd.testing.assert_frame_equal(matrix.frame[rest], fixed.frame[rest], check_exact=True)
    changed = features.cell_changes(matrix.frame[features.OVERLAY_COLUMNS], fixed.frame[features.OVERLAY_COLUMNS])
    material = (matrix.frame[features.OVERLAY_COLUMNS].isna().ne(fixed.frame[features.OVERLAY_COLUMNS].isna())
                | (matrix.frame[features.OVERLAY_COLUMNS] - fixed.frame[features.OVERLAY_COLUMNS]).abs().gt(1e-12))
    affected_ids = sorted(set(matrix.frame.loc[material.any(axis=1), "race_id"]))
    folds = folds_for(races)
    reuse = certify_baseline130(matrix, folds, inputs) if changed.any(axis=None) else {
        "status": "NOT_NEEDED", "reason": "No changed started input"}
    audit = {"token_audit": tokens, "coverage": coverage, "data_through": str(DATA_END),
             "nonoverlay_columns_exact": True, "legacy_eight_columns_exact": True,
             "matrix_changed_rows": int(changed.any(axis=1).sum()),
             "matrix_material_changed_rows": int(material.any(axis=1).sum()),
             "matrix_rounding_only_rows": int((changed.any(axis=1) & ~material.any(axis=1)).sum()),
             "matrix_changed_cells": int(changed.sum().sum()),
             "semantics_id": features.SEMANTICS_ID, "can_adopt": False}
    dump(WORK / "overlay.pkl", {"overlay": overlay, "affected_race_ids": affected_ids})
    write_json(WORK / "coverage.json", audit)
    write_json(WORK / "baseline-reuse.json", reuse)
    if source_hash() != source or runtime() != environment or input_manifest() != inputs:
        raise ValueError("Inputs or execution code changed during preparation; partial files preserved")
    frozen = {"schema_version": 1, "config": cfg, "config_sha256": stable_hash(cfg),
              "source_hash": source, "runtime": environment, "inputs": inputs,
              "overlay_sha256": digest(WORK / "overlay.pkl"),
              "coverage_sha256": digest(WORK / "coverage.json"),
              "baseline_reuse_sha256": digest(WORK / "baseline-reuse.json"),
              "folds": {str(y): fold_identity(f) for y, f in folds.items()},
              "feature_columns": expected_columns(matrix), "recipe_meta": recipe_meta(),
              "needs_fit": bool(changed.any(axis=None)), "prepare_seconds": time.monotonic() - started,
              "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "can_adopt": False, "eligible_for_verdict": False}
    write_json(WORK / "run-freeze.json", frozen)
    readme = ("# 132 脚質Unknown研究\n\n入力は111の2007年〜2026-08-23凍結物。上限2026-09-06。\n"
              "未知行を直近5実出走の窓に残し、8列だけを研究用に修正。後方の既知ゼロは維持。\n"
              "prepareは学習しない。runまたはfitで明示学習し、summarizeは欠損cacheで停止。\n"
              "138列・seed42・900本・8OOF・体重mask0.5・TE10、2024/2026。"
              "同一fitからfull/preweightを予測。baseline-reuse.jsonが旧130予測の再利用条件を示す。\n"
              "coverage.jsonはbit差・1e-12超の数値差/NaN遷移・丸めだけの差を区別する。"
              "本番変更・モデル登録・採用判定は行わない。\n")
    with (WORK / "README.md").open("x") as fh:
        fh.write(readme)
    print(json.dumps({"prepare": "PASS", "needs_fit": frozen["needs_fit"],
                      "changed_rows": audit["matrix_changed_rows"],
                      "material_changed_rows": audit["matrix_material_changed_rows"],
                      "baseline": reuse["status"], "seconds": frozen["prepare_seconds"]}), flush=True)


def verify():
    frozen = read_json(WORK / "run-freeze.json")
    if (frozen["config"] != config() or frozen["config_sha256"] != stable_hash(config())
            or frozen["source_hash"] != source_hash() or frozen["runtime"] != runtime()
            or frozen["inputs"] != input_manifest() or frozen["recipe_meta"] != recipe_meta()):
        raise ValueError("132 style source/config/runtime/input freeze changed")
    for file, key in (("overlay.pkl", "overlay_sha256"), ("coverage.json", "coverage_sha256"),
                      ("baseline-reuse.json", "baseline_reuse_sha256")):
        if digest(WORK / file) != frozen[key]:
            raise ValueError(f"132 artifact changed: {file}")
    reuse = read_json(WORK / "baseline-reuse.json")
    if reuse["status"] == "REUSE_VERIFIED":
        if digest(reuse["freeze_path"]) != reuse["freeze_sha256"]:
            raise ValueError("130 freeze changed")
        for item in reuse["years"].values():
            for kind in ("cache", "receipt"):
                if digest(item[f"{kind}_path"]) != item[f"{kind}_sha256"]:
                    raise ValueError("130 verified cache/receipt changed")
    return frozen


@contextmanager
def frozen_outcomes(races):
    outcomes = {r.context.race_id: (r.n_result_rows, {l.horse_id for l in r.labels if l.win == 1}) for r in races}
    def lookup(session, ids):
        if session is not None or any(i not in outcomes for i in ids):
            raise ValueError("OOF request outside frozen training outcomes")
        return {i: outcomes[i] for i in ids}
    with patch("horseracing_training.calib_split._started_all_outcomes", side_effect=lookup):
        yield


def make_predictor(matrix, arm, overlay, smoke=False):
    if arm not in ARMS:
        raise ValueError("Unregistered research arm")
    data = matrix if arm == "baseline" else features.apply_overlay(matrix, overlay)
    return OofCalibratedPredictor(None, recipe(smoke), shared_data=data,
        n_oof_blocks=2 if smoke else 8, method="isotonic", require_sufficient=not smoke)


def output_path(arm, year, smoke=False):
    return WORK / ("smoke" if smoke else "cache") / f"{arm}-{year}.pkl"


def receipt_path(arm, year, smoke=False):
    return output_path(arm, year, smoke).with_suffix(".json")


def fit(arm, year, smoke=False):
    if arm not in ARMS or year not in YEARS:
        raise ValueError("Unregistered arm/year")
    frozen = verify()
    if not frozen["needs_fit"]:
        raise ValueError("No changed input; fitting is unnecessary")
    reuse = read_json(WORK / "baseline-reuse.json")
    if arm == "baseline" and not smoke and reuse["status"] == "REUSE_VERIFIED":
        raise ValueError("Baseline is certified for reuse; no hidden redundant fit")
    path, receipt = output_path(arm, year, smoke), receipt_path(arm, year, smoke)
    if path.exists() or receipt.exists():
        raise FileExistsError("Existing or partial fit preserved; explicit new run required")
    lock = WORK / f"{arm}-{year}{'-smoke' if smoke else ''}.lock"
    write_json(lock, {"pid": os.getpid()})
    try:
        matrix, races = load(INPUT / "snapshot.pkl")
        validate_matrix_races(matrix, races)
        fold = folds_for(races)[year]
        if fold_identity(fold) != frozen["folds"][str(year)]:
            raise ValueError("Actual fold changed")
        train, valid = list(fold.train), list(fold.valid)
        if smoke:
            train = [r for r in train if r.context.race_date.year <= 2008]
            valid = valid[:24]
        bundle = load(WORK / "overlay.pkl")
        predictor = make_predictor(matrix, arm, bundle["overlay"], smoke)
        start = time.monotonic()
        print(f"FIT {arm} {year} smoke={smoke} train={len(train)} valid={len(valid)}", flush=True)
        with frozen_outcomes(train):
            predictor.fit([r.context for r in train], num_threads=1)
        if predictor._base.feature_cols_ != frozen["feature_columns"]:
            raise ValueError("Actual fitted columns differ")
        if predictor._base.win_model_.params != recipe(smoke).resolved_params():
            raise ValueError("Actual fitted model parameters differ")
        predictions = {}
        for regime, mask in REGIMES.items():
            predictor.set_predict_weight_mask(mask)
            predictions[regime] = {}
            for er in valid:
                p = predictor.predict_race(er.context)
                predictions[regime][er.context.race_id] = {
                    h: (float(v.win), float(v.top2), float(v.top3)) for h, v in p.items()}
            validate_predictions(predictions[regime], valid)
        predictor.set_predict_weight_mask(None)
        elapsed = time.monotonic() - start
        verify()
        payload = {"arm": arm, "year": year, "smoke": smoke, "predictions": predictions,
                   "semantics_id": features.SEMANTICS_ID if arm == "candidate" else "legacy-138",
                   "source_hash": frozen["source_hash"], "overlay_sha256": frozen["overlay_sha256"],
                   "input_snapshot_sha256": frozen["inputs"]["files"]["snapshot.pkl"],
                   "train_hash": train_identity(train), "valid_ids_sha256": stable_hash([r.context.race_id for r in valid]),
                   "recipe_meta": recipe_meta(smoke), "feature_columns": predictor._base.feature_cols_,
                   "oof_info": predictor.oof_info_, "fit_seconds": elapsed,
                   "can_adopt": False, "eligible_for_verdict": False}
        dump(path, payload)
        write_json(receipt, {"cache_sha256": digest(path), "freeze_sha256": digest(WORK / "run-freeze.json"),
                             "arm": arm, "year": year, "smoke": smoke, "fit_seconds": elapsed})
        print(f"FIT PASS {arm} {year} seconds={elapsed:.1f}", flush=True)
    finally:
        lock.unlink()


def read_predictions(arm, year, frozen, valid):
    reuse = read_json(WORK / "baseline-reuse.json")
    if arm == "baseline" and reuse["status"] == "REUSE_VERIFIED":
        item = reuse["years"][str(year)]
        cache = load(item["cache_path"])
        out = cache["predictions"]
        if stable_hash(out) != item["prediction_sha256"]:
            raise ValueError("Reused raw predictions changed")
    else:
        path, receipt = output_path(arm, year), receipt_path(arm, year)
        r = read_json(receipt)  # No automatic fit or fallback on a missing result.
        if (r["cache_sha256"] != digest(path) or r["freeze_sha256"] != digest(WORK / "run-freeze.json")
                or r["arm"] != arm or r["year"] != year or r["smoke"] is not False):
            raise ValueError("Research receipt identity invalid")
        cache = load(path)
        if (cache["arm"] != arm or cache["year"] != year or cache["smoke"] is not False
                or cache["source_hash"] != frozen["source_hash"]
                or cache["overlay_sha256"] != frozen["overlay_sha256"]
                or cache["recipe_meta"] != recipe_meta()
                or cache["train_hash"] != frozen["folds"][str(year)]["train_hash"]
                or cache["feature_columns"] != frozen["feature_columns"]
                or not cache["oof_info"].get("sufficient")):
            raise ValueError("Research cache identity invalid")
        out = cache["predictions"]
    for regime in REGIMES:
        validate_predictions(out[regime], valid)
    return out


def contrast(valid, base, cand, bootstrap):
    b = {rid: {h: Prediction(*v) for h, v in p.items()} for rid, p in base.items()}
    c = {rid: {h: Prediction(*v) for h, v in p.items()} for rid, p in cand.items()}
    bs = asdict(_score_arm(valid, b, band_edges=DEFAULT_BAND_EDGES))
    cs = asdict(_score_arm(valid, c, band_edges=DEFAULT_BAND_EDGES))
    rows, days = [], {}
    for er in valid:
        pop = population_masks(er)
        if not pop.eligible:
            continue
        rid, day, winner = er.context.race_id, str(er.context.race_date), pop.winner_horse_id
        bl = -float(np.log(np.clip(base[rid][winner][0], 1e-15, 1 - 1e-15)))
        cl = -float(np.log(np.clip(cand[rid][winner][0], 1e-15, 1 - 1e-15)))
        rows.append({"race_id": rid, "race_date": day, "baseline_nll": bl, "candidate_nll": cl, "diff": cl - bl})
        days.setdefault(day, []).append(cl - bl)
    ci = asdict(race_day_cluster_bootstrap_ci_v1(days, **bootstrap)) if len(days) >= 2 else None
    return {"n_races": len(valid), "n_eligible": len(rows), "n_days": len(days),
            "baseline": bs, "candidate": cs, "winner_nll_diff": cs["winner_nll"] - bs["winner_nll"],
            "top2_diff": cs["top2_logloss"] - bs["top2_logloss"],
            "top3_diff": cs["top3_logloss"] - bs["top3_logloss"],
            "sample_ci": ci, "rows": rows,
            "uncertainty": "Single seed; day-cluster sampling only, no new estimate of refit or selection uncertainty"}


def summarize():
    frozen = verify()
    if (WORK / "summary.json").exists():
        raise FileExistsError("Summary exists; never overwrite")
    if not frozen["needs_fit"]:
        write_json(WORK / "summary.json", {"status": "NO_INPUT_CHANGE", "additional_fits": 0,
                                          "data_through": str(DATA_END), "can_adopt": False})
        return
    matrix, races = load(INPUT / "snapshot.pkl")
    validate_matrix_races(matrix, races)
    folds = folds_for(races)
    del matrix
    gc.collect()
    affected = set(load(WORK / "overlay.pkl")["affected_race_ids"])
    results, pooled = {}, {r: {"valid": [], "base": {}, "cand": {}} for r in REGIMES}
    for year, fold in folds.items():
        base = read_predictions("baseline", year, frozen, fold.valid)
        cand = read_predictions("candidate", year, frozen, fold.valid)
        results[str(year)] = {}
        for regime in REGIMES:
            item = contrast(fold.valid, base[regime], cand[regime], frozen["config"]["bootstrap"])
            sub = [r for r in fold.valid if r.context.race_id in affected]
            item["materially_affected_input_races"] = (
                contrast(sub, base[regime], cand[regime], frozen["config"]["bootstrap"]) if sub else None)
            results[str(year)][regime] = item
            pooled[regime]["valid"].extend(fold.valid)
            pooled[regime]["base"].update(base[regime])
            pooled[regime]["cand"].update(cand[regime])
    report = {"status": "RESEARCH_COMPLETE", "years": results,
              "pooled": {r: contrast(v["valid"], v["base"], v["cand"], frozen["config"]["bootstrap"])
                         for r, v in pooled.items()},
              "data_through": str(DATA_END), "cutoff": str(features.CUTOFF),
              "freeze_sha256": digest(WORK / "run-freeze.json"),
              "baseline_reuse": read_json(WORK / "baseline-reuse.json"),
              "can_adopt": False, "eligible_for_verdict": False}
    verify()
    write_json(WORK / "summary.json", report)
    print(json.dumps({r: x["winner_nll_diff"] for r, x in report["pooled"].items()}), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("prepare", "verify", "fit", "run", "summarize"))
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--year", type=int, choices=YEARS)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "verify":
        verify(); print("VERIFY PASS", flush=True)
    elif args.command == "fit":
        if args.arm is None or args.year is None:
            ap.error("fit requires --arm and --year")
        fit(args.arm, args.year, args.smoke)
    elif args.command == "run":
        frozen = verify()
        if not frozen["needs_fit"]:
            print("No changed input; no fits required", flush=True)
            return
        reuse = read_json(WORK / "baseline-reuse.json")
        for year in YEARS:
            for arm in ARMS:
                if arm == "baseline" and not args.smoke and reuse["status"] == "REUSE_VERIFIED":
                    continue
                fit(arm, year, args.smoke)
    else:
        summarize()


if __name__ == "__main__":
    main()
