"""Compare recorded pre-race decisions; official settlement joins only after selection."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/158-prerace-roi-comparison"
SPEC = ROOT / "specs/158-prerace-roi-comparison"
SNAPSHOT = OUT / "snapshot"
COEFFICIENTS = ROOT / "artifacts/151-roi-prediction-ablation/comparison/summary.json"
EVALUATION = ROOT / "scripts/roi_explore/verified_recompare_157/evaluation.py"
CONFIG = dict(feature=158, version=1, new_external_collection=False, database_write=False,
    new_fits=0, new_inference=0, closing_odds_in_decision=False,
    decision_at="recorded MEV computed_at; publication/commit completion not retained",
    price="MEV odds_used; quote source; observed<=computed<post; complete same-run field",
    old_prediction="latest eligible stored preweight run available by decision, without replay fallback",
    coefficients="fixed 151 2026 coefficients fit through 2025-12-28",
    policy=dict(threshold=1., strict=True, stake=100, cap=10, budget=1000),
    settlement="official stored netkeiba_result payout; separate post-decision table",
    bootstrap=dict(repetitions=4000, seed=20261026, unit="race day", financial_family=2,
        quantiles=[.0125, .9875], minimum_days=2, undefined_roi="null interval, no replicate dropping"),
    can_adopt=False, eligible_for_verdict=False, untouched_holdout=False)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


ev = load_module(EVALUATION, "prerace158_evaluation")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    return value


def write(path, value):
    Path(path).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def decide(judgments, coefficients):
    """Explicit input whitelist: closing prices, outcomes and payouts cannot enter selection."""
    columns = ["race_id", "horse_id", "horse_number", "race_date", "post_time", "decision_at",
               "judgment_odds", "q", "p_mev", "p_pred_preweight"]
    frame = judgments[columns].copy().reset_index(drop=True)
    if frame.empty or frame.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("Empty/duplicate decision population")
    post = pd.to_datetime(frame.post_time, utc=True, errors="coerce")
    when = pd.to_datetime(frame.decision_at, utc=True, errors="coerce")
    if post.isna().any() or when.isna().any() or not when.lt(post).all():
        raise ValueError("Decision is not before post")
    # Alias only the proven historical input price. No current RaceHorse price is loaded.
    frame["odds"] = frame.judgment_odds
    for arm, prob in ev.probabilities(frame, coefficients).items():
        frame[arm] = prob
        frame[arm + "_selected"], frame[arm + "_ev"] = ev.select_tickets(frame, prob)
    return frame


def settle_all(decisions, settlements):
    """Official winners and payments are read only after both decisions are complete."""
    keys = ["race_id", "horse_id"]
    labels = settlements[keys + ["won", "official_payout_yen"]]
    if set(map(tuple, labels[keys].to_numpy())) != set(map(tuple, decisions[keys].to_numpy())):
        raise ValueError("Settlement population differs")
    frame = decisions.merge(labels, on=keys, validate="one_to_one")
    accounts, summaries = {}, {}
    for arm in ev.ARMS:
        accounts[arm] = ev.account(frame, frame[arm + "_selected"])
        summaries[arm] = ev.summarize(accounts[arm], frame, frame[arm])
    return frame, accounts, summaries


def prepare():
    loader = load_module(HERE / "inputs.py", "prerace158_inputs")
    destination = OUT / "prepared"
    if destination.exists():
        raise FileExistsError("Never overwrite a prepared study")
    judgments, settlements, audit = loader.load_inputs(SNAPSHOT)
    destination.mkdir(parents=True)
    judgments.to_parquet(destination / "judgments.parquet", index=False)
    settlements.to_parquet(destination / "settlements.parquet", index=False)
    judgments.to_csv(destination / "judgments.csv", index=False)
    settlements.to_csv(destination / "settlements.csv", index=False)
    write(destination / "input_audit.json", audit)
    write(destination / "coverage.json", dict(comparable_races=int(judgments.race_id.nunique()),
        horses=len(judgments), days=int(judgments.race_date.nunique()),
        date_from=judgments.race_date.min() if len(judgments) else None,
        date_through=judgments.race_date.max() if len(judgments) else None))
    print(f"PREPARED {judgments.race_id.nunique()} races / {len(judgments)} horses", flush=True)


def freeze():
    path = OUT / "protocol.json"
    if path.exists():
        raise FileExistsError("Never overwrite a protocol")
    loader = load_module(HERE / "inputs.py", "prerace158_inputs")
    paths = sorted(set([*loader.input_paths(SNAPSHOT), *HERE.glob("*.py"), EVALUATION, COEFFICIENTS,
        SPEC / "spec.md", SPEC / "plan.md", ROOT / "scripts/tests/test_prerace_compare_158.py",
        *list((OUT / "prepared").glob("*"))]))
    write(path, dict(config=CONFIG, frozen_at=dt.datetime.now(dt.timezone.utc),
        inputs_and_source_sha256={str(p.relative_to(ROOT)): sha(p) for p in paths},
        runtime=dict(python=sys.version, numpy=np.__version__, pandas=pd.__version__)))
    (OUT / "protocol.sha256").write_text(sha(path) + "\n")
    print(f"FROZEN {len(paths)} files", flush=True)


def verify():
    path = OUT / "protocol.json"
    if sha(path) != (OUT / "protocol.sha256").read_text().strip():
        raise ValueError("Protocol changed")
    document = json.loads(path.read_text())
    if document["config"] != CONFIG:
        raise ValueError("Comparison rules changed")
    for name, checksum in document["inputs_and_source_sha256"].items():
        if sha(ROOT / name) != checksum:
            raise ValueError(f"Frozen material changed: {name}")


def run():
    verify()
    destination = OUT / "comparison"
    if destination.exists():
        raise FileExistsError("Never overwrite a comparison")
    judgments = pd.read_parquet(OUT / "prepared/judgments.parquet")
    coefficients = json.loads(COEFFICIENTS.read_text())["coefficients"]["2026"]
    destination.mkdir()
    coverage = json.loads((OUT / "prepared/coverage.json").read_text())
    if judgments.empty:
        write(destination / "summary.json", dict(config=CONFIG, coverage=coverage,
            feasibility="No common provable as-of decision population", arms=None, paired=None))
        return
    decisions = decide(judgments, coefficients)
    decisions.to_parquet(destination / "decisions.parquet", index=False)
    decisions.to_csv(destination / "decisions.csv", index=False)
    # The settlement table is first read here, after all prices/probabilities/choices are fixed.
    settlements = pd.read_parquet(OUT / "prepared/settlements.parquet")
    frame, accounts, summaries = settle_all(decisions, settlements)
    contrast = None
    if coverage["days"] >= CONFIG["bootstrap"]["minimum_days"]:
        contrast = ev.paired(accounts["roi_only"], accounts["plus_prediction"])
    result = dict(config=CONFIG, protocol_sha256=sha(OUT / "protocol.json"), coverage=coverage,
        feasibility="Common pre-race-price comparison possible at recorded MEV compute time",
        publication_time_proven=False, arms=summaries, paired=contrast,
        winner_nll_difference=summaries["plus_prediction"]["winner_nll"] - summaries["roi_only"]["winner_nll"],
        changed_decisions=int(frame.roi_only_selected.ne(frame.plus_prediction_selected).sum()),
        utility_verdict="Feasibility demonstrated; too few dates and no untouched holdout to establish added value")
    for arm, races in accounts.items():
        races.to_parquet(destination / (arm + "_races.parquet"), index=False)
        frame.loc[frame[arm + "_selected"]].to_csv(destination / (arm + "_tickets.csv"), index=False)
    frame.to_parquet(destination / "settled.parquet", index=False)
    frame.to_csv(destination / "settled.csv", index=False)
    write(destination / "summary.json", result)
    print(json.dumps(clean(result), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["prepare", "freeze", "verify", "run"])
    args = parser.parse_args()
    dict(prepare=prepare, freeze=freeze, verify=verify, run=run)[args.command]()
