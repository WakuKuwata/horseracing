"""110: pinned-input group ablations using the existing arm E and v4 gate."""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import hashlib
import json
import os
from pathlib import Path
import pickle
import subprocess
import time
from unittest.mock import patch

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from horseracing_db.models import ModelVersion
from horseracing_db.session import create_db_engine
from horseracing_db.validation import FEATURE_POOL_START
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.decision import assert_confirmatory, gate_config_hash
from horseracing_eval.delta_provenance import assert_delta_provenance
from horseracing_eval.hashing import stable_hash
from horseracing_eval.paired import paired_eval
from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.dataset import build_training_matrix
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs/110-feature-pruning"
WORK = ROOT / "artifacts/110-feature-pruning"
MODEL = ROOT / "artifacts/model_versions/lgbm-094-cap900"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")


def save_pickle(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(value, f, protocol=5)
    tmp.replace(path)


def load_config():
    cfg = json.loads((SPEC / "gate-config.json").read_text())
    expected = (SPEC / "gate-config.hash.txt").read_text().strip()
    if gate_config_hash(cfg) != expected:
        raise ValueError("Frozen config changed")
    assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg["eval_window"])
    assert_delta_provenance(cfg, root=ROOT)
    return cfg


def columns_from_model():
    return next(line.split("=", 1)[1].split() for line in
                (MODEL / "model.txt").read_text().splitlines()
                if line.startswith("feature_names="))


def source_hash():
    paths = [Path(__file__).resolve()]
    for package in ("db", "features", "training", "eval", "probability"):
        paths.extend((ROOT / package / "src").rglob("*.py"))
    return stable_hash([(str(p.relative_to(ROOT)), digest(p)) for p in sorted(paths)])


def freeze(cfg):
    # Dataset provenance remains append-only; execution is frozen separately after review.
    meta = json.loads((WORK / "snapshot.json").read_text())
    if meta["snapshot_sha256"] != digest(WORK / "snapshot.pkl") or meta["config_hash"] != gate_config_hash(cfg):
        raise ValueError("Snapshot or config changed")
    deps = [f"{p}/src" for p in ("db", "features", "training", "eval", "probability")]
    subprocess.run(["git", "diff", "--exit-code", meta["git_sha"], "--", *deps],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    write_json(WORK / "run-freeze.json", {"snapshot_sha256": meta["snapshot_sha256"],
        "snapshot_metadata_sha256": digest(WORK / "snapshot.json"),
        "config_hash": gate_config_hash(cfg), "source_hash": source_hash(),
        "model_sha256": digest(MODEL / "model.txt"),
        "note": "Data preparation code provenance retained; reviewed execution frozen before any fit."})
    print("RUN FREEZE OK", flush=True)


def make_recipe(cfg, drops=(), smoke=False):
    a = cfg["arms"]
    return ModelRecipe(objective="pl_topk", calibration="none", calib_frac=0.0,
                       seed=a["seed"], drop_features=tuple(drops),
                       params=(("n_estimators", 5 if smoke else a["n_estimators"]),),
                       weight_mask_rate=a["weight_mask_rate"],
                       weight_mask_seed=a["weight_mask_seed"],
                       target_encode_cols=tuple(a["target_encode_cols"]),
                       te_smoothing=a["te_smoothing"])


def validate_scope(matrix, recipe):
    if matrix.feature_cols != columns_from_model():
        raise ValueError("Baseline columns differ from shipped model")
    drops = set(recipe.drop_features)
    if not drops <= set(matrix.feature_cols) or len(drops) != len(recipe.drop_features):
        raise ValueError("Unknown or duplicate drop column")
    scoped = LightGBMPredictor(None, drop_features=recipe.drop_features)._scope_columns(matrix)
    expected = [c for c in matrix.feature_cols if c not in drops]
    if scoped.feature_cols != expected:
        raise ValueError("Drop scope was not applied")
    return expected


def prepare(cfg):
    if (WORK / "snapshot.pkl").exists():
        raise FileExistsError("Snapshot already exists")
    WORK.mkdir(parents=True, exist_ok=True)
    end = dt.date.fromisoformat(cfg["eval_window"]["to"])
    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    engine = create_db_engine(url, isolation_level="REPEATABLE READ")
    t0 = time.monotonic()
    with Session(engine) as s:
        s.execute(text("SET TRANSACTION READ ONLY"))
        active = list(s.scalars(select(ModelVersion.model_version).where(
            ModelVersion.adoption_status == "active")))
        if active != ["lgbm-094-cap900"]:
            raise ValueError(f"Active model changed: {active}")
        print("PREPARE: fixed DB snapshot, fresh feature build", flush=True)
        matrix = build_training_matrix(s, representation="raw", end_date=end)
        races = load_eval_races(s, start_date=FEATURE_POOL_START, end_date=end)
    validate_scope(matrix, make_recipe(cfg))
    for candidate in cfg["candidates"]:
        validate_scope(matrix, make_recipe(cfg, candidate["drop_features"]))
    save_pickle(WORK / "snapshot.pkl", (matrix, races))
    info = {"snapshot_sha256": digest(WORK / "snapshot.pkl"),
            "config_hash": gate_config_hash(cfg), "driver_sha256": digest(__file__),
            "model_sha256": digest(MODEL / "model.txt"),
            "n_rows": len(matrix.frame), "n_races": len(races),
            "feature_columns": matrix.feature_cols, "build_audit": matrix.build_audit,
            "data_through": str(end), "elapsed_seconds": time.monotonic() - t0,
            "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    write_json(WORK / "snapshot.json", info)
    print(f"PREPARE OK rows={len(matrix.frame)} races={len(races)} elapsed={info['elapsed_seconds']:.1f}s", flush=True)


class ReplayPredictor:
    def __init__(self, predictions):
        self.predictions = predictions

    def predict_race(self, race):
        p = self.predictions[race.race_id]
        if set(p) != {h.horse_id for h in race.started_horses}:
            raise ValueError("Cached prediction population mismatch")
        return p


class CachedFactory:
    """Only reuse predictions fitted on this exact snapshot/train-set/recipe."""
    def __init__(self, cfg, matrix, races, identity, drops=(), smoke=False, label="baseline"):
        self.factory = CalibSplitFactory(None, make_recipe(cfg, drops, smoke),
            n_oof_blocks=2 if smoke else cfg["arms"]["n_oof_blocks"],
            method="isotonic", require_sufficient=not smoke)
        self.factory._shared = matrix
        self.expected_columns = validate_scope(matrix, self.factory.recipe)
        self.races = races
        if any(er.n_result_rows is None for er in races):
            raise ValueError("Frozen outcomes lack result-row counts")
        self.outcomes = {er.context.race_id: (er.n_result_rows,
            {lab.horse_id for lab in er.labels if lab.win == 1}) for er in races}
        self.identity = identity
        self.label = label
        self.smoke = smoke
        self.recipe_meta = self.factory.recipe_meta
        self.recipe_hash = self.factory.recipe_hash

    def fit(self, train_races, *, num_threads=None):
        train_hash = stable_hash([(r.race_id, str(r.race_date),
            [h.horse_id for h in r.started_horses]) for r in train_races])
        year = max(r.race_date.year for r in train_races) + 1
        key = stable_hash([self.identity, self.recipe_hash, train_hash, year])
        path = WORK / "cache" / f"{key}.pkl"
        if path.exists():
            with path.open("rb") as f:
                cached = pickle.load(f)
            if cached["key"] != key:
                raise ValueError("Cache identity mismatch")
            if (cached["feature_columns"] != self.expected_columns
                or cached["recipe_meta"] != self.recipe_meta
                or (not self.smoke and not cached["oof_info"].get("sufficient"))):
                raise ValueError("Cache recipe/columns/calibration invalid")
            print(f"CACHE {self.label} year={year}", flush=True)
            return ReplayPredictor(cached["predictions"])
        t0 = time.monotonic()
        print(f"FIT {self.label} year={year} train_races={len(train_races)}", flush=True)
        # OOF reads result completeness and dead-heat winners through this one DB helper.
        # Supply the same facts from the frozen EvalRace objects, preserving all existing
        # OOF/label code. Local, single-threaded patch; restored even on failure.
        with patch("horseracing_training.calib_split._started_all_outcomes",
                   side_effect=lambda session, ids: {rid: self.outcomes[rid] for rid in ids
                                                      if rid in self.outcomes}):
            pred = self.factory.fit(train_races, num_threads=1)
        if pred._base.feature_cols_ != self.expected_columns:
            raise ValueError("Actual fitted columns differ from frozen scope")
        predictions = {}
        for er in self.races:
            if er.context.race_date.year != year:
                continue
            p = pred.predict_race(er.context)
            wins = np.array([v.win for v in p.values()])
            if (set(p) != {h.horse_id for h in er.context.started_horses}
                or not np.isfinite(wins).all() or not np.isclose(wins.sum(), 1.0, atol=1e-8)):
                raise ValueError("Prediction probability/population invalid")
            predictions[er.context.race_id] = p
        save_pickle(path, {"key": key, "train_hash": train_hash,
            "recipe_meta": self.recipe_meta, "feature_columns": pred._base.feature_cols_,
            "predictions": predictions, "oof_info": pred.oof_info_,
            "elapsed_seconds": time.monotonic() - t0})
        print(f"FIT OK {self.label} year={year} elapsed={time.monotonic()-t0:.1f}s", flush=True)
        return ReplayPredictor(predictions)


def advances(report, cfg):
    r = report.gate.reasons
    return (report.periods["all"]["diff"] < 0
        and r["top2_diff"] <= cfg["top_noninferior"]["top2"]
        and r["top3_diff"] <= cfg["top_noninferior"]["top3"]
        and r["cand_ece"] is not None and r["act_ece"] is not None
        and r["cand_ece"] - r["act_ece"] <= cfg["calibration"]["noninferior_width"])


def run(cfg, stage):
    meta = json.loads((WORK / "snapshot.json").read_text())
    frozen = json.loads((WORK / "run-freeze.json").read_text())
    if (frozen["snapshot_sha256"] != digest(WORK / "snapshot.pkl")
        or frozen["snapshot_metadata_sha256"] != digest(WORK / "snapshot.json")
        or frozen["config_hash"] != gate_config_hash(cfg)
        or frozen["source_hash"] != source_hash()
        or frozen["model_sha256"] != digest(MODEL / "model.txt")):
        raise ValueError("Snapshot/config/source/model changed since run freeze")
    with (WORK / "snapshot.pkl").open("rb") as f:
        matrix, all_races = pickle.load(f)
    phase_cfg = json.loads(json.dumps(cfg))
    if stage != "full":
        phase_cfg["eval_window"] = cfg[stage]["eval_window"]
        phase_cfg["subgroup_guard"]["critical_subgroups"] = ["canonical"]
    win = phase_cfg["eval_window"]
    start, end = (dt.date.fromisoformat(win[k]) for k in ["from", "to"])
    races = [r for r in all_races if r.context.race_date <= end]
    candidates = cfg["candidates"]
    if stage == "full":
        candidates = [c for c in candidates if json.loads(
            (SPEC / "evidence" / f"screen-{c['id']}.json").read_text())["progression"] == "ADVANCE"]
    elif stage == "smoke":
        candidates = candidates[:1]
    for c in candidates:
        out = SPEC / "evidence" / f"{stage}-{c['id']}.json"
        if out.exists():
            print(f"COMPLETED {out.name}; preserving existing evidence", flush=True)
            continue
        smoke = stage == "smoke"
        kwargs = dict(cfg=cfg, matrix=matrix, races=races,
                      identity=[meta["snapshot_sha256"], frozen["source_hash"], stage], smoke=smoke)
        candidate = CachedFactory(**kwargs, drops=c["drop_features"], label=c["id"])
        active = CachedFactory(**kwargs)
        t0 = time.monotonic()
        report = paired_eval(candidate, active, races, gate_config=phase_cfg,
            first_valid_year=start.year, valid_from=start, subgroups=True,
            num_threads=1, snapshot={**meta, "run_freeze": frozen, "stage": stage, "candidate": c})
        if not any(r.diff != 0 for r in report.evidence.rows):
            raise ValueError("Ablation predictions are identical; no verdict")
        if smoke:
            result = {"stage": stage, "structure": "PASS", "can_adopt": False,
                      "n_races": report.n_races, "elapsed_seconds": time.monotonic() - t0}
        else:
            evidence = WORK / f"{stage}-{c['id']}-evidence.json"
            write_json(evidence, report.evidence.to_dict())
            result = report.to_dict()
            result.pop("evidence", None)
            result.pop("diffs_by_day", None)
            result.update(stage=stage, candidate=c, can_adopt=False,
                evidence_path=str(evidence), evidence_sha256=digest(evidence),
                study_config_hash=gate_config_hash(cfg), evidence_regime="historical_development",
                elapsed_seconds=time.monotonic() - t0)
            result["gate_readout"] = result.pop("decision")
            result["progression"] = ("ADVANCE" if advances(report, cfg) else "DEFER") if stage == "screen" else "COMPLETE"
            print(f"RESULT {stage} {c['id']} diff={report.periods['all']['diff']:+.6f} gate={report.decision} progression={result['progression']}", flush=True)
        write_json(out, result)
        del candidate, active, report
        gc.collect()
    print(f"STAGE COMPLETE {stage}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["prepare", "freeze", "smoke", "screen", "full"])
    args = ap.parse_args()
    cfg = load_config()
    if args.stage == "prepare":
        prepare(cfg)
    elif args.stage == "freeze":
        freeze(cfg)
    else:
        run(cfg, args.stage)


if __name__ == "__main__":
    main()
