"""Rebuild a separate, whole-field dataset from frozen audit evidence and stored DB rows."""
from __future__ import annotations

import gzip
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_training import market_ev

ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "artifacts/155-saved-odds-settlement-audit"
OUT = ROOT / "artifacts/157-verified-roi-comparison"
SNAPSHOT = OUT / "snapshot-v2"
MEV = ROOT / "artifacts/market_ev/mev-ens15-v1"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def by_race(frame):
    return {str(k): g for k, g in frame.groupby("race_id", sort=False)}


def read_snapshot():
    document = json.loads((SNAPSHOT / "manifest.json").read_text())
    require(document["settings"]["read_only"] == "on", "Snapshot not read-only")
    require(document["settings"]["isolation"] == "repeatable read", "Inconsistent DB snapshot")
    result = {}
    for key, record in document["tables"].items():
        path = ROOT / record["path"]
        require(sha(path) == record["sha256"], f"Stored input changed: {key}")
        result[key] = pd.read_parquet(path)
        require(len(result[key]) == record["rows"], "Snapshot row count differs")
    return result


def read_audit(relative):
    path = AUDIT / relative
    if relative.startswith("audit/"):
        expected = json.loads((AUDIT / "output-sha256.json").read_text())["files"]
        expected = expected[str(path.relative_to(ROOT))]
    else:
        expected = json.loads((AUDIT / "protocol.json").read_text())["inputs_and_source_sha256"]
        expected = expected[str(path.relative_to(ROOT))]
    require(sha(path) == expected, f"Audit material changed: {relative}")
    return pd.read_parquet(path)


def validate_race(entries, results, html, payouts, mappings):
    """No guessed identity mapping, partial joins, or loser-zero fallback on missing payouts."""
    started = entries.loc[entries.entry_status.eq("started")].copy()
    printed = html.loc[html.is_started.eq(True)].copy()
    require(len(started) >= 2, "Too few starters")
    for group in (started, printed):
        require(group.horse_number.notna().all() and not group.horse_number.duplicated().any(),
                "Missing/duplicate horse number")
        require(not group.horse_id.duplicated().any(), "Duplicate horse identity")
    require(set(started.horse_id) == set(results.horse_id), "Incomplete/extra results")
    require(len(results) == len(started), "Duplicate result identity")
    require(set(started.horse_number) == set(printed.horse_number), "HTML starter set differs")
    require(results.result_status.isin(["finished", "stopped", "disqualified"]).all(), "Unknown result")
    finished = results.loc[results.result_status.eq("finished")]
    require(finished.finish_order.notna().all() and finished.finish_order.between(1, len(started)).all(),
            "Invalid finishing rank")
    numbered = started.set_index("horse_number")
    result = results.set_index("horse_id")
    records = []
    for row in printed.to_dict("records"):
        n = row["horse_number"]
        horse = numbered.loc[n]
        raw_id, canonical_id = row["horse_id"], horse.horse_id
        exact = raw_id == canonical_id
        require(exact or mappings.get(raw_id) == canonical_id, "Unproven horse identity")
        require(not row["row_parse_error"], "HTML row parse error")
        price = Decimal(str(row["final_odds_text"]))
        require(price.is_finite() and price >= 1, "Invalid final odds")
        rr = result.loc[canonical_id]
        require(rr.result_status == row["result_status"], "HTML result status differs")
        if rr.result_status == "finished":
            require(rr.finish_order == row["finish_order"], "HTML finishing rank differs")
        records.append(dict(horse_id=canonical_id, html_horse_id=raw_id, horse_number=int(n),
            identity_method="exact" if exact else "explicit_mapped", odds=float(price),
            final_odds_decimal=str(price), popularity=row["popularity"],
            won=bool(rr.result_status == "finished" and rr.finish_order == 1),
            result_status=rr.result_status, finish_order=rr.finish_order))
    winner_numbers = {r["horse_number"] for r in records if r["won"]}
    require(bool(winner_numbers), "No winner")
    require(not payouts.horse_number.duplicated().any(), "Duplicate payout")
    require(winner_numbers == set(payouts.horse_number), "Payout/winner set differs")
    require((payouts.payout_yen >= 100).all(), "Invalid official payout")
    dividends = payouts.set_index("horse_number").payout_yen.to_dict()
    for record in records:
        record["official_payout_yen"] = int(dividends.get(record["horse_number"], 0))
        record["dead_heat"] = len(winner_numbers) > 1
        if record["won"] and len(winner_numbers) == 1:
            require(Decimal(record["final_odds_decimal"]) * 100 == record["official_payout_yen"],
                    "Ordinary winner final price/payout mismatch")
    return pd.DataFrame(records)


def verified_dataset(snapshot):
    ledger = read_audit("audit/races.parquet")
    pages = read_audit("audit/archive_pages.parquet")
    horses = read_audit("audit/archive_horses.parquet")
    dividends = read_audit("audit/archive_payouts.parquet")
    stored_payouts = read_audit("snapshot/payouts.parquet")
    mapping = read_audit("snapshot/id_mappings.parquet")
    mapping = mapping.loc[mapping.source.eq("netkeiba") & mapping.mapping_status.eq("mapped")]
    require(not mapping.source_id.duplicated().any(), "Ambiguous mapping")
    mappings = mapping.set_index("source_id").canonical_id.to_dict()
    selected = pages.loc[pages.selected].set_index("race_id")
    eh, rs, ah, ap, sp = [by_race(x) for x in (
        snapshot["entries"], snapshot["results"], horses, dividends, stored_payouts)]
    races = snapshot["races"].set_index("race_id")
    records = []
    for rid in sorted(ledger.loc[ledger.archive_full_field_price_verified.fillna(False), "race_id"]):
        page = selected.loc[rid]
        require(page.result_parse_status == "ok" and page.payout_parse_status == "ok"
                and bool(page.body_race_id_matches) and bool(page.html_date_in_scope), "Bad source page")
        raw = gzip.decompress((ROOT / page.archive_path).read_bytes())
        require(hashlib.sha256(raw).hexdigest() == page.archive_raw_sha256, "Archive raw SHA changed")
        html_text = raw.decode(page.decode_encoding, errors="replace")
        require(hashlib.sha256(html_text.encode("utf-8")).hexdigest() == page.html_utf8_sha256,
                "Archive UTF8 SHA changed")
        require(ap[rid].set_index("horse_number").payout_yen.to_dict()
                == sp[rid].set_index("horse_number").payout_yen.to_dict(), "Stored payout differs from HTML")
        frame = validate_race(eh[rid], rs[rid], ah[rid], ap[rid], mappings)
        info = races.loc[rid]
        frame["race_id"] = rid
        frame["race_date"] = str(info.race_date)
        frame["track_type"] = info.track_type
        frame["post_time"] = info.post_time
        frame["archive_path"] = page.archive_path
        frame["archive_html_sha256"] = page.html_utf8_sha256
        frame["archive_fetched_at"] = page.fetched_at
        frame["official_source"] = "netkeiba_result"
        records.append(frame)
    dataset = pd.concat(records, ignore_index=True)
    require(not dataset.duplicated(["race_id", "horse_id"]).any(), "Dataset duplicate identity")
    return dataset, ledger


def replace_verified_prices(raw, verified):
    """Replace entire proven fields before rebuilding current and lagged market features."""
    result = raw.copy()
    target = verified.loc[verified.track_type.ne("障")]
    raw_groups = by_race(result)
    changes = 0
    for rid, group in target.groupby("race_id", sort=False):
        original = raw_groups.get(rid)
        require(original is not None and set(original.horse_id) == set(group.horse_id),
                f"Fresh raw field differs: {rid}")
        require(len(original) == len(group) and not original.horse_id.duplicated().any()
                and not original.horse_number.duplicated().any(), "Duplicate fresh raw field")
        fixed = group.set_index("horse_id")
        for index in original.index:
            h = fixed.loc[result.at[index, "horse_id"]]
            require(result.at[index, "horse_number"] == h.horse_number, "Fresh raw number differs")
            changes += int(Decimal(str(result.at[index, "odds"])) != Decimal(h.final_odds_decimal))
            result.at[index, "odds"] = h.odds
            result.at[index, "popularity"] = h.popularity
    return result, changes


def build_features(snapshot, verified):
    corrected, changed = replace_verified_prices(snapshot["raw"], verified)
    features = market_ev.build_features(corrected)
    target_ids = set(verified.loc[verified.track_type.ne("障"), "race_id"])
    target = features.loc[features.race_id.isin(target_ids)].copy()
    require(set(target.race_id) == target_ids, "Missing rebuilt target")
    return target, dict(history_rows=len(corrected), history_races=int(corrected.race_id.nunique()),
        verified_replaced_races=len(target_ids), raw_target_price_differences=changed,
        input_history="current stored DB revision; unverified historical fields are not certified",
        feature_rebuild="all 2007+ flat rows; verified prices/popularity replaced before current/lag calculations")


def predict_mev(features):
    model = market_ev.EnsembleMarketEvModel.load(MEV, "mev-ens15-v1")
    cutoff = model.spec["training_cutoff_by_year"]["2026"]
    require(cutoff["train_through"] == 2025, "Future MEV training")
    require(features.year.eq(2026).all(), "Unexpected evaluation year")
    pred = market_ev.predict_ensemble(model, features)
    require(len(pred) == len(features), "MEV dropped a verified field")
    result = pred[["race_id", "horse_id", "win_prob", "booster", "booster_sha256"]].copy()
    result = result.rename(columns={"win_prob": "p_mev_raw"})
    result["p_mev"] = result.p_mev_raw.clip(1e-6, 1 - 1e-6)
    require(np.isfinite(result.p_mev).all(), "Invalid MEV probability")
    return result
