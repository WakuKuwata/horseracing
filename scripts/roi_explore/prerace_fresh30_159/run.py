"""User-specified 30-minute freshness restriction of the frozen 158 paired study."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/159-prerace-fresh30"
SPEC = ROOT / "specs/159-prerace-fresh30"
PARENT = ROOT / "artifacts/158-prerace-roi-comparison"
PARENT_SOURCE = ROOT / "scripts/roi_explore/prerace_compare_158/run.py"
TEST = ROOT / "scripts/tests/test_prerace_fresh30_159.py"
CONFIG = dict(feature=159, version=1, source_study=158, maximum_quote_age_seconds=1800,
    rule="0 <= decision_at - quote_observed_at <= 1800 seconds; entire race",
    decision_at="same recorded MEV computed_at as 158", new_collection=False, database_access=False,
    new_fit=False, new_inference=False, closing_odds_in_decision=False,
    policy="same 158 fixed coefficients; strict EV>1; flat100; cap10; budget1000",
    settlement="same official payout table, joined after both decisions",
    bootstrap="same paired day 4000/seed20261026; undefined ROI interval remains null",
    conclusion="保留: 既存34レース・最大4開催日の部分集合として記述比較のみ",
    can_adopt=False, eligible_for_verdict=False, untouched_holdout=False)


def load_parent():
    spec = importlib.util.spec_from_file_location("fresh159_parent158", PARENT_SOURCE)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


parent = load_parent()


def _naive_time(value):
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return False
    return bool(pd.notna(stamp) and stamp.tzinfo is None)


def freshness_filter(judgments):
    """Recompute age from timestamps, never truncate the horse population of a race."""
    required = {"race_id", "horse_id", "horse_number", "field_size", "race_date",
                "quote_observed_at", "decision_at", "post_time"}
    if not required <= set(judgments):
        raise ValueError("Missing freshness population fields")
    if judgments.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("Duplicate horse identity")
    selected, records = [], []
    for rid, group in judgments.groupby("race_id", sort=True):
        if (group.horse_id.isna().any() or group.horse_number.isna().any()
                or group.horse_number.duplicated().any() or not group.field_size.eq(len(group)).all()):
            raise ValueError("Incomplete/ambiguous full race field")
        fields = ["quote_observed_at", "decision_at", "post_time"]
        times = {key: pd.to_datetime(group[key], utc=True, errors="coerce") for key in fields}
        reason, age = "accepted", None
        if group.race_date.nunique(dropna=False) != 1:
            raise ValueError("Mixed race date")
        if any(group[key].map(_naive_time).any() for key in fields):
            reason = "ambiguous_time"
        elif any(value.isna().any() for value in times.values()):
            reason = "missing_time"
        elif any(value.nunique() != 1 for value in times.values()):
            reason = "mixed_race_time"
        else:
            quote, decision, post = (times[key].iloc[0] for key in fields)
            elapsed = decision - quote
            age = elapsed / pd.Timedelta(seconds=1)
            if decision >= post:
                reason = "decision_not_prestart"
            elif elapsed < pd.Timedelta(0):
                reason = "quote_after_decision"
            elif elapsed > pd.Timedelta(seconds=1800):
                reason = "quote_older_than_30min"
        records.append(dict(race_id=rid, race_date=group.race_date.iloc[0], horses=len(group),
            quote_age_seconds=age, reason=reason))
        if reason == "accepted":
            entire = group.copy()
            entire["quote_age_seconds"] = age
            selected.append(entire)
    result = (pd.concat(selected, ignore_index=True) if selected
              else judgments.iloc[:0].copy().reset_index(drop=True))
    return result, pd.DataFrame(records, columns=[
        "race_id", "race_date", "horses", "quote_age_seconds", "reason"])


def prepare():
    parent.verify()
    destination = OUT / "prepared"
    if destination.exists():
        raise FileExistsError("Never overwrite prepared data")
    source = pd.read_parquet(PARENT / "prepared/judgments.parquet")
    fresh, ledger = freshness_filter(source)
    destination.mkdir(parents=True)
    fresh.to_parquet(destination / "judgments.parquet", index=False)
    fresh.to_csv(destination / "judgments.csv", index=False)
    ledger.to_csv(destination / "freshness_ledger.csv", index=False)
    parent.write(destination / "coverage.json", dict(source_races=int(source.race_id.nunique()),
        races=int(fresh.race_id.nunique()), horses=len(fresh), days=int(fresh.race_date.nunique()),
        date_from=fresh.race_date.min() if len(fresh) else None,
        date_through=fresh.race_date.max() if len(fresh) else None,
        reasons=ledger.reason.value_counts().to_dict(),
        races_by_day=fresh.groupby("race_date").race_id.nunique().to_dict(),
        minimum_quote_age_seconds=float(fresh.quote_age_seconds.min()) if len(fresh) else None,
        maximum_quote_age_seconds=float(fresh.quote_age_seconds.max()) if len(fresh) else None))
    print(f"PREPARED {fresh.race_id.nunique()} races / {len(fresh)} horses", flush=True)


def freeze():
    parent.verify()
    path = OUT / "protocol.json"
    if path.exists():
        raise FileExistsError("Never overwrite protocol")
    inherited = json.loads((PARENT / "protocol.json").read_text())["inputs_and_source_sha256"]
    additional = [Path(__file__), TEST, SPEC / "spec.md", SPEC / "plan.md",
        PARENT / "protocol.json", PARENT / "protocol.sha256", *list((OUT / "prepared").iterdir())]
    hashes = {**inherited, **{str(p.relative_to(ROOT)): parent.sha(p) for p in additional}}
    parent.write(path, dict(config=CONFIG, frozen_at=dt.datetime.now(dt.timezone.utc),
        inputs_and_source_sha256=hashes, parent_protocol_sha256=parent.sha(PARENT / "protocol.json")))
    (OUT / "protocol.sha256").write_text(parent.sha(path) + "\n")
    print(f"FROZEN {len(hashes)} files", flush=True)


def verify():
    path = OUT / "protocol.json"
    if parent.sha(path) != (OUT / "protocol.sha256").read_text().strip():
        raise ValueError("Protocol changed")
    document = json.loads(path.read_text())
    if document["config"] != CONFIG:
        raise ValueError("Freshness comparison rules changed")
    for name, checksum in document["inputs_and_source_sha256"].items():
        if parent.sha(ROOT / name) != checksum:
            raise ValueError(f"Frozen input/source changed: {name}")


def run():
    verify()
    destination = OUT / "comparison"
    if destination.exists():
        raise FileExistsError("Never overwrite a comparison")
    fresh = pd.read_parquet(OUT / "prepared/judgments.parquet")
    coverage = json.loads((OUT / "prepared/coverage.json").read_text())
    destination.mkdir()
    if fresh.empty:
        parent.write(destination / "summary.json", dict(config=CONFIG, coverage=coverage,
            conclusion=CONFIG["conclusion"], arms=None, paired=None))
        return
    coefficients = json.loads(parent.COEFFICIENTS.read_text())["coefficients"]["2026"]
    decisions = parent.decide(fresh, coefficients)
    decisions.to_parquet(destination / "decisions.parquet", index=False)
    decisions.to_csv(destination / "decisions.csv", index=False)
    # No results are loaded until both arms' selections have been materialized.
    settlements = pd.read_parquet(PARENT / "prepared/settlements.parquet")
    settlements = settlements.loc[settlements.race_id.isin(fresh.race_id)].copy()
    frame, accounts, summaries = parent.settle_all(decisions, settlements)
    contrast = parent.ev.paired(accounts["roi_only"], accounts["plus_prediction"]) if coverage["days"] >= 2 else None
    for arm, races in accounts.items():
        races.to_parquet(destination / (arm + "_races.parquet"), index=False)
        frame.loc[frame[arm + "_selected"]].to_csv(destination / (arm + "_tickets.csv"), index=False)
    frame.to_parquet(destination / "settled.parquet", index=False)
    frame.to_csv(destination / "settled.csv", index=False)
    combined = accounts["roi_only"].merge(accounts["plus_prediction"], on=["race_id", "race_date"],
        suffixes=("_roi", "_plus"), validate="one_to_one")
    combined.to_csv(destination / "paired_races.csv", index=False)
    result = dict(config=CONFIG, coverage=coverage, protocol_sha256=parent.sha(OUT / "protocol.json"),
        arms=summaries, paired=contrast,
        changed_decisions=int(frame.roi_only_selected.ne(frame.plus_prediction_selected).sum()),
        winner_nll_difference=summaries["plus_prediction"]["winner_nll"] - summaries["roi_only"]["winner_nll"],
        conclusion=CONFIG["conclusion"],
        equal_results_do_not_establish_equivalence=True)
    parent.write(destination / "summary.json", result)
    print(json.dumps(parent.clean(result), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["prepare", "freeze", "verify", "run"])
    args = parser.parse_args()
    dict(prepare=prepare, freeze=freeze, verify=verify, run=run)[args.command]()
