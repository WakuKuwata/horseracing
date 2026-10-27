"""Freeze old-OOF mentor and subsequent weighted comparison without changing prior studies."""
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
OUT = ROOT / "artifacts/146-old-oof-backfill"
PREVIOUS = ROOT / "artifacts/143-existing-feature-roi"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
OLD_WEIGHTS = PREVIOUS / "weights.parquet"
WEIGHTS = OUT / "weights.parquet"
CONTRASTS = [["weighted_backfill", a] for a in ("baseline", "weighted_baseline")]


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
    parent_report = module("roi146_reference143_report", HERE.parent / "roi_improvement_143/report.py")
    original = json.loads((PREVIOUS / "protocol.json").read_text())
    parent_report.models.verify_protocol(PREVIOUS / "protocol.json", ROWS,
        PREVIOUS / "data/additions.parquet", OLD_WEIGHTS)
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


def validate_teacher(frame, years):
    columns = ["race_id", "horse_id", "race_date", "year", "p_oof", "oof_train_through"]
    if list(frame.columns) != columns or frame.isna().any().any() or frame.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("Teacher schema/keys differ")
    if sorted(frame.year.unique()) != list(years) or not frame.oof_train_through.eq(frame.year - 1).all():
        raise ValueError("Teacher chronology differs")
    if not np.isfinite(frame.p_oof).all() or not frame.p_oof.between(0, 1).all():
        raise ValueError("Teacher probability differs")
    if not frame.race_date.str[:4].eq(frame.year.astype(str)).all() or not frame.race_id.str[:4].eq(frame.year.astype(str)).all():
        raise ValueError("Teacher year/date differs")
    return frame


def compose_weights(historical, old):
    """Append old raw probability means while preserving original rows exactly."""
    validate_teacher(historical, range(2013, 2019))
    validate_teacher(old, range(2019, 2026))
    historical = historical.astype(old.dtypes.to_dict())
    result = pd.concat([historical, old], ignore_index=True)
    validate_teacher(result, range(2013, 2026))
    preserved = result.loc[result.year.ge(2019)].reset_index(drop=True)
    original = old.reset_index(drop=True)
    pd.testing.assert_frame_equal(preserved, original, check_exact=True)
    return result


def verify_marker(out, stage, models):
    path = out / ("mentor_models_complete.json" if stage == "mentor" else "models_complete.json")
    marker = json.loads(path.read_text())
    protocol = out / ("mentor_protocol.json" if stage == "mentor" else "protocol.json")
    count = 90 if stage == "mentor" else 120
    if marker["stage"] != stage or marker["jobs"] != count or marker["fits"] != count or len(marker["hashes"]) != 3*count:
        raise ValueError("Completion inventory differs")
    if marker["protocol_sha256"] != models.sha(protocol):
        raise ValueError("Completion protocol differs")
    for name, digest in marker["hashes"].items():
        p = (out / name).resolve()
        if not p.is_relative_to(out.resolve()) or models.sha(p) != digest:
            raise ValueError("Completion artifact changed")
    return marker


def mentor_ensemble(out):
    experiment = module("roi146_mentor_experiment", HERE / "experiment.py")
    models = experiment.models
    proto, paths = experiment.configuration(out, "mentor")
    marker = verify_marker(out, "mentor", models)
    expected = experiment.expected_recipes(paths, models.MENTOR_YEARS, "mentor")
    pieces, details = [], []
    for year in models.MENTOR_YEARS:
        first, total = None, None
        for seed in proto["seeds"]:
            args = experiment.job_args(out, year, seed, paths, "mentor")
            experiment.verify_completed(args, proto, expected[year])
            fit = args.out / "baseline"
            rec = json.loads((fit / "receipt.json").read_text())
            frame = pd.read_parquet(fit / "predictions.parquet")
            if not frame.year.eq(year).all() or not np.isfinite(frame.p).all() or not frame.p.between(0, 1).all():
                raise ValueError("Mentor target differs")
            if first is None:
                first, total = frame[models.PRED_KEYS].copy(), np.zeros(len(frame))
            elif not first.equals(frame[models.PRED_KEYS]):
                raise ValueError("Mentor seed population differs")
            total += frame.p.to_numpy(float)
            details.append(dict(year=year, seed=seed, elapsed_s=rec["elapsed_s"], receipt_sha256=models.sha(fit / "receipt.json")))
        first["p_oof"] = total / len(proto["seeds"])
        first["oof_train_through"] = year - 1
        pieces.append(first[["race_id", "horse_id", "race_date", "year", "p_oof", "oof_train_through"]])
    result = pd.concat(pieces, ignore_index=True)
    validate_teacher(result, models.MENTOR_YEARS)
    if len(result) != 287252 or result.race_id.nunique() != 19935:
        raise ValueError("Historical teacher population differs")
    return result, details, marker


def prepare_weights(out):
    if (out / "weights.parquet").exists() or (out / "weights_manifest.json").exists():
        raise FileExistsError("Prepared teacher outputs already exist")
    historical, details, marker = mentor_ensemble(out)
    old = pd.read_parquet(OLD_WEIGHTS)
    combined = compose_weights(historical, old)
    if len(old) != 321271 or len(combined) != 608523:
        raise ValueError("Merged teacher population differs")
    combined.to_parquet(out / "weights.parquet", index=False)
    models = module("roi146_prepare_models", HERE / "models.py")
    write(out / "weights_manifest.json", dict(rows=len(combined), new_rows=len(historical), original_rows=len(old),
        rows_by_year={str(k): int(v) for k, v in combined.groupby("year").size().items()},
        mentor_protocol_sha256=models.sha(out / "mentor_protocol.json"),
        mentor_completion_sha256=models.sha(out / "mentor_models_complete.json"),
        original_weights_sha256=models.sha(OLD_WEIGHTS), weights_sha256=models.sha(out / "weights.parquet"),
        mentor_fits=details, mentor_wall_seconds=marker["elapsed_s"],
        mentor_worker_seconds=sum(r["elapsed_s"] for r in details),
        original_values_preserved_exactly=True, neutral_years=list(range(2007, 2013)),
        source="2013..18 baseline77 raw-p mean15 plus exact original143 2019..25 teacher",
        can_adopt=False, untouched_holdout=False))


def freeze_mentor(out):
    models = module("roi146_freeze_models", HERE / "models.py")
    _, reference_inputs = references()
    previous = json.loads((PREVIOUS / "protocol.json").read_text())
    paths = [ROOT / name for name in previous["inputs_and_source_sha256"]]
    paths += [ROWS, OLD_WEIGHTS, PREVIOUS / "protocol.json", PREVIOUS / "protocol.sha256",
        PREVIOUS / "weighted_comparison/summary.json", *reference_inputs, *HERE.glob("*.py"),
        *(HERE.parent / "roi_improvement_143" / name for name in ("models.py", "report.py", "run.py")),
        *(PREVIOUS / f"weighted_comparison/{a}_ensemble.parquet" for a in ("current_reference", "baseline", "weighted_baseline"))]
    document = dict(feature="146-old-oof-backfill", version=1, stage="mentor",
        frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(), rows=str(ROWS.relative_to(ROOT)),
        window=dict(training_from=2007, through="2026-06-26", model_outer=list(range(2013, 2019))),
        mentor_years=list(range(2013, 2019)), candidate_years=list(range(2019, 2027)),
        seeds=list(range(1, 16)), max_workers=2, threads_each=1, arms=["baseline"],
        models=dict(objective="binary", rounds=300, params=models.base.BASE_PARAMS),
        features=models.base.FEATURES, weight_recipe=models.parent.WEIGHT_RECIPE,
        candidate_plan=dict(arm="weighted_backfill", kernel_arm="weighted_baseline", new_mentor_years=list(range(2013, 2019)),
            original_mentor_years=list(range(2019, 2026)), neutral_years=list(range(2007, 2013)),
            teacher="raw probability mean15 before EV; old2019..25 unchanged; no weighting of mentor models",
            weights="exact143 formula on complete OOF races, normalize informative rows together in each annual train; unavailable exactly1",
            no_additional_features=True, no_within_race_normalization=True),
        policy=dict(raw_ev_gt=1.2, budget_yen_per_race=1000, stake_unit_yen=100, max_horses=10,
            allocation="equal floor(10/n)100yen units; remaining and skipped budget staycash"),
        contrasts=CONTRASTS, bootstrap=dict(repetitions=4000, seed=20261025, family_size=2,
            unit="all common eligible race dates including no-bet dates", adjustment="Bonferroni separately per metric"),
        sensitivity=dict(same_calendar_through="06-21", known_influence_race="202608030111",
            top_difference_race="retrospective sensitivity only; never a trading exclusion"),
        scope="Proposal3 only: strict annual2013..18 mentor backfill; fixed77-feature original weighting; no144/145combination or search",
        inputs_and_source_sha256={str(p.relative_to(ROOT)): models.sha(p) for p in sorted(set(paths))},
        can_adopt=False, eligible_for_verdict=False, untouched_holdout=False,
        limitations=["Previously examined historical development data; no untouched holdout.",
            "Fixed historical recipe was chosen in later research; annual training is past-only but configuration was not historically pre-registered.",
            "Stored closing odds imply hypothetical settlement, not executable decision-time profits.",
            "Official win payout archive has no coverage in the fixed primary window (142audit).",
            "2007..2012 remain neutral; coverage expansion and changed informative normalization denominator occur together.",
            "2019 now has nonneutral historical weights and must not be treated as baseline parity control.",
            "Day bootstrap is conditional on savedseeds and does not remove prior repeated historical model selection.",
            "Currentreference and refitted77 baseline differ slightly due to train-only categories.",
            "Within-day drawdown order is race_id, not verified chronological post times."])
    out.mkdir(parents=True, exist_ok=True)
    write(out / "mentor_protocol.json", document)
    (out / "mentor_protocol.sha256").write_text(models.sha(out / "mentor_protocol.json") + "\n")
    models.verify_protocol(out / "mentor_protocol.json", ROWS)


def freeze_candidate(out):
    models = module("roi146_freeze_candidate_models", HERE / "models.py")
    old = models.verify_protocol(out / "mentor_protocol.json", ROWS)
    historical, _, _ = mentor_ensemble(out)
    expected = compose_weights(historical, pd.read_parquet(OLD_WEIGHTS))
    pd.testing.assert_frame_equal(expected, pd.read_parquet(out / "weights.parquet"), check_exact=True)
    manifest = json.loads((out / "weights_manifest.json").read_text())
    if manifest["weights_sha256"] != models.sha(out / "weights.parquet") or manifest["original_weights_sha256"] != models.sha(OLD_WEIGHTS):
        raise ValueError("Teacher manifest differs")
    doc = dict(old)
    doc.update(stage="candidate", frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(), arms=["weighted_backfill"],
        window=dict(old["window"], model_outer=list(range(2019, 2027))), weights=str((out / "weights.parquet").relative_to(ROOT)),
        mentor_protocol_sha256=models.sha(out / "mentor_protocol.json"))
    paths = [out / name for name in ("mentor_protocol.json", "mentor_protocol.sha256", "mentor_models_complete.json", "weights_manifest.json", "weights.parquet")]
    for year in range(2013, 2019):
        for seed in range(1, 16):
            fit = out / "mentor_models" / f"seed_{seed:02d}/year_{year}/baseline"
            paths.extend([fit / "receipt.json", fit / "predictions.parquet"])
    doc["inputs_and_source_sha256"] = dict(old["inputs_and_source_sha256"], **{str(p.relative_to(ROOT)): models.sha(p) for p in paths})
    write(out / "protocol.json", doc)
    (out / "protocol.sha256").write_text(models.sha(out / "protocol.json") + "\n")
    models.verify_protocol(out / "protocol.json", ROWS, out / "weights.parquet")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["freeze-mentor", "prepare-weights", "freeze-candidate", "verify"])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    if args.command == "freeze-mentor":
        freeze_mentor(args.out)
    elif args.command == "prepare-weights":
        prepare_weights(args.out)
    elif args.command == "freeze-candidate":
        freeze_candidate(args.out)
    else:
        models = module("roi146_verify_models", HERE / "models.py")
        models.verify_protocol(args.out / "mentor_protocol.json", ROWS)
        if (args.out / "protocol.json").exists():
            models.verify_protocol(args.out / "protocol.json", ROWS, args.out / "weights.parquet")
    print(json.dumps(dict(command=args.command, out=str(args.out), status="complete")), flush=True)


if __name__ == "__main__":
    main()


