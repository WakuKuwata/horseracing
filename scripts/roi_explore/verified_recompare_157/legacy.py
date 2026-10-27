"""Database-free, hash-checked saved mix-129 predictions for research 157.

135 is a retrospective replay of preserved pre-start inputs; 133 capture records contain
separately stored preweight predictions. A serving/full-info vector is never substituted for
the preweight primary input. IDs are kept literally as stored.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
RECENT = ROOT / "artifacts/135-mixture-reproducibility/recent-owned-54586a4bf86c"
BUNDLE = ROOT / "artifacts/129-candidate-mixture-serving/bundle.json"
CAPTURE = ROOT / "artifacts/133-prospective-paired-comparison/records/capture"
MODEL_VERSION = "mix-129-nj6"
DATABASE_SNAPSHOT = ROOT / "artifacts/157-verified-roi-comparison/snapshot-v2"
SAME_DAY_WEIGHT_COLUMNS = ("weight", "weight_diff", "carried_weight_ratio")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def input_paths() -> list[Path]:
    """Enumerate the receipt-bound originals, preserved source copies and capture JSONs."""
    paths = {BUNDLE, RECENT / "execution-receipt.json", RECENT / "input-receipt.json"}
    for name in ("execution-receipt.json", "input-receipt.json"):
        paths.update(Path(p) for p in _json(RECENT / name)["files"])
    paths.update(CAPTURE.glob("*.json"))
    if (DATABASE_SNAPSHOT / "manifest.json").exists():
        paths.add(DATABASE_SNAPSHOT / "manifest.json")
        paths.update(ROOT / item["path"] for item in
                     _json(DATABASE_SNAPSHOT / "manifest.json")["tables"].values())
        model = pd.read_parquet(DATABASE_SNAPSHOT / "model.parquet").iloc[0]
        registered_bundle = Path(model.weights_uri)
        paths.add(registered_bundle)
        for member in _json(registered_bundle)["members"]:
            paths.update(registered_bundle.parent / member["artifact_dir"] / filename
                         for filename in member["files"])
    return sorted(paths)


def _verify_receipts():
    receipts = {}
    expected = {}
    for name in ("execution-receipt.json", "input-receipt.json"):
        receipt = _json(RECENT / name)
        _require(receipt["no_outcomes_read"] is True, f"135 outcomes read: {name}")
        for path, checksum in receipt["files"].items():
            _require(path not in expected or expected[path] == checksum, "Conflicting 135 SHA")
            expected[path] = checksum
        receipts[name] = receipt
    for path, checksum in expected.items():
        _require(sha256(Path(path)) == checksum, f"135 input/source SHA changed: {path}")
    return receipts, expected


def _validate_bundle():
    bundle = _json(BUNDLE)
    _require(bundle["bundle_id"] == "129-125_new_joint_mixed6_v1", "Wrong legacy bundle")
    _require(len(bundle["members"]) == 6, "Wrong legacy member count")
    _require(np.isclose(sum(m["weight"] for m in bundle["members"]), 1., atol=1e-12),
             "Legacy weights do not sum to one")
    for member in bundle["members"]:
        directory = BUNDLE.parent / member["artifact_dir"]
        for filename, checksum in member["files"].items():
            _require(sha256(directory / filename) == checksum, f"Legacy member changed: {member['id']}")
        _require(member["correction"]["fit_through"] == bundle["coefficient_train_through"],
                 "Legacy correction cutoff differs")
        meta = _json(directory / "metadata.json")
        _require(meta["train_through"] == bundle["train_through"], "Legacy model cutoff differs")
    return bundle


def _validated_win(ids, predictions):
    ids = list(ids)
    _require(bool(ids) and len(ids) == len(set(ids)) and all(isinstance(h, str) for h in ids),
             "Invalid legacy field identities")
    array = np.asarray(predictions, dtype=float)
    _require(array.shape == (len(ids), 3), "Legacy prediction shape differs")
    win = array[:, 0]
    _require(np.isfinite(array).all() and (win > 0).all() and (win < 1).all()
             and np.isclose(win.sum(), 1., atol=1e-8, rtol=0), "Invalid legacy probability vector")
    return ids, win


def _time(value):
    return pd.to_datetime(value, utc=True, errors="raise")


def _race_rows(rid, day, ids, primary, full, *, source, path, captured, predicted, post,
               bundle, time_is_proxy):
    day = pd.Timestamp(day).date().isoformat()
    _require(bundle["train_through"] < day, f"Legacy model trained through evaluation race: {rid}")
    _require(bundle["coefficient_train_through"] < day, f"Legacy coefficient sees evaluation: {rid}")
    ids, primary = _validated_win(ids, primary)
    full_win = np.full(len(ids), np.nan) if full is None else _validated_win(ids, full)[1]
    captured, predicted, post = _time(captured), _time(predicted), _time(post)
    _require(pd.notna(captured) and pd.notna(post) and captured < post,
             f"Legacy inputs not preserved before scheduled start: {rid}")
    return [dict(race_id=rid, horse_id=h, race_date=day,
                 p_pred_preweight=float(primary[i]), p_pred_full=float(full_win[i]),
                 prediction_source=source, prediction_path=str(path.relative_to(ROOT)),
                 input_captured_at=captured, predicted_at=predicted,
                 prediction_time_is_proxy=time_is_proxy, scheduled_post_time=post,
                 prediction_prestart=bool(predicted < post), input_prestart=True,
                 model_version=MODEL_VERSION, model_train_through=bundle["train_through"],
                 coefficient_train_through=bundle["coefficient_train_through"])
            for i, h in enumerate(ids)]


def _load_135(bundle, receipts):
    path = RECENT / "predictions.pkl"
    with path.open("rb") as handle:
        saved = pickle.load(handle)
    with (RECENT / "raw-inputs.pkl").open("rb") as handle:
        raw = pickle.load(handle)
    selections = {r["race_id"]: r for r in raw["selection"]}
    _require(len(selections) == len(raw["selection"]), "Duplicate 135 selected input run")
    snapshots = {}
    for row in raw["snapshots"]:
        snapshots.setdefault(row["race_id"], []).append(row)
    _require(set(saved["ids"]) == set(saved["dates"]), "135 saved date/ID populations differ")
    _require(len(saved["ids"]) == receipts["execution-receipt.json"]["n_races"],
             "135 saved race count differs from receipt")
    rows = []
    for rid, ids in saved["ids"].items():
        selected = selections[rid]
        snapshot = [r for r in snapshots[rid]
                    if r["prediction_run_id"] == selected["prediction_run_id"]]
        _require(len(snapshot) == len(ids) and {r["horse_id"] for r in snapshot} == set(ids),
                 f"135 prestart snapshot field differs: {rid}")
        captured = max(_time(r["snapshot_created_at"]) for r in snapshot)
        rows.extend(_race_rows(rid, saved["dates"][rid], ids,
            saved["predictions"]["preweight"]["baseline"][rid],
            saved["predictions"]["full"]["baseline"][rid],
            source="135_owned_baseline_preweight", path=path, captured=captured,
            predicted=receipts["execution-receipt.json"]["created_at"],
            post=selected["post_time"], bundle=bundle, time_is_proxy=True))
    return rows


def _load_133(bundle, receipt_hashes):
    rows = []
    saved_hashes = set(receipt_hashes.values())
    expected_bundle = sha256(BUNDLE)
    for path in sorted(CAPTURE.glob("*.json")):
        saved = _json(path)
        _require(saved["source"] == "capture", "133 production replay cannot replace capture")
        model = saved["comparators"]["new"]
        _require(model["model_version"] == MODEL_VERSION
                 and model["bundle_manifest_sha256"] == expected_bundle
                 and model["train_through"] == bundle["train_through"]
                 and model["coefficient_train_through"] == bundle["coefficient_train_through"],
                 f"133 legacy model lineage differs: {path}")
        _require(all(h in saved_hashes for h in saved["code"].values()),
                 f"133 source unavailable in verified preserved source: {path}")
        regime = saved["regimes"]["preweight"]
        ids = regime["started_ids"]
        predictions = regime["new"]
        _require(set(ids) == set(predictions) == set(regime["inputs"]["rows"]),
                 f"133 input/prediction field differs: {path}")
        _require(regime["new_audit"]["bundle_sha256"] == expected_bundle,
                 "133 prediction audit bundle differs")
        displayed = regime["display_production_lambda"]["new"]
        _require(set(displayed) == set(ids) and all(
            predictions[h][0] == displayed[h][0] for h in ids), "133 displayed win differs")
        rows.extend(_race_rows(saved["race_id"], saved["race_day"], ids,
            [predictions[h] for h in ids], None,
            source="133_capture_preweight", path=path, captured=saved["input_captured_at"],
            predicted=regime["predicted_at"]["new"], post=saved["scheduled_start"],
            bundle=bundle, time_is_proxy=False))
    return rows


def load_predictions() -> tuple[pd.DataFrame, dict]:
    """Load saved probabilities only; no database access, inference or model fitting."""
    receipts, expected = _verify_receipts()
    bundle = _validate_bundle()
    _require(receipts["input-receipt.json"]["train_through"] == bundle["train_through"],
             "135 input receipt and bundle training cutoff differ")
    a, b = _load_135(bundle, receipts), _load_133(bundle, expected)
    first, second = pd.DataFrame(a), pd.DataFrame(b)
    overlap = set(first.race_id) & set(second.race_id)
    for rid in overlap:
        x = first[first.race_id.eq(rid)].set_index("horse_id").p_pred_preweight
        y = second[second.race_id.eq(rid)].set_index("horse_id").p_pred_preweight
        _require(set(x.index) == set(y.index) and np.allclose(x, y.reindex(x.index), atol=1e-12, rtol=0),
                 f"Conflicting duplicate saved preweight predictions: {rid}")
    result = pd.concat([first, second[~second.race_id.isin(overlap)]], ignore_index=True)
    _require(not result.duplicated(["race_id", "horse_id"]).any(), "Duplicate legacy identity")
    audit = dict(source_priority=["135_owned_baseline_preweight", "133_capture_preweight"],
        receipt_file_sha_verified=len(expected), race_count=int(result.race_id.nunique()),
        horse_count=len(result), overlap_agreement_races=len(overlap),
        source_races=result.groupby("prediction_source").race_id.nunique().to_dict(),
        train_through=bundle["train_through"], coefficient_train_through=bundle["coefficient_train_through"],
        bundle_sha256=sha256(BUNDLE), model_version=MODEL_VERSION,
        known_limitations=[
            "135 baseline predictions are retrospective replays of saved prestart inputs, not live forecasts.",
            "135 predicted_at is the execution receipt write time upper bound, not per-race inference time.",
            "133 capture JSON bytes are frozen by 157; no independent historical JSON-file SHA receipt exists.",
            "133 serving probabilities are not relabelled full-info or used as preweight.",
            "No untouched holdout claim; feature snapshot timestamps do not prove every feature's historical origin."])
    return result, audit


def load_database_predictions(snapshot_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Use only existing stored runs whose full field proves the preweight condition.

    No SQL is executed. The exporter already restricted this snapshot to 155's strict
    verified race IDs. Unknown/missing times and a missing weight feature are not evidence
    of a preweight input. Eligibility is evaluated for every run before latest-run selection.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest = _json(snapshot_dir / "manifest.json")
    _require(manifest["settings"]["read_only"] == "on"
             and manifest["settings"]["isolation"] == "repeatable read", "Unsafe DB snapshot")
    data = {}
    for name in ("runs", "predictions", "features", "entries", "races", "model"):
        record = manifest["tables"][name]
        path = ROOT / record["path"]
        _require(sha256(path) == record["sha256"], f"Stored DB snapshot changed: {name}")
        data[name] = pd.read_parquet(path)
        _require(len(data[name]) == record["rows"], f"Stored DB row count differs: {name}")
    bundle = _validate_bundle()
    expected_bundle = sha256(BUNDLE)
    _require(len(data["model"]) == 1, "Ambiguous DB model registration")
    model = data["model"].iloc[0]
    metrics = json.loads(model.metrics_summary) if isinstance(model.metrics_summary, str) else model.metrics_summary
    _require(model.model_version == MODEL_VERSION and model.feature_version == "features-021"
             and metrics["registration"]["bundle_sha256"] == expected_bundle
             and metrics["training"]["train_through"] == bundle["train_through"],
             "DB legacy model registration differs")
    registered_bundle = Path(model.weights_uri)
    _require(sha256(registered_bundle) == expected_bundle, "DB weights URI bundle differs")
    for member in _json(registered_bundle)["members"]:
        for filename, checksum in member["files"].items():
            path = registered_bundle.parent / member["artifact_dir"] / filename
            _require(sha256(path) == checksum, "DB registered member bytes differ")
    races = data["races"].set_index("race_id").to_dict("index")
    fields = {rid: group for rid, group in data["entries"].groupby("race_id")}
    predictions = {run: group for run, group in data["predictions"].groupby("prediction_run_id")}
    features = {run: group for run, group in data["features"].groupby("prediction_run_id")}
    eligible, rejected = [], []
    for run in data["runs"].to_dict("records"):
        rid, run_id = run["race_id"], run["prediction_run_id"]
        race = races[rid]
        flags = []
        tokens = set(run["logic_version"].split(";"))
        needed = {"feat=features-021", f"mix=6:{expected_bundle[:12]}", "coef=2026", "wregime=serving"}
        if run["model_version"] != MODEL_VERSION or not needed <= tokens:
            flags.append("model_or_regime_token")
        day = pd.Timestamp(race["race_date"]).date().isoformat()
        if not bundle["train_through"] < day:
            flags.append("in_model_training_period")
        post, computed = _time(race["post_time"]), _time(run["computed_at"])
        if pd.isna(post) or pd.isna(computed) or not computed < post:
            flags.append("run_not_proven_prestart")
        entry = fields[rid]
        active = entry[entry.entry_status.eq("started")]
        ids = set(active.horse_id)
        if (not ids or active.horse_number.isna().any() or active.horse_number.duplicated().any()
                or active.horse_id.duplicated().any()):
            flags.append("invalid_started_field")
        prediction, feature = predictions.get(run_id), features.get(run_id)
        if (prediction is None or prediction.horse_id.duplicated().any()
                or set(prediction.horse_id) != ids):
            flags.append("prediction_field_mismatch")
        if (feature is None or feature.horse_id.duplicated().any() or set(feature.horse_id) != ids):
            flags.append("feature_field_mismatch")
        if prediction is not None:
            for col in ("created_at", "updated_at"):
                ts = pd.to_datetime(prediction[col], utc=True, errors="coerce")
                if ts.isna().any() or pd.isna(post) or not ts.lt(post).all():
                    flags.append(f"prediction_{col}_not_prestart")
            prob = pd.to_numeric(prediction.win_prob, errors="coerce").to_numpy(float)
            if (not np.isfinite(prob).all() or not ((prob > 0) & (prob < 1)).all()
                    or not np.isclose(prob.sum(), 1., atol=1e-8, rtol=0)):
                flags.append("invalid_probability_vector")
        if feature is not None:
            if not feature.feature_version.eq("features-021").all():
                flags.append("feature_version")
            for col in ("created_at", "updated_at"):
                ts = pd.to_datetime(feature[col], utc=True, errors="coerce")
                if ts.isna().any() or pd.isna(post) or not ts.lt(post).all():
                    flags.append(f"feature_{col}_not_prestart")
            payloads = [json.loads(v) if isinstance(v, str) else v for v in feature.features_json]
            if not all(isinstance(v, dict) and all(c in v and v[c] is None
                       for c in SAME_DAY_WEIGHT_COLUMNS) for v in payloads):
                flags.append("preweight_not_proven")
        if flags:
            rejected.append(dict(race_id=rid, prediction_run_id=run_id, reasons=flags))
            continue
        # Full heads are retained only to validate the saved vector's shape. The serving
        # probability is explicitly proven preweight; it is not a full-info sensitivity arm.
        p = prediction.set_index("horse_id")
        order = sorted(ids)
        heads = p.loc[order, ["win_prob", "top2_prob", "top3_prob"]].to_numpy(float)
        captured = pd.to_datetime(feature.created_at, utc=True).max()
        rows = _race_rows(rid, day, order, heads, None,
            source="db_prestart_preweight", path=snapshot_dir / "predictions.parquet",
            captured=captured, predicted=computed, post=post, bundle=bundle, time_is_proxy=False)
        for row in rows:
            row["prediction_run_id"] = run_id
        eligible.append(dict(race_id=rid, prediction_run_id=run_id, computed_at=computed, rows=rows))
    selected = {}
    for record in eligible:
        rid = record["race_id"]
        if rid not in selected or (record["computed_at"], record["prediction_run_id"]) > (
                selected[rid]["computed_at"], selected[rid]["prediction_run_id"]):
            selected[rid] = record
    rows = [row for record in selected.values() for row in record["rows"]]
    result = pd.DataFrame(rows)
    audit = dict(candidate_runs=len(data["runs"]), eligible_runs=len(eligible),
        selected_races=len(selected), selected_horses=len(result), rejected_runs=rejected,
        selection="latest eligible prestart computed_at, then run UUID lexical; field validation before choice",
        preweight_proof="features-021 serving: all same-day weight columns explicitly present and null",
        required_null_columns=list(SAME_DAY_WEIGHT_COLUMNS),
        selected_runs=[{k: str(v) for k, v in record.items() if k != "rows"}
                       for record in selected.values()])
    return result, audit


def combine_predictions(database: pd.DataFrame, cached: pd.DataFrame):
    """Keep fixed source priority and report, rather than average, differing repeat forecasts."""
    overlap = set(database.race_id) & set(cached.race_id) if len(database) else set()
    differences = []
    for rid in sorted(overlap):
        x = database[database.race_id.eq(rid)].set_index("horse_id").p_pred_preweight
        y = cached[cached.race_id.eq(rid)].set_index("horse_id").p_pred_preweight
        same = set(x.index) == set(y.index)
        differences.append(dict(race_id=rid, same_field=same,
            max_abs_probability_difference=float(np.max(np.abs(x-y.reindex(x.index)))) if same else None))
    if len(database):
        result = pd.concat([database, cached[~cached.race_id.isin(set(database.race_id))]], ignore_index=True)
    else:
        result = cached.copy()
    _require(not result.duplicated(["race_id", "horse_id"]).any(), "Combined legacy field duplicate")
    return result, dict(source_priority=["db_prestart_preweight", "135_owned_baseline_preweight", "133_capture_preweight"],
        duplicate_source_races=differences,
        source_races=result.groupby("prediction_source").race_id.nunique().to_dict())
