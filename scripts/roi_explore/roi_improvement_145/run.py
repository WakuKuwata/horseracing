"""Freeze the single pace14 plus original143 weight comparison."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/145-pace-weight-combination"
PREVIOUS = ROOT / "artifacts/143-existing-feature-roi"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
ADDITIONS = PREVIOUS / "data/additions.parquet"
WEIGHTS = PREVIOUS / "weights.parquet"
CONTRASTS = [["weighted_pace", arm] for arm in ("baseline", "weighted_baseline", "pace")]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def references():
    prior = module("roi145_reference143_report", HERE.parent / "roi_improvement_143/report.py")
    original = json.loads((PREVIOUS / "protocol.json").read_text())
    prior.models.verify_protocol(PREVIOUS / "protocol.json", ROWS, ADDITIONS, WEIGHTS)
    baseline, base_inputs = prior.research.baseline_ensemble()
    current = pd.read_parquet(PREVIOUS / "weighted_comparison/current_reference_ensemble.parquet")
    frames = {"current_reference": current, "baseline": baseline}
    paths = list(base_inputs)
    for arm, directory, comparison in (("weighted_baseline", "weighted_models", "weighted_comparison"),
                                       ("pace", "models", "features_comparison")):
        frames[arm], _ = prior.ensemble(PREVIOUS, arm, original)
        pd.testing.assert_frame_equal(frames[arm], pd.read_parquet(PREVIOUS / comparison / f"{arm}_ensemble.parquet"))
        for year in range(2019, 2027):
            for seed in range(1, 16):
                fit = PREVIOUS / directory / f"seed_{seed:02d}/year_{year}" / arm
                paths.extend([fit / "receipt.json", fit / "predictions.parquet"])
    for frame in frames.values():
        pd.testing.assert_frame_equal(baseline[prior.models.PRED_KEYS], frame[prior.models.PRED_KEYS])
    return frames, paths


def freeze(out):
    models = module("roi145_freeze_models", HERE / "models.py")
    _, reference_inputs = references()
    previous = json.loads((PREVIOUS / "protocol.json").read_text())
    paths = [ROOT / name for name in previous["inputs_and_source_sha256"]]
    paths += [ROWS, ADDITIONS, WEIGHTS, PREVIOUS / "protocol.json", PREVIOUS / "protocol.sha256",
              PREVIOUS / "weighted_comparison/summary.json", PREVIOUS / "features_comparison/summary.json",
              *reference_inputs, *HERE.glob("*.py"),
              *(HERE.parent / "roi_improvement_143" / name for name in ("models.py", "report.py", "run.py")),
              *(PREVIOUS / f"weighted_comparison/{arm}_ensemble.parquet" for arm in
                ("current_reference", "baseline", "weighted_baseline")),
              PREVIOUS / "features_comparison/pace_ensemble.parquet"]
    hashes = {str(path.relative_to(ROOT)): models.sha(path) for path in sorted(set(paths))}
    document = dict(feature="145-pace-weight-combination", version=1,
        frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        rows=str(ROWS.relative_to(ROOT)), additions=str(ADDITIONS.relative_to(ROOT)), weights=str(WEIGHTS.relative_to(ROOT)),
        window=dict(training_from=2007, through="2026-06-26", model_outer=list(range(2019, 2027))),
        seeds=list(range(1, 16)), max_workers=2, threads_each=1, arms=["weighted_pace"],
        models=dict(objective="binary", rounds=300, params=models.base.BASE_PARAMS),
        base_features=models.base.FEATURES, bundles=previous["bundles"],
        candidate_features=models.parent.feature_contract("pace", previous["bundles"])[2],
        weight_recipe=models.parent.WEIGHT_RECIPE,
        weight_definition="Exact original143 weights in each annual train; same baseline mentor OOF; no144 transformation; no pace-derived teacher",
        policy=dict(raw_ev_gt=1.2, budget_yen_per_race=1000, stake_unit_yen=100,
                    max_horses=10, allocation="equal floor(10/n)100yen units; remaining and skipped budget staycash"),
        contrasts=CONTRASTS,
        bootstrap=dict(repetitions=4000, seed=20261024, family_size=3,
                       unit="all common eligible race dates including no-bet dates", adjustment="Bonferroni separately per metric"),
        sensitivity=dict(same_calendar_through="06-21", known_influence_race="202608030111",
                         top_difference_race="retrospective sensitivity only; never a trading exclusion"),
        scope="Proposal2 only: existing pace14 plus original143 weights; proposal3 OOF backfill deferred",
        controls="Reused142baseline77,143original③,143pace91; current savedens15 separately as reference",
        inputs_and_source_sha256=hashes, can_adopt=False, eligible_for_verdict=False, untouched_holdout=False,
        limitations=["Previously examined historical development data; no untouched holdout.",
                     "Stored closing odds imply hypothetical settlement, not executable decision-time profits.",
                     "Official win payout archive has no coverage in the fixed primary window (142audit).",
                     "Original143OOF starts2019; older574304rows retain exact neutral weights;2019 must reproduce pace-only predictions.",
                     "Pace features and original weights are fixed; no new feature, teacher, threshold or hyperparameter search.",
                     "Day bootstrap is conditional on savedseeds and does not remove prior repeated historical model selection.",
                     "Currentreference and refitted77 baseline differ slightly due to train-only categories.",
                     "Within-day drawdown order is race_id, not verified chronological post times."])
    out.mkdir(parents=True, exist_ok=True)
    write(out / "protocol.json", document)
    (out / "protocol.sha256").write_text(models.sha(out / "protocol.json") + "\n")
    models.verify_protocol(out / "protocol.json", ROWS, ADDITIONS, WEIGHTS)
    return document


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["freeze", "verify"])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.out)
    else:
        module("roi145_verify_models", HERE / "models.py").verify_protocol(args.out / "protocol.json", ROWS, ADDITIONS, WEIGHTS)
    print(json.dumps(dict(command=args.command, out=str(args.out), status="complete")), flush=True)


if __name__ == "__main__":
    main()
