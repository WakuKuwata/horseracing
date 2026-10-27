"""Freeze the fixed half-probability blend; no fitting or policy search."""
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
OUT = ROOT / "artifacts/147-fixed-half-blend"
PREVIOUS = ROOT / "artifacts/143-existing-feature-roi"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
ADDITIONS = PREVIOUS / "data/additions.parquet"
WEIGHTS = PREVIOUS / "weights.parquet"
CONTRASTS = [["half_blend", arm] for arm in ("baseline", "weighted_baseline", "pace")]


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
    prior = module("roi147_reference143_report", HERE.parent / "roi_improvement_143/report.py")
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
                rec = json.loads((fit / "receipt.json").read_text())
                expected_features = prior.models.feature_contract(arm, original["bundles"])[2]
                expected_params = dict(prior.models.base.BASE_PARAMS, seed=seed, objective="binary", metric="binary_logloss")
                if rec["recipe"]["features"] != expected_features or rec["recipe"]["params"] != expected_params:
                    raise ValueError("Parent model recipe differs")
                if rec["actual_iterations"] != 300 or rec["roundtrip_max_abs_raw_difference"] > 1e-12:
                    raise ValueError("Parent fit iterations/roundtrip differs")
                if arm == "pace" and rec["recipe"]["weights"] is not None:
                    raise ValueError("Pace parent must be unweighted")
                paths.extend([fit / "receipt.json", fit / "predictions.parquet"])
    for frame in frames.values():
        pd.testing.assert_frame_equal(baseline[prior.models.PRED_KEYS], frame[prior.models.PRED_KEYS])
    return frames, paths


BLEND = dict(arm="half_blend", parents=["baseline", "pace"], weights=[0.5, 0.5],
             parent_ensemble="arithmetic raw probability mean of seeds1..15 FIRST",
             formula="0.5*p_baseline77 + 0.5*p_unweighted_pace91",
             normalize_before_ev=False, tune_weights=False)
POLICY = dict(raw_ev_gt=1.2, budget_yen_per_race=1000, stake_unit_yen=100, max_horses=10,
              allocation="equal floor(10/n)100yen units; remaining and skipped budget staycash")
BOOTSTRAP = dict(repetitions=4000, seed=20261025, family_size=3,
                 unit="all common eligible race dates including no-bet dates", adjustment="Bonferroni separately per metric")


def verify(out):
    prior = module("roi147_verify_prior", HERE.parent / "roi_improvement_143/report.py")
    path = out / "protocol.json"
    if prior.models.sha(path) != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("Protocol sidecar differs")
    doc = json.loads(path.read_text())
    expected = dict(feature="147-fixed-half-blend", version=1, blend=BLEND, policy=POLICY,
        bootstrap=BOOTSTRAP, contrasts=CONTRASTS, seeds=list(range(1, 16)), new_fits=0,
        window=dict(training_from=2007, through="2026-06-26", model_outer=list(range(2019, 2027))),
        can_adopt=False, eligible_for_verdict=False, untouched_holdout=False, proposal2="held")
    for key, value in expected.items():
        if prior.models.canonical(doc.get(key)) != prior.models.canonical(value):
            raise ValueError(f"Protocol constraint differs: {key}")
    hashes = doc["inputs_and_source_sha256"]
    for name in ("run.py", "report.py"):
        if str((HERE / name).relative_to(ROOT)) not in hashes:
            raise ValueError("Protocol source binding missing")
    for name, digest in hashes.items():
        dependency = (ROOT / name).resolve()
        if not dependency.is_relative_to(ROOT) or prior.models.sha(dependency) != digest:
            raise ValueError(f"Input/source changed: {name}")
    return doc


def freeze(out):
    _, inputs = references()
    prior = module("roi147_freeze_prior", HERE.parent / "roi_improvement_143/report.py")
    old = json.loads((PREVIOUS / "protocol.json").read_text())
    paths = [ROOT / name for name in old["inputs_and_source_sha256"]]
    paths += [*inputs, *HERE.glob("*.py"), PREVIOUS / "protocol.json", PREVIOUS / "protocol.sha256",
        PREVIOUS / "features_comparison/summary.json", PREVIOUS / "weighted_comparison/summary.json",
        PREVIOUS / "features_comparison/pace_ensemble.parquet",
        *(PREVIOUS / f"weighted_comparison/{a}_ensemble.parquet" for a in ("current_reference", "baseline", "weighted_baseline"))]
    doc = dict(feature="147-fixed-half-blend", version=1, frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        blend=BLEND, policy=POLICY, bootstrap=BOOTSTRAP, contrasts=CONTRASTS,
        seeds=list(range(1, 16)), new_fits=0, reused_control_fits=360,
        window=dict(training_from=2007, through="2026-06-26", model_outer=list(range(2019, 2027))),
        proposal2="held", can_adopt=False, eligible_for_verdict=False, untouched_holdout=False,
        sensitivity=dict(known_influence_race="202608030111", same_calendar_through="06-21",
            top_difference_race="retrospective sensitivity only; never a trading exclusion"),
        inputs_and_source_sha256={str(p.relative_to(ROOT)): prior.models.sha(p) for p in sorted(set(paths))},
        limitations=["Previously examined historical development data; no untouched holdout.",
            "Stored closing odds imply hypothetical settlement, not executable decision-time profits.",
            "Official win payout archive has no coverage in the fixed primary window (142audit).",
            "Half blend weights fixed before scoring; no extra fitting, search, market mixture or pre-EV race normalization.",
            "Day bootstrap is conditional on savedseeds and does not remove prior repeated historical model selection.",
            "Currentreference and refitted77 baseline differ slightly due to train-only categories.",
            "Within-day drawdown order is race_id, not verified chronological post times."])
    out.mkdir(parents=True, exist_ok=True)
    write(out / "protocol.json", doc)
    (out / "protocol.sha256").write_text(prior.models.sha(out / "protocol.json")+"\n")
    return verify(out)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["freeze", "verify"])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    freeze(args.out) if args.command == "freeze" else verify(args.out)
    print(json.dumps(dict(command=args.command, out=str(args.out), status="complete")), flush=True)


if __name__ == "__main__":
    main()


