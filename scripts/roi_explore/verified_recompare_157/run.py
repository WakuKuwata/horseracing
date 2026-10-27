"""Rebuild, freeze, and compare two fixed stacks on verified stored settlement evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import lightgbm
import numpy as np
import pandas as pd
import threadpoolctl
from horseracing_training import market_ev

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/157-verified-roi-comparison"
SPEC = ROOT / "specs/157-verified-roi-comparison"
COEFFICIENTS = ROOT / "artifacts/151-roi-prediction-ablation/comparison/summary.json"
CONFIG = dict(feature=157, version=1, new_external_collection=False, database_write=False,
    new_booster_fits=0, blend_refits=0, primary="plus_prediction minus roi_only",
    settlement="official stored netkeiba_result payout; complete verified race only",
    decision_prices="verified final odds; closing-price counterfactual",
    legacy_model="mix-129-nj6", legacy_regime="preweight", legacy_train_through="2026-08-23",
    mev_model="mev-ens15-v1", mev_train_through=2025,
    coefficient_source="151 2026 common 2020-2025 calibration, fit through 2025-12-28",
    policy=dict(threshold=1., strict=True, stake=100, cap=10, budget=1000),
    bootstrap=dict(repetitions=4000, seed=20261026, unit="race day", financial_family=2,
        interval_quantiles=[.0125, .9875], undefined_roi_replicates="retain and make ROI interval unavailable"),
    can_adopt=False, eligible_for_verdict=False, untouched_holdout=False)


def module(name):
    spec = importlib.util.spec_from_file_location("verified157_" + name, HERE / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


data = module("data")
legacy = module("legacy")
ev = module("evaluation")


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
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


def prepare():
    """Coverage and material validation only: no comparison probabilities, bets or ROI readout."""
    destination = OUT / "rebuilt"
    if (destination / "population.parquet").exists():
        raise FileExistsError("Prepared population already exists")
    snapshot = data.read_snapshot()
    if (destination / "verified_rows.parquet").exists():
        # The exploratory builder performed the same source/data validation. Verify exact
        # reconstruction again before using these already-built expensive history features.
        verified, ledger = data.verified_dataset(snapshot)
        saved = pd.read_parquet(destination / "verified_rows.parquet")
        pd.testing.assert_frame_equal(verified, saved)
        features = pd.read_parquet(destination / "features.parquet")
    else:
        verified, ledger = data.verified_dataset(snapshot)
        features, audit = data.build_features(snapshot, verified)
        destination.mkdir(parents=True, exist_ok=False)
        verified.to_parquet(destination / "verified_rows.parquet", index=False)
        features.to_parquet(destination / "features.parquet", index=False)
        write(destination / "build_audit.json", audit)
    cached, cache_audit = legacy.load_predictions()
    database, db_audit = legacy.load_database_predictions(data.SNAPSHOT)
    saved, combined = legacy.combine_predictions(database, cached)
    stored = data.by_race(saved)
    coverage = []
    accepted = []
    for rid, group in verified.groupby("race_id", sort=True):
        reason = "accepted"
        pred = stored.get(rid)
        if group.track_type.iloc[0] == "障":
            reason = "jump_not_in_mev_scope"
        elif group.dead_heat.any():
            reason = "dead_heat_not_in_151_primary"
        elif group.race_date.iloc[0] <= CONFIG["legacy_train_through"]:
            reason = "legacy_model_training_period"
        elif pred is None:
            reason = "no_proven_preweight_prediction"
        elif set(group.horse_id) != set(pred.horse_id) or len(group) != len(pred):
            reason = "legacy_full_field_mismatch"
        else:
            data.require(pred.race_date.eq(group.race_date.iloc[0]).all(), "Legacy date differs")
            joined = group.merge(pred.drop(columns="race_date"), on=["race_id", "horse_id"],
                                 validate="one_to_one")
            q = features.loc[features.race_id.eq(rid), ["race_id", "horse_id", "q"]]
            joined = joined.merge(q, on=["race_id", "horse_id"], validate="one_to_one")
            data.require(len(joined) == len(group), "Feature join dropped a horse")
            data.require(np.allclose(joined.q, (1 / joined.odds) / (1 / joined.odds).sum(),
                                     atol=1e-12, rtol=0), "Final market q differs")
            joined["field_size"] = len(group)
            accepted.append(joined)
        coverage.append(dict(race_id=rid, race_date=group.race_date.iloc[0], n_horses=len(group),
            reason=reason, source=None if pred is None else pred.prediction_source.iloc[0]))
    data.require(bool(accepted), "No comparable verified races")
    population = pd.concat(accepted, ignore_index=True).sort_values(
        ["race_date", "post_time", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
    population.to_parquet(destination / "population.parquet", index=False)
    pd.DataFrame(coverage).to_csv(destination / "coverage.csv", index=False)
    saved.to_parquet(destination / "legacy_predictions.parquet", index=False)
    write(destination / "legacy_audit.json", dict(cached=cache_audit, database=db_audit, combined=combined))
    write(destination / "coverage.json", dict(verified_races=int(verified.race_id.nunique()),
        verified_horses=len(verified), verified_flat_races=int(verified.loc[verified.track_type.ne("障"), "race_id"].nunique()),
        comparable_races=int(population.race_id.nunique()), comparable_horses=len(population),
        days=int(population.race_date.nunique()), date_from=population.race_date.min(), date_through=population.race_date.max(),
        exclusions=pd.DataFrame(coverage).reason.value_counts().to_dict(),
        source_races=population.groupby("prediction_source").race_id.nunique().to_dict(),
        original_audit_races=len(ledger)))
    print(f"PREPARED {population.race_id.nunique()} races / {len(population)} horses", flush=True)


def input_paths():
    paths = [*legacy.input_paths(), *HERE.glob("*.py"), SPEC / "spec.md", SPEC / "plan.md",
        ROOT / "scripts/tests/test_verified_recompare_157.py", Path(market_ev.__file__), COEFFICIENTS,
        *list((OUT / "rebuilt").glob("*")),
        data.AUDIT / "output-sha256.json", data.AUDIT / "protocol.json", data.AUDIT / "protocol.sha256",
        data.AUDIT / "audit/races.parquet", data.AUDIT / "audit/archive_pages.parquet",
        data.AUDIT / "audit/archive_horses.parquet", data.AUDIT / "audit/archive_payouts.parquet",
        data.AUDIT / "snapshot/payouts.parquet", data.AUDIT / "snapshot/id_mappings.parquet",
        data.MEV / "ensemble.spec.json", data.MEV / "ensemble_2026.json"]
    paths.extend(data.MEV / r["path"] for r in json.loads((data.MEV / "ensemble_2026.json").read_text())["members"])
    paths.extend(data.MEV / f"seed_{s:02d}/model.spec.json" for s in range(1, 16))
    pages = pd.read_parquet(data.AUDIT / "audit/archive_pages.parquet")
    ids = set(pd.read_parquet(OUT / "rebuilt/verified_rows.parquet", columns=["race_id"]).race_id)
    paths.extend(ROOT / p for p in pages.loc[pages.selected & pages.race_id.isin(ids), "archive_path"])
    return sorted(set(paths))


def freeze():
    path = OUT / "protocol.json"
    if path.exists():
        raise FileExistsError("Never overwrite protocol")
    paths = input_paths()
    write(path, dict(config=CONFIG, frozen_at=dt.datetime.now(dt.timezone.utc),
        inputs_and_source_sha256={str(p.relative_to(ROOT)): data.sha(p) for p in paths},
        runtime=dict(python=sys.version, pandas=pd.__version__, numpy=np.__version__, lightgbm=lightgbm.__version__)))
    (OUT / "protocol.sha256").write_text(data.sha(path) + "\n")
    print(f"FROZEN {len(paths)} files", flush=True)


def verify():
    path = OUT / "protocol.json"
    data.require(data.sha(path) == (OUT / "protocol.sha256").read_text().strip(), "Protocol SHA differs")
    document = json.loads(path.read_text())
    data.require(document["config"] == CONFIG, "Comparison rules changed")
    for name, checksum in document["inputs_and_source_sha256"].items():
        data.require(data.sha(ROOT / name) == checksum, f"Frozen material changed: {name}")


def run():
    verify()
    destination = OUT / "comparison"
    if destination.exists():
        raise FileExistsError("Never overwrite a comparison")
    frame = pd.read_parquet(OUT / "rebuilt/population.parquet")
    features = pd.read_parquet(OUT / "rebuilt/features.parquet")
    with threadpoolctl.threadpool_limits(limits=1):
        mev = data.predict_mev(features)
    frame = frame.merge(mev, on=["race_id", "horse_id"], validate="one_to_one")
    data.require(len(frame) == len(pd.read_parquet(OUT / "rebuilt/population.parquet")), "MEV missing rows")
    coefficients = json.loads(COEFFICIENTS.read_text())["coefficients"]["2026"]
    probabilities = ev.probabilities(frame, coefficients)
    destination.mkdir()
    accounts, summaries = {}, {}
    for arm, p in probabilities.items():
        frame[arm] = p
        selected, expected = ev.select_tickets(frame, p)
        frame[arm + "_selected"] = selected
        frame[arm + "_ev"] = expected
        accounts[arm] = ev.account(frame, selected)
        summaries[arm] = ev.summarize(accounts[arm], frame, p)
        accounts[arm].to_parquet(destination / (arm + "_races.parquet"), index=False)
        frame.loc[selected].to_parquet(destination / (arm + "_bets.parquet"), index=False)
    contrast = ev.paired(accounts["roi_only"], accounts["plus_prediction"])
    nll_diff = summaries["plus_prediction"]["winner_nll"] - summaries["roi_only"]["winner_nll"]
    result = dict(config=CONFIG, protocol_sha256=data.sha(OUT / "protocol.json"),
        coverage=json.loads((OUT / "rebuilt/coverage.json").read_text()),
        feature_rebuild=json.loads((OUT / "rebuilt/build_audit.json").read_text()),
        coefficients={name: coefficients[name] for name in ev.ARMS}, arms=summaries,
        paired=contrast, winner_nll_difference=nll_diff,
        changed_decisions=int((frame.roi_only_selected != frame.plus_prediction_selected).sum()),
        verdict="additional ROI value unconfirmed in this small closing-price comparison")
    lower_roi = contrast["roi_difference_interval"]
    lower_profit = contrast["profit_common_budget_interval"]
    if lower_roi and lower_roi[0] > 0 and lower_profit[0] > 0:
        result["verdict"] = "positive conditional historical comparison; not confirmatory adoption evidence"
    frame.to_parquet(destination / "predictions.parquet", index=False)
    mev.to_parquet(destination / "recomputed_mev.parquet", index=False)
    combined = accounts["roi_only"].merge(accounts["plus_prediction"], on=["race_id", "race_date"], suffixes=("_roi", "_plus"))
    combined.to_csv(destination / "paired_races.csv", index=False)
    write(destination / "summary.json", result)
    print(json.dumps(clean(result), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["prepare", "freeze", "verify", "run"])
    args = parser.parse_args()
    dict(prepare=prepare, freeze=freeze, verify=verify, run=run)[args.command]()
