"""Read-only saved prediction inventory; no fetching, DB, inference or settlement prices."""
from pathlib import Path
import hashlib
import importlib.util
import json
import pickle
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
SNAP = ROOT / "artifacts/158-prerace-roi-comparison/snapshot"
R02 = ROOT / "artifacts/roi_explore/missed_20261004/R02_odds_drift"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source = ROOT / "scripts/roi_explore/prerace_compare_158/inputs.py"
    spec = importlib.util.spec_from_file_location("inventory160_inputs", source)
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    tables = helper._read_snapshot(SNAP)
    bundle, bundle_sha, _ = helper._validate_models(tables["model"])
    races = tables["races"].set_index("race_id").to_dict("index")
    entries = helper._groups(tables["entries"])
    runs = helper._groups(tables["runs"])
    preds = helper._groups(tables["predictions"], "prediction_run_id")
    feats = helper._groups(tables["features"], "prediction_run_id")
    retained = helper._groups(tables["mev"])
    chaos = helper._groups(tables["chaos"])
    paths = {source, Path(__file__), SNAP / "manifest.json", R02 / "db_mev_pending.parquet",
             R02 / "pairs.parquet", R02 / "results.json", R02 / "build_meta.json",
             ROOT / "artifacts/155-saved-odds-settlement-audit/snapshot/mev.parquet",
             ROOT / "artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet"}
    schema_inventory = []
    for path in sorted((ROOT / "artifacts").rglob("*.parquet")):
        if "/models/" in str(path) or "/screen_" in str(path):
            continue
        cols = pq.read_schema(path).names
        if "race_id" in cols and set(cols) & {"computed_at", "odds_observed_at", "odds_used"}:
            schema_inventory.append(dict(path=str(path.relative_to(ROOT)),
                rows=pq.ParquetFile(path).metadata.num_rows, columns=cols))
    old155 = pd.read_parquet(ROOT / "artifacts/155-saved-odds-settlement-audit/snapshot/mev.parquet")
    old155 = old155.loc[old155.race_id.isin(races) & old155.model_version.eq("mev-ens15-v1")]
    current = tables["mev"]
    same155 = old155.sort_values(["race_id", "horse_id"]).reset_index(drop=True).equals(
        current.sort_values(["race_id", "horse_id"])[old155.columns].reset_index(drop=True))
    r02 = pd.read_parquet(R02 / "db_mev_pending.parquet")
    r02 = r02.loc[r02.model_version.eq("mev-ens15-v1")]
    candidates = []
    for rid, group in r02.groupby("race_id", sort=True):
        race = races[rid]
        compute = helper._time(group.computed_at.iloc[0])
        observed = helper._time(group.odds_observed_at.iloc[0])
        post = helper._time(race["post_time"])
        active = entries[rid].loc[entries[rid].entry_status.eq("started")]
        expected = {(h, int(n)) for h, n in zip(active.horse_id, active.horse_number)}
        actual = {(h, int(n)) for h, n in zip(group.horse_id, group.horse_number)}
        matches = []
        for record in chaos.get(rid, pd.DataFrame()).to_dict("records"):
            vector = {x["horse_id"]: (int(x["horse_number"]), helper._decimal(x["odds"]))
                      for x in json.loads(record["field_json"])}
            stored = {h: (int(n), helper._decimal(o))
                      for h, n, o in zip(group.horse_id, group.horse_number, group.odds_used)}
            if vector == stored:
                captured = helper._time(record["captured_at"])
                matches.append(dict(captured_at=str(captured),
                    capture_age_seconds=(compute-captured).total_seconds(),
                    captured_before_compute=bool(captured <= compute),
                    fresh_at_compute=bool(pd.Timedelta(0) <= compute-captured <= pd.Timedelta(minutes=30))))
        eligible = []
        for run in runs.get(rid, pd.DataFrame()).to_dict("records"):
            key = run["prediction_run_id"]
            bad = helper.legacy_flags(run, preds.get(key), feats.get(key), entries[rid], race,
                decision=compute, bundle_sha=bundle_sha, train_through=bundle["train_through"],
                coefficient_train_through=bundle["coefficient_train_through"])
            if not bad:
                eligible.append(key)
        prior = retained[rid].set_index("horse_id").loc[group.horse_id]
        candidates.append(dict(race_id=rid, horses=len(group), computed_at=str(compute),
            proxy_odds_observed_at=str(observed), post_time=str(post),
            full_field=bool(expected == actual and len(group) == len(active)),
            computed_prestart=bool(compute < post), proxy_age_seconds=(compute-observed).total_seconds(),
            proxy_age_within_30m=bool(pd.Timedelta(0) <= compute-observed <= pd.Timedelta(minutes=30)),
            missing_columns=[k for k in ("run_id", "logic_version", "booster_sha256") if k not in group],
            retained_compute_identical=bool(pd.to_datetime(prior.computed_at, utc=True).eq(compute).all()),
            retained_prices_identical=bool(np.array_equal(prior.odds_used.astype(float), group.odds_used.astype(float))),
            retained_probability_max_abs_difference=float(np.max(np.abs(prior.win_prob.astype(float).to_numpy()-group.win_prob.astype(float).to_numpy()))),
            eligible_old_runs_at_compute=eligible, whole_field_chaos_price_matches=matches,
            added_distinct_timepoint=False))
    capture_rows = []
    for path in sorted((ROOT / "artifacts/133-prospective-paired-comparison/records/capture").glob("*.json")):
        paths.add(path)
        record = json.loads(path.read_text())
        stamp = pd.to_datetime(record["regimes"]["preweight"]["predicted_at"]["new"], utc=True)
        available = max(stamp, pd.to_datetime(record["record_written_at"], utc=True))
        post = pd.to_datetime(record["scheduled_start"], utc=True)
        rid = record["race_id"]
        capture_rows.append(dict(race_id=rid, race_day=record["race_day"],
            prediction_at=str(stamp), saved_at=str(available), scheduled_start=str(post),
            prestart=bool(available < post and record["n_result_rows_at_capture"] == 0
                          and record["n_result_rows_after_prediction"] == 0),
            bundle_sha=record["comparators"]["new"]["bundle_manifest_sha256"],
            retained_mev_prestart=bool(rid in retained and
                pd.to_datetime(retained[rid].computed_at, utc=True).lt(post).all())))
    recent = ROOT / "artifacts/135-mixture-reproducibility/recent-owned-54586a4bf86c"
    paths.update({recent / "execution-receipt.json", recent / "input-receipt.json", recent / "predictions.pkl"})
    receipt = json.loads((recent / "execution-receipt.json").read_text())
    predictions = pickle.loads((recent / "predictions.pkl").read_bytes())
    dates = sorted({str(x) for x in predictions["dates"].values()})
    r02_pairs = pd.read_parquet(R02 / "pairs.parquet", columns=["race_id", "pair_id"])
    scored = pd.read_parquet(ROOT / "artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet",
                             columns=["race_id", "pair_id"])
    result = dict(scope="saved files only; no DB/network/inference; coverage only, no ROI rescoring",
        can_adopt=False, eligible_for_verdict=False,
        parquet_time_column_inventory=schema_inventory,
        previous_mev_exports=dict(source155_rows=len(old155), source155_races=old155.race_id.nunique(),
            source155_equals158_all_shared_columns=bool(same155),
            r02_ens_rows=len(r02), r02_ens_races=r02.race_id.nunique(),
            r02_all_computation_times_equal_retained=all(x["retained_compute_identical"] for x in candidates),
            r02_added_distinct_timepoints=0, candidate_details=candidates),
        retrospectively_scored_sources=[dict(path=str((R02/"pairs.parquet").relative_to(ROOT)),
            rows=len(r02_pairs), races=r02_pairs.race_id.nunique(), timepoints=r02_pairs.pair_id.nunique(),
            reason="EVe_J recomputed by Scorer.score(jv) after races; not predecision probability"),
            dict(path="artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet",
                 rows=len(scored), races=scored.race_id.nunique(), timepoints=scored.pair_id.nunique(),
                 reason="derived from R02 pairs; residual/extended p also retrospective")],
        capture133=dict(records=len(capture_rows), days=sorted({r["race_day"] for r in capture_rows}),
            saved_prestart=sum(r["prestart"] for r in capture_rows),
            retained_mev_prestart=sum(r["retained_mev_prestart"] for r in capture_rows),
            details=capture_rows),
        replay135=dict(races=len(predictions["ids"]), dates=dates,
            execution_receipt_created_at=receipt["created_at"],
            reason="baseline recomputed on September 20; old input capture timestamps do not make predictions available before August29-September6 decisions"),
        sources={str(path.relative_to(ROOT)):sha(path) for path in sorted(paths)})
    (OUT/"saved-model-inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k in ("capture133", "replay135")}, default=str)[:500])
    print("r02", len(candidates), "prestart", sum(x["computed_prestart"] for x in candidates),
          "proxyfreshpre", sum(x["computed_prestart"] and x["proxy_age_within_30m"] for x in candidates))


if __name__ == "__main__":
    main()
