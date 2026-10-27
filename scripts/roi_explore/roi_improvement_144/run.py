"""Freeze only the race-normalized weighting comparison; preserve142/143."""
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
OUT = ROOT / "artifacts/144-race-normalized-weight"
PREVIOUS = ROOT / "artifacts/143-existing-feature-roi"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
WEIGHTS = PREVIOUS / "weights.parquet"


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
    """Rebuild both15seed controls from immutable receipts, not just summaries."""
    parent_report = module("roi144_reference143_report", HERE.parent / "roi_improvement_143/report.py")
    original = json.loads((PREVIOUS / "protocol.json").read_text())
    parent_report.models.verify_protocol(PREVIOUS / "protocol.json", ROWS,
        PREVIOUS / "data/additions.parquet", WEIGHTS)
    baseline, base_inputs = parent_report.research.baseline_ensemble()
    weighted, _ = parent_report.ensemble(PREVIOUS, "weighted_baseline", original)
    saved = pd.read_parquet(PREVIOUS / "weighted_comparison/weighted_baseline_ensemble.parquet")
    pd.testing.assert_frame_equal(weighted, saved)
    current = pd.read_parquet(PREVIOUS / "weighted_comparison/current_reference_ensemble.parquet")
    keys = parent_report.models.PRED_KEYS
    for frame in (weighted, current):
        pd.testing.assert_frame_equal(baseline[keys], frame[keys])
    paths = list(base_inputs)
    for year in range(2019, 2027):
        for seed in range(1, 16):
            fit = PREVIOUS / "weighted_models" / f"seed_{seed:02d}/year_{year}/weighted_baseline"
            paths.extend([fit / "receipt.json", fit / "predictions.parquet"])
    return {"current_reference": current, "baseline": baseline, "weighted_baseline": weighted}, paths


def freeze(out):
    models = module("roi144_freeze_models", HERE / "models.py")
    _, reference_inputs = references()
    previous = json.loads((PREVIOUS / "protocol.json").read_text())
    # Parent protocol also binds the precise historical reference dependencies.
    paths = [ROOT / name for name in previous["inputs_and_source_sha256"]]
    paths += [ROWS, WEIGHTS, PREVIOUS / "protocol.json", PREVIOUS / "protocol.sha256",
              PREVIOUS / "weighted_comparison/summary.json", *reference_inputs,
              *HERE.glob("*.py"),
              *(HERE.parent / "roi_improvement_143" / name for name in ("models.py", "report.py", "run.py")),
              *(PREVIOUS / f"weighted_comparison/{arm}_ensemble.parquet" for arm in
                ("current_reference", "baseline", "weighted_baseline"))]
    hashes = {str(path.relative_to(ROOT)): models.sha(path) for path in sorted(set(paths))}
    document = dict(feature="144-race-normalized-weight", version=1,
        frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        rows=str(ROWS.relative_to(ROOT)), weights=str(WEIGHTS.relative_to(ROOT)),
        window=dict(training_from=2007, through="2026-06-26", model_outer=list(range(2019, 2027))),
        seeds=list(range(1, 16)), max_workers=2, threads_each=1,
        arms=["within_race", "race_only"],
        models=dict(objective="binary", rounds=300, params=models.base.BASE_PARAMS),
        base_features=models.base.FEATURES,
        weight_recipe=models.parent.WEIGHT_RECIPE, weight_transforms=models.TRANSFORMS,
        transform_definition="w = exact143 weights in each annual train; a_r=mean_r(w); within_race=w/a_r; race_only=a_r; no further normalization",
        policy=dict(raw_ev_gt=1.2, budget_yen_per_race=1000, stake_unit_yen=100,
                    max_horses=10, allocation="equal floor(10/n)100yen units; remaining and skipped budget staycash"),
        contrasts=[[a, b] for a in ("within_race", "race_only") for b in ("baseline", "weighted_baseline")],
        bootstrap=dict(repetitions=4000, seed=20261024, family_size=4,
                       unit="all common eligible race dates including no-bet dates", adjustment="Bonferroni separately per metric"),
        sensitivity=dict(same_calendar_through="06-21", known_influence_race="202608030111",
                         top_difference_race="retrospective sensitivity only; never a trading exclusion"),
        scope="Proposal1 only: mean-one weights within each race plus race-onlymechanism control; pace interaction and OOF backfill deferred",
        controls="Reused142same-recipe baseline77 plus143original③; existingcurrentens15 shown separately as reference",
        inputs_and_source_sha256=hashes, can_adopt=False, eligible_for_verdict=False, untouched_holdout=False,
        limitations=["Previously examined historical development data; no untouched holdout.",
                     "Stored closing odds imply hypothetical settlement, not executable decision-time profits.",
                     "Official win payout archive has no coverage in the fixed primary window (142audit).",
                     "Original143OOF starts2019; older574304rows retainexactly neutralweights;2019 is a paritycontrol.",
                     "Mean1perrace does not give everyrace equal loss: largerfields still have more rows.",
                     "Weightfactor controls aid mechanism interpretation; nonlinear fitted-model effects are not an additive causal decomposition.",
                     "Day bootstrap is conditional on savedseeds; it does not remove prior repeated historicalmodel selection.",
                     "Currentreference and refitted77 baseline differ slightly due to train-only categories.",
                     "Within-day drawdown order is race_id, not verified chronological post times."])
    out.mkdir(parents=True, exist_ok=True)
    write(out / "protocol.json", document)
    (out / "protocol.sha256").write_text(models.sha(out / "protocol.json") + "\n")
    models.verify_protocol(out / "protocol.json", ROWS, WEIGHTS)
    return document


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["freeze", "verify"])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.out)
    else:
        module("roi144_verify_models", HERE / "models.py").verify_protocol(args.out / "protocol.json", ROWS, WEIGHTS)
    print(json.dumps(dict(command=args.command, out=str(args.out), status="complete")), flush=True)


if __name__ == "__main__":
    main()
