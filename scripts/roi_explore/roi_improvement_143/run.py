"""Freeze the existing-data-only 143 feature and subsequent weighting study."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/143-existing-feature-roi"
OLD = ROOT / "artifacts/142-roi-policy-model-improvement"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


def write(path: Path, obj) -> None:
    with path.open("x") as stream:
        json.dump(obj, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def baseline_ensemble():
    """Rebuild the frozen comparator, checking each source recipe and artifact."""
    base = module("roi143_prepare_base", HERE.parent / "roi_improvement_142/models.py")
    frames, inputs = [], []
    provenance = dict(input_sha256=base.sha(ROWS), source_sha256=base.sha(Path(base.__file__)),
                      dependency_sha256={"direct_return_model": base.sha(Path(base.legacy.__file__))},
                      protocol_sha256=base.sha(OLD / "protocol.json"), features=base.FEATURES,
                      python=base.platform.python_version(), numpy=np.__version__, pandas=pd.__version__,
                      lightgbm=base.lgb.__version__, lower=base.LOWER, upper=base.UPPER)
    for year in range(2019, 2027):
        first, total = None, None
        for seed in range(1, 16):
            folder = OLD / "models" / f"seed_{seed:02d}" / f"year_{year}" / "baseline"
            receipt_path = folder / "receipt.json"
            receipt = json.loads(receipt_path.read_text())
            recipe = receipt["recipe"]
            if recipe["seed"] != seed or recipe["train_end"] >= recipe["target_start"]:
                raise ValueError("Baseline recipe is not the frozen past-only comparator")
            if recipe["rounds"] != 300 or recipe["arm"] != "binary" or recipe["earlystop"] or recipe["overrides"]:
                raise ValueError("Baseline recipe differs")
            expected = dict(provenance, year=year, seed=seed)
            if any(recipe["identity"].get(k) != v for k, v in expected.items()):
                raise ValueError("Baseline protocol provenance differs")
            for name, digest in receipt["hashes"].items():
                if base.sha(folder / name) != digest:
                    raise ValueError(f"Baseline artifact changed: {folder / name}")
            pred_path = folder / "predictions.parquet"
            specification = json.loads((folder / "feature_spec.json").read_text())
            spec_expected = dict(features=base.NUM, cats=base.CAT,
                                 params=dict(base.BASE_PARAMS, seed=seed, metric="binary_logloss"),
                                 objective="binary", best_iteration=300, train_from=2007,
                                 train_through=year-1, trained_for_year=year, training_cutoff=recipe["train_end"])
            if any(specification.get(k) != v for k, v in spec_expected.items()):
                raise ValueError("Baseline feature/model provenance differs")
            pred = pd.read_parquet(pred_path)
            keys = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
            if not pred.year.eq(year).all():
                raise ValueError("Baseline prediction year differs")
            if first is None:
                first = pred[keys].copy()
                total = np.zeros(len(pred))
            elif not first.equals(pred[keys]):
                raise ValueError("Baseline seed fields differ")
            total += pred.p.to_numpy(float)
            inputs += [receipt_path, pred_path]
        first["p"] = total / 15
        frames.append(first)
    result = pd.concat(frames, ignore_index=True)
    saved = pd.read_parquet(OLD / "model_comparison/baseline_ensemble.parquet")
    if not result.equals(saved):
        raise ValueError("Rebuilt comparator differs from completed142 ensemble")
    return result, inputs


def prepare_weights(out: Path):
    baseline, _ = baseline_ensemble()
    pool = baseline.loc[baseline.year <= 2025, ["race_id", "horse_id", "race_date", "year", "p"]].copy()
    pool = pool.rename(columns={"p": "p_oof"})
    pool["oof_train_through"] = pool.year - 1
    path = out / "weights.parquet"
    pool.to_parquet(path, index=False)
    write(out / "weights_manifest.json", {
        "source": "142 baseline raw-probability mean of seeds1..15; reconstructed from audited annual fits",
        "rows": len(pool), "years": sorted(pool.year.unique().tolist()),
        "neutral_years": list(range(2007, 2019)),
        "note": "2019 weighted fit has no mentor coverage and must reproduce baseline exactly; never use same-year fit for its own training weights.",
    })


def freeze(out: Path):
    models = module("roi143_freeze_models", HERE / "models.py")
    features = module("roi143_freeze_features", HERE / "features.py")
    _, originals = baseline_ensemble()
    additions = out / "data/additions.parquet"
    manifest_path = out / "data/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # Freeze every actual source dependency, including imported feature builders.
    import horseracing_features.pace_features as pace
    import horseracing_features.pace_scenario_features as scenario
    import horseracing_features.corner_trajectory_features as corner
    import horseracing_features.speed_figure_features as speed
    import horseracing_features.loader as loader
    import horseracing_db.enums as enums
    paths = [ROWS, additions, manifest_path, out / "weights.parquet", out / "weights_manifest.json",
             OLD / "protocol.json", OLD / "protocol.sha256",
             OLD / "existing_policies/inputs.parquet", OLD / "model_comparison/baseline_ensemble.parquet",
             ROOT / "artifacts/111-ability-observation/source-frames.pkl",
             ROOT / "artifacts/111-ability-observation/snapshot.json",
             HERE.parent / "direct_return_model.py",
             HERE.parent / "roi_improvement_142/models.py", HERE.parent / "roi_improvement_142/policies.py",
             *(Path(m.__file__) for m in (pace, scenario, corner, speed, loader, enums)),
             *HERE.glob("*.py"), *originals]
    hashes = {str(p.relative_to(ROOT)): models.base.sha(p) for p in sorted(set(paths))}
    document = {
        "feature": "143-existing-feature-roi", "version": 1,
        "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rows": str(ROWS.relative_to(ROOT)), "additions": str(additions.relative_to(ROOT)),
        "weights": str((out / "weights.parquet").relative_to(ROOT)),
        "comparison_marker": str((out / "comparison_marker.json").relative_to(ROOT)),
        "window": {"training_from": 2007, "through": "2026-06-26", "model_outer": list(range(2019, 2027))},
        "seeds": list(range(1, 16)), "max_workers": 2, "threads_each": 1,
        "bundles": {"speed": list(features.SPEED_COLUMNS), "pace": list(features.PACE_COLUMNS)},
        "arms": ["speed", "pace", "weighted_baseline"],
        "stage_order": "freeze all recipes; fit speed and pace; save/hash their comparison; only then fit weighted_baseline77; no selection of weighting parent from results",
        "models": {"rounds": 300, "params": models.base.BASE_PARAMS, "objective": "binary"},
        "base_features": models.base.FEATURES,
        "weight_recipe": models.WEIGHT_RECIPE,
        "comparator": "142 same-recipe re-fitted77 baseline ensemble; existing current ens15 shown separately as reference",
        "policy": {"raw_ev_gt": 1.2, "budget_yen_per_race": 1000, "unit_yen": 100,
                   "max_horses": 10, "allocation": "floor(10/n) units per selected horse; remainder and skipped budget stay cash"},
        "bootstrap": {"repetitions": 4000, "seed": 20261019, "family_size": 3,
                      "unit": "all eligible race dates including no-bet dates", "adjustment": "Bonferroni separately per metric over all three preplanned candidates"},
        "feature_data_manifest": manifest,
        "inputs_and_source_sha256": hashes,
        "can_adopt": False, "untouched_holdout": False, "eligible_for_verdict": False,
        "limitations": ["All historical periods have already informed research; no untouched confirmation sample.",
                        "Saved closing odds imply hypothetical settlement, not decision-time execution profits.",
                        "Official win payout archive has zero coverage in the fixed main window (142 audit).",
                        "Feature groups are separate fixed candidates, with no post-result feature, threshold, or hyperparameter search.",
                        "Added historical features exclude malformed source races406/407; existing77 columns remain frozen.",
                        "Stage3 weights use only142 annual OOF2019..2025; earlier races stay exactly neutral.",
                        "Day bootstrap is conditional on saved seeds and does not erase prior historical model selection.",
                        "Within-day drawdown order is race_id, not verified chronological post times."],
    }
    write(out / "protocol.json", document)
    (out / "protocol.sha256").write_text(models.base.sha(out / "protocol.json") + "\n")
    models.verify_protocol(out / "protocol.json", ROWS, additions, out / "weights.parquet")
    return document


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["prepare-weights", "freeze", "verify"])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "prepare-weights":
        if (args.out / "weights.parquet").exists():
            raise FileExistsError("Weights already prepared")
        prepare_weights(args.out)
    elif args.command == "freeze":
        freeze(args.out)
    else:
        module("roi143_verify_models", HERE / "models.py").verify_protocol(
            args.out / "protocol.json", ROWS, args.out / "data/additions.parquet", args.out / "weights.parquet")
    print(json.dumps({"command": args.command, "status": "complete", "out": str(args.out)}), flush=True)


if __name__ == "__main__":
    main()
