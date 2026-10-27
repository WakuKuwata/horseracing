"""Stored pre-race judgments with official settlement isolated; no closing price is used."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "artifacts/158-prerace-roi-comparison/snapshot"
AUDIT = ROOT / "artifacts/155-saved-odds-settlement-audit"
MEV = ROOT / "artifacts/market_ev/mev-ens15-v1"
LEGACY_SOURCE = ROOT / "scripts/roi_explore/verified_recompare_157/legacy.py"
WEIGHT_COLUMNS = ("weight", "weight_diff", "carried_weight_ratio")
MEV_TOKENS = {"mev-ens15-v1", "seeds=1-15", "threads=1", "deterministic",
    "features=roi-explore-2026-09", "drop=sameday,weightlive", "data>=2007", "oddsobs=quote"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text())


def _legacy():
    spec = importlib.util.spec_from_file_location("prerace158_legacy", LEGACY_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _time(value):
    return pd.to_datetime(value, utc=True, errors="coerce")


def _decimal(value):
    if value is None or pd.isna(value):
        return None
    try:
        x = Decimal(str(value))
    except InvalidOperation:
        return None
    return x if x.is_finite() else None


def _groups(frame, key="race_id"):
    return {str(k): g for k, g in frame.groupby(key, sort=False)}


def _field_valid(entries):
    if entries is None or entries.empty:
        return False
    active = entries.loc[entries.entry_status.eq("started")]
    nums = entries.horse_number
    return bool(len(active) >= 2 and entries.horse_id.notna().all()
        and not entries.horse_id.duplicated().any() and nums.notna().all()
        and not nums.duplicated().any() and (nums >= 1).all()
        and np.equal(nums, np.floor(nums)).all()
        and entries.entry_status.isin(["started", "cancelled", "excluded"]).all())


def settlement_rows(entries, results, payouts, archive_payouts):
    """Validate full outcomes/payouts without consulting final odds."""
    require(_field_valid(entries), "invalid_settlement_field")
    active = entries.loc[entries.entry_status.eq("started")].copy()
    require(results is not None and not results.horse_id.duplicated().any()
        and set(results.horse_id) == set(active.horse_id)
        and len(results) == len(active), "incomplete_or_extra_results")
    require(results.result_status.isin(["finished", "stopped", "disqualified"]).all(),
        "invalid_result_status")
    finished = results.loc[results.result_status.eq("finished")]
    require(finished.finish_order.notna().all()
        and finished.finish_order.between(1, len(active)).all()
        and np.equal(finished.finish_order, np.floor(finished.finish_order)).all(),
        "invalid_finishing_rank")
    joined = active[["horse_id", "horse_number"]].merge(
        results[["horse_id", "result_status", "finish_order"]], on="horse_id", validate="one_to_one")
    joined["won"] = joined.result_status.eq("finished") & joined.finish_order.eq(1)
    winners = set(joined.loc[joined.won, "horse_number"])
    require(bool(winners), "winner_absent")
    require(payouts is not None and not payouts.empty and payouts.horse_number.notna().all()
        and not payouts.horse_number.duplicated().any(), "official_payout_missing_or_duplicate")
    require(winners == set(payouts.horse_number), "official_payout_winner_set_mismatch")
    require(payouts.payout_yen.notna().all() and (payouts.payout_yen >= 100).all()
        and np.equal(payouts.payout_yen, np.floor(payouts.payout_yen)).all(), "invalid_official_payout")
    if "source" in payouts:
        require(payouts.source.eq("netkeiba_result").all(), "unknown_payout_source")
    require(archive_payouts is not None and not archive_payouts.horse_number.duplicated().any(),
        "archive_payout_missing_or_duplicate")
    expected = payouts.set_index("horse_number").payout_yen.to_dict()
    require(expected == archive_payouts.set_index("horse_number").payout_yen.to_dict(),
        "archive_and_stored_payout_differ")
    joined["official_payout_yen"] = joined.horse_number.map(expected).fillna(0).astype(int)
    joined["dead_heat"] = len(winners) > 1
    return joined


def mev_flags(group, entries, race, *, manifest_sha):
    """Eligibility of stored whole-field quote/MEV; this function never inspects results."""
    flags = []
    if group is None or group.empty:
        return ["mev_not_saved"]
    if not _field_valid(entries):
        flags.append("mev_invalid_started_field")
    active = entries.loc[entries.entry_status.eq("started")]
    if (group.horse_id.isna().any() or group.horse_id.duplicated().any()
        or len(group) != len(active) or set(group.horse_id) != set(active.horse_id)):
        flags.append("mev_full_field_mismatch")
    else:
        numbering = active.set_index("horse_id").horse_number
        if not all(n == numbering[h] for h, n in zip(group.horse_id, group.horse_number, strict=True)):
            flags.append("mev_horse_number_mismatch")
    if group.horse_number.isna().any() or group.horse_number.duplicated().any():
        flags.append("mev_invalid_horse_number")
    if not group.model_version.eq("mev-ens15-v1").all():
        flags.append("mev_wrong_model")
    if not group.logic_version.map(lambda x: isinstance(x, str) and MEV_TOKENS <= set(x.split(";"))).all():
        flags.append("mev_quote_or_logic_provenance_unverified")
    if any(group[c].isna().any() or group[c].nunique() != 1 for c in (
        "run_id", "computed_at", "odds_observed_at", "booster", "booster_sha256")):
        flags.append("mev_mixed_or_missing_snapshot")
    post = _time(race["post_time"])
    observed = pd.to_datetime(group.odds_observed_at, utc=True, errors="coerce")
    computed = pd.to_datetime(group.computed_at, utc=True, errors="coerce")
    if pd.isna(post) or computed.isna().any() or not computed.lt(post).all():
        flags.append("mev_not_proven_prestart")
    if observed.isna().any() or computed.isna().any() or not observed.le(computed).all():
        flags.append("quote_not_available_by_decision")
    if not group.result_pending_at_compute.eq(True).all():
        flags.append("mev_result_already_known")
    if not (group.booster.eq("ensemble_2026.json").all() and group.booster_sha256.eq(manifest_sha).all()):
        flags.append("mev_booster_identity_mismatch")
    odds = pd.to_numeric(group.odds_used, errors="coerce").to_numpy(float)
    p = pd.to_numeric(group.win_prob, errors="coerce").to_numpy(float)
    if not np.isfinite(odds).all() or (odds < 1).any():
        flags.append("invalid_judgment_odds")
    if not np.isfinite(p).all() or not ((p > 0) & (p < 1)).all():
        flags.append("invalid_mev_probability")
    for row in group.itertuples():
        probability, price, expected = map(_decimal, (row.win_prob, row.odds_used, row.expected_return))
        if probability is None or price is None or expected is None or probability * price != expected:
            flags.append("mev_expected_return_identity_mismatch")
            break
    return flags


def legacy_flags(run, predictions, features, entries, race, *, decision, bundle_sha,
                 train_through, coefficient_train_through):
    flags = []
    tokens = set(run["logic_version"].split(";"))
    required = {"feat=features-021", f"mix=6:{bundle_sha[:12]}", "coef=2026", "wregime=serving"}
    if run["model_version"] != "mix-129-nj6" or not required <= tokens:
        flags.append("legacy_model_or_regime_token")
    day = str(race["race_date"])
    if not train_through < day or not coefficient_train_through < day:
        flags.append("legacy_training_overlaps_target")
    decision, post = _time(decision), _time(race["post_time"])
    if pd.isna(decision) or pd.isna(post) or not decision < post:
        flags.append("invalid_common_decision")
    for col in ("computed_at", "created_at", "updated_at"):
        stamp = _time(run.get(col))
        if pd.isna(stamp) or pd.isna(decision) or stamp > decision:
            flags.append(f"legacy_run_{col}_not_available")
    active = entries.loc[entries.entry_status.eq("started")]
    ids = set(active.horse_id)
    if not _field_valid(entries):
        flags.append("legacy_invalid_started_field")
    for kind, frame in (("prediction", predictions), ("feature", features)):
        if (frame is None or frame.empty or frame.horse_id.duplicated().any()
            or set(frame.horse_id) != ids or len(frame) != len(ids)):
            flags.append(f"legacy_{kind}_field_mismatch")
        if frame is not None:
            for col in ("created_at", "updated_at"):
                if col not in frame:
                    flags.append(f"legacy_{kind}_{col}_not_available")
                    continue
                stamps = pd.to_datetime(frame[col], utc=True, errors="coerce")
                if stamps.isna().any() or pd.isna(decision) or not stamps.le(decision).all():
                    flags.append(f"legacy_{kind}_{col}_not_available")
    if predictions is not None:
        p = pd.to_numeric(predictions.win_prob, errors="coerce").to_numpy(float)
        if (not np.isfinite(p).all() or not ((p > 0) & (p < 1)).all()
            or not np.isclose(p.sum(), 1., atol=1e-8, rtol=0)):
            flags.append("legacy_invalid_probability_vector")
    if features is not None:
        if not features.feature_version.eq("features-021").all():
            flags.append("legacy_feature_version")
        try:
            payloads = [json.loads(v) if isinstance(v, str) else v for v in features.features_json]
        except (TypeError, ValueError):
            payloads = []
        if not payloads or not all(isinstance(v, dict) and all(
            key in v and v[key] is None for key in WEIGHT_COLUMNS) for v in payloads):
            flags.append("legacy_preweight_not_proven")
    return flags


def _read_snapshot(snapshot_dir):
    manifest = _json(snapshot_dir / "manifest.json")
    require(manifest["settings"]["read_only"] == "on"
        and manifest["settings"]["isolation"] == "repeatable read", "Unsafe snapshot")
    tables = {}
    for name, record in manifest["tables"].items():
        path = ROOT / record["path"]
        require(sha(path) == record["sha256"], f"Snapshot SHA differs: {name}")
        tables[name] = pd.read_parquet(path)
        require(len(tables[name]) == record["rows"], f"Snapshot row count differs: {name}")
    return tables


def _archive_tables(candidate_ids):
    expected = _json(AUDIT / "output-sha256.json")["files"]
    tables = {}
    for name in ("archive_pages", "archive_payouts"):
        path = AUDIT / "audit" / (name + ".parquet")
        require(sha(path) == expected[str(path.relative_to(ROOT))], f"Archive audit changed: {name}")
        tables[name] = pd.read_parquet(path)
    pages = tables["archive_pages"]
    pages = pages.loc[pages.selected & pages.race_id.isin(candidate_ids)].copy()
    require(not pages.race_id.duplicated().any(), "Duplicate selected settlement archive")
    for row in pages.itertuples():
        require(row.payout_parse_status == "ok" and row.result_parse_status == "ok"
            and row.body_race_id_matches and row.html_date_in_scope, "Bad settlement source page")
        path = ROOT / row.archive_path
        require(sha(path) == row.archive_gz_sha256, "Settlement gzip SHA differs")
        raw = gzip.decompress(path.read_bytes())
        require(hashlib.sha256(raw).hexdigest() == row.archive_raw_sha256, "Settlement raw SHA differs")
        text = raw.decode(row.decode_encoding, errors="replace")
        require(hashlib.sha256(text.encode()).hexdigest() == row.html_utf8_sha256,
            "Settlement decoded SHA differs")
    return pages.set_index("race_id"), _groups(tables["archive_payouts"])


def _validate_models(model_table):
    old = _legacy()
    bundle = old._validate_bundle()
    bundle_sha = sha(old.BUNDLE)
    require(len(model_table) == 1, "Ambiguous old model registration")
    registration = model_table.iloc[0]
    metrics = json.loads(registration.metrics_summary)
    require(registration.model_version == "mix-129-nj6"
        and registration.feature_version == "features-021"
        and metrics["registration"]["bundle_sha256"] == bundle_sha
        and metrics["training"]["train_through"] == bundle["train_through"],
        "Old model registration differs")
    registered = Path(registration.weights_uri)
    require(sha(registered) == bundle_sha, "Registered old bundle bytes differ")
    for member in _json(registered)["members"]:
        for filename, checksum in member["files"].items():
            require(sha(registered.parent / member["artifact_dir"] / filename) == checksum,
                "Registered old member bytes differ")
    spec = _json(MEV / "ensemble.spec.json")
    require(spec["version"] == "mev-ens15-v1" and spec["objective"] == "binary"
        and spec["seeds"] == list(range(1, 16))
        and spec["training_cutoff_by_year"]["2026"]["train_through"] == 2025,
        "MEV training or ensemble identity differs")
    manifest = _json(MEV / "ensemble_2026.json")
    require(manifest["year"] == 2026 and manifest["version"] == "mev-ens15-v1"
        and [x["seed"] for x in manifest["members"]] == list(range(1, 16)),
        "MEV manifest identity differs")
    for member in manifest["members"]:
        require(sha(MEV / member["path"]) == member["sha256"], "MEV booster bytes differ")
    return bundle, bundle_sha, sha(MEV / "ensemble_2026.json")


def _available_at(run, predictions, features):
    stamps = [_time(run[c]) for c in ("computed_at", "created_at", "updated_at")]
    for frame in (predictions, features):
        for col in ("created_at", "updated_at"):
            stamps.extend(pd.to_datetime(frame[col], utc=True).tolist())
    return max(stamps)


def _chaos_diagnostic(chaos, entries, groups, races):
    """Coverage only; these other quotes are never substituted into the primary decision."""
    rows = []
    for record in chaos.to_dict("records"):
        rid = record["race_id"]
        field = json.loads(record["field_json"])
        active = entries[rid].loc[entries[rid].entry_status.eq("started")]
        expected = {(h, int(n)) for h, n in zip(active.horse_id, active.horse_number, strict=True)}
        actual = {(r["horse_id"], int(r["horse_number"])) for r in field}
        digest = hashlib.sha256(json.dumps(field, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        post, captured, created = map(_time, (races[rid]["post_time"], record["captured_at"], record["created_at"]))
        row = dict(race_id=rid, status=record["status"], field_same=actual == expected and len(field) == len(active),
            digest_matches=digest == record["content_digest"],
            captured_prestart=bool(pd.notna(post) and pd.notna(captured) and captured < post),
            stored_prestart=bool(pd.notna(post) and pd.notna(created) and created < post),
            captured_at=str(captured), created_at=str(created), source=record["source"],
            capture_strength=record["capture_strength"], capture_trigger=record["capture_trigger"])
        if rid in groups:
            g = groups[rid]
            by_id = {r["horse_id"]: r for r in field}
            same_ids = set(by_id) == set(g.horse_id) and len(field) == len(g)
            row["all_prices_equal_mev"] = same_ids and all(
                _decimal(by_id[h]["odds"]) == _decimal(o) for h, o in zip(g.horse_id, g.odds_used, strict=True))
            row["same_observed_time_as_mev"] = bool(g.odds_observed_at.nunique() == 1
                and captured == _time(g.odds_observed_at.iloc[0]))
        rows.append(row)
    return rows


def load_inputs(snapshot_dir=SNAPSHOT):
    snapshot_dir = Path(snapshot_dir).resolve()
    tables = _read_snapshot(snapshot_dir)
    bundle, bundle_sha, mev_sha = _validate_models(tables["model"])
    races = tables["races"].set_index("race_id").to_dict("index")
    pages, archived = _archive_tables(set(races))
    entries, results, payouts, groups, runs = [_groups(tables[k]) for k in (
        "entries", "results", "payouts", "mev", "runs")]
    predictions = _groups(tables["predictions"], "prediction_run_id")
    features = _groups(tables["features"], "prediction_run_id")
    judgments, settlements, exclusions, mev_candidates, legacy_rejections, selections = [], [], [], [], [], []
    for rid, race in sorted(races.items()):
        entry, result, payout = entries.get(rid), results.get(rid), payouts.get(rid)
        try:
            require(rid in pages.index, "settlement_archive_missing")
            settled = settlement_rows(entry, result, payout, archived.get(rid))
        except ValueError as error:
            exclusions.append(dict(race_id=rid, reason=str(error)))
            continue
        flags = mev_flags(groups.get(rid), entry, race, manifest_sha=mev_sha)
        if race["track_type"] == "障":
            flags.append("jump_not_in_mev_scope")
        if str(race["race_date"])[:4] != "2026":
            flags.append("outside_fixed_model_year")
        if flags:
            exclusions.append(dict(race_id=rid, reason=flags[0], details=flags))
            continue
        g = groups[rid]
        decision = _time(g.computed_at.iloc[0])
        mev_candidates.append(dict(race_id=rid, race_date=str(race["race_date"]), horses=len(g),
            decision_at=decision.isoformat(), quote_observed_at=_time(g.odds_observed_at.iloc[0]).isoformat()))
        if settled.dead_heat.any():
            exclusions.append(dict(race_id=rid, reason="dead_heat_not_in_151_primary"))
            continue
        eligible = []
        for run in runs.get(rid, pd.DataFrame()).to_dict("records"):
            run_id = run["prediction_run_id"]
            ps, fs = predictions.get(run_id), features.get(run_id)
            bad = legacy_flags(run, ps, fs, entry, race, decision=decision, bundle_sha=bundle_sha,
                train_through=bundle["train_through"], coefficient_train_through=bundle["coefficient_train_through"])
            if bad:
                legacy_rejections.append(dict(race_id=rid, prediction_run_id=run_id, reasons=bad))
            else:
                eligible.append(run)
        if not eligible:
            exclusions.append(dict(race_id=rid, reason="no_old_prediction_available_by_decision"))
            continue
        chosen = max(eligible, key=lambda r: (_time(r["computed_at"]), r["prediction_run_id"]))
        run_id = chosen["prediction_run_id"]
        old = predictions[run_id].set_index("horse_id")
        available = _available_at(chosen, predictions[run_id], features[run_id])
        row = g[["race_id", "horse_id", "horse_number"]].copy()
        row["race_date"] = str(race["race_date"])
        row["post_time"] = _time(race["post_time"])
        row["decision_at"] = decision
        row["quote_observed_at"] = _time(g.odds_observed_at.iloc[0])
        row["quote_age_seconds"] = (decision - _time(g.odds_observed_at.iloc[0])).total_seconds()
        row["judgment_odds"] = pd.to_numeric(g.odds_used).to_numpy(float)
        row["q"] = (1. / row.judgment_odds) / (1. / row.judgment_odds).sum()
        row["p_mev_raw"] = pd.to_numeric(g.win_prob).to_numpy(float)
        row["p_mev"] = row.p_mev_raw.clip(1e-6, 1 - 1e-6)
        row["p_pred_preweight"] = pd.to_numeric(old.loc[row.horse_id, "win_prob"]).to_numpy(float)
        row["mev_run_id"] = str(g.run_id.iloc[0])
        row["mev_booster"] = str(g.booster.iloc[0])
        row["mev_booster_sha256"] = str(g.booster_sha256.iloc[0])
        row["old_prediction_run_id"] = run_id
        row["old_computed_at"] = _time(chosen["computed_at"])
        row["old_available_at"] = available
        row["old_age_seconds"] = (decision - _time(chosen["computed_at"])).total_seconds()
        row["field_size"] = len(g)
        row["prediction_source"] = "stored_prestart_mev_and_mix129"
        judgments.append(row)
        settled["race_id"] = rid
        settled["race_date"] = str(race["race_date"])
        settled["post_time"] = _time(race["post_time"])
        settled["official_source"] = "netkeiba_result"
        settled["settlement_archive_path"] = pages.loc[rid, "archive_path"]
        settled["settlement_archive_sha256"] = pages.loc[rid, "html_utf8_sha256"]
        settlements.append(settled)
        selections.append(dict(race_id=rid, prediction_run_id=run_id, old_available_at=str(available),
            old_computed_at=str(chosen["computed_at"]), decision_at=str(decision),
            quote_observed_at=str(g.odds_observed_at.iloc[0]),
            quote_age_seconds=float(row.quote_age_seconds.iloc[0]),
            eligible_old_runs=len(eligible)))
    judgment = pd.concat(judgments, ignore_index=True) if judgments else pd.DataFrame()
    settlement = pd.concat(settlements, ignore_index=True) if settlements else pd.DataFrame()
    if len(judgment):
        judgment = judgment.sort_values(["race_date", "decision_at", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
        settlement = settlement.sort_values(["race_date", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
        require(not judgment.duplicated(["race_id", "horse_id"]).any(), "Duplicate judgment identity")
    audit = dict(candidate_settlement_races=len(races), mev_candidates=len(mev_candidates),
        mev_candidate_rows=sum(r["horses"] for r in mev_candidates),
        mev_candidate_days=sorted({r["race_date"] for r in mev_candidates}),
        paired_races=int(judgment.race_id.nunique()) if len(judgment) else 0,
        paired_rows=len(judgment), paired_days=sorted(set(judgment.race_date)) if len(judgment) else [],
        mev_candidate_details=mev_candidates, exclusions=exclusions,
        exclusion_counts=pd.Series([r["reason"] for r in exclusions]).value_counts().to_dict(),
        legacy_run_rejections=legacy_rejections, selected_old_runs=selections,
        chaos_diagnostic=_chaos_diagnostic(tables["chaos"], entries, groups, races),
        decision_clock="stored MEV computed_at; DB commit/publication time not retained",
        quote_source="exact odds_used saved with quote-provenance MEV, not current odds or chaos substitutions",
        old_selection="latest eligible computed_at then lexical UUID; all stored rows available <= decision",
        model=dict(old_bundle_sha256=bundle_sha, old_train_through=bundle["train_through"],
            old_coefficient_through=bundle["coefficient_train_through"], mev_manifest_sha256=mev_sha,
            mev_train_through=2025),
        limitations=["MEV has no row created/updated or commit/publication timestamp; computed_at is its single audit clock.",
            "Historical feature values consumed by MEV are not preserved as a full as-of matrix.",
            "Training/calibration used historical closing prices; pre-race input is an out-of-regime evaluation.",
            "Recorded quote age does not guarantee executable prices; no closing quote enters judgment.",
            "Official payouts are saved netkeiba result data, not a newly fetched independent feed."])
    return judgment, settlement, audit


def input_paths(snapshot_dir=SNAPSHOT):
    snapshot_dir = Path(snapshot_dir).resolve()
    manifest = _json(snapshot_dir / "manifest.json")
    paths = {Path(__file__), LEGACY_SOURCE, snapshot_dir / "manifest.json",
        AUDIT / "output-sha256.json", AUDIT / "audit/archive_pages.parquet",
        AUDIT / "audit/archive_payouts.parquet", MEV / "ensemble.spec.json", MEV / "ensemble_2026.json"}
    paths.update(ROOT / r["path"] for r in manifest["tables"].values())
    pages = pd.read_parquet(AUDIT / "audit/archive_pages.parquet")
    paths.update(ROOT / p for p in pages.loc[
        pages.selected & pages.race_id.isin(manifest["candidate_race_ids"]), "archive_path"])
    old = _legacy()
    paths.add(old.BUNDLE)
    for base in (old.BUNDLE, Path(pd.read_parquet(snapshot_dir / "model.parquet").iloc[0].weights_uri)):
        paths.add(base)
        for member in _json(base)["members"]:
            paths.update(base.parent / member["artifact_dir"] / filename for filename in member["files"])
    paths.update(MEV / member["path"] for member in _json(MEV / "ensemble_2026.json")["members"])
    return sorted(paths)
