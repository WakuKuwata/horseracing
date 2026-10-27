"""Audit frozen prices, official payouts, timestamps and complete fields; never repair."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/155-saved-odds-settlement-audit"
SPEC = ROOT / "specs/155-saved-odds-settlement-audit"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
PRIOR151 = ROOT / "artifacts/151-roi-prediction-ablation/comparison/predictions.parquet"
CONFIG = dict(feature=155,window=["2007-01-01","2026-10-26"],exact="Decimal(odds)*100 == integer payout",
    near="0 < absolute difference <=10 yen; not exact",dead_heat="separate from ordinary price matching",
    archive_selection="latest stored version with result table, regardless of agreement",
    identity="exact stored IDs or explicit resolved mapping; never strip nk:",
    new_collection=False,database_write=False,model_fit=False,selection_change=False,
    can_adopt=False,official_source="stored netkeiba_result, no independent JRA confirmation")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def clean(value):
    if isinstance(value,dict):
        return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)):
        return [clean(v) for v in value]
    if isinstance(value,np.generic):
        return clean(value.item())
    if isinstance(value,Decimal):
        return str(value)
    if isinstance(value,(pd.Timestamp,)):
        return value.isoformat()
    if value is None or value is pd.NA or value is pd.NaT or (isinstance(value,float) and not np.isfinite(value)):
        return None
    return value


def write(path,value):
    Path(path).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False)+"\n")


def decimal(value):
    if value is None or pd.isna(value):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def price_match(odds,payout,dead_heat=False):
    if dead_heat:
        return "dead_heat_separate",None
    o,p = decimal(odds),decimal(payout)
    if o is None or o < 1 or p is None or p < 100:
        return "missing_or_invalid",None
    delta = p-o*100
    if delta == 0:
        return "exact",0.
    return ("near_10_yen" if abs(delta) <= 10 else "mismatch"),float(delta)


def timestamp_class(event,post):
    e = pd.to_datetime(event,utc=True,errors="coerce")
    p = pd.to_datetime(post,utc=True,errors="coerce")
    if pd.isna(e):
        return "missing_event"
    if pd.isna(p):
        return "post_time_unknown"
    return "before_post" if e < p else "at_or_after_post"


def field_digest(ids):
    return hashlib.sha256("\n".join(sorted(set(map(str,ids)))).encode()).hexdigest()


def archive_module():
    spec = importlib.util.spec_from_file_location("audit155_archive",HERE/"archive.py")
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


def read_snapshot(out):
    manifest = json.loads((out/"snapshot/manifest.json").read_text())
    if manifest["settings"]["read_only"] != "on" or manifest["settings"]["isolation"] != "repeatable read":
        raise ValueError("Snapshot was not read-only/repeatable")
    data = {}
    for name,record in manifest["tables"].items():
        path = ROOT/record["path"]
        if sha(path) != record["sha256"]:
            raise ValueError(f"Snapshot changed: {name}")
        data[name] = pd.read_parquet(path)
        if len(data[name]) != record["rows"]:
            raise ValueError("Snapshot row count differs")
    return data,manifest


def freeze(out):
    if (out/"protocol.json").exists():
        raise FileExistsError("Protocol exists")
    loader = archive_module()
    manifest_path = out/"snapshot/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    paths = [manifest_path,*[ROOT/r["path"] for r in manifest["tables"].values()],
        ROWS,PRIOR151,*loader.input_paths(),*loader.source_files(),
        *HERE.glob("*.py"),SPEC/"spec.md",SPEC/"plan.md",
        ROOT/"scripts/tests/test_odds_settlement_audit_155.py",
        ROOT/"scrape/src/horseracing_scrape/upsert.py",
        ROOT/"training/src/horseracing_training/market_ev.py",
        ROOT/"training/src/horseracing_training/attention_picks.py",
        ROOT/"api/src/horseracing_api/purchase_view.py"]
    hashes = {str(p.relative_to(ROOT)):sha(p) for p in sorted(set(paths))}
    write(out/"protocol.json",dict(config=CONFIG,inputs_and_source_sha256=hashes,
        snapshot_started_at=manifest["started_at"],python=sys.version,pandas=pd.__version__,numpy=np.__version__))
    (out/"protocol.sha256").write_text(sha(out/"protocol.json")+"\n")
    print(f"FROZEN {len(hashes)} inputs/sources",flush=True)


def verify(out):
    path = out/"protocol.json"
    if sha(path) != (out/"protocol.sha256").read_text().strip():
        raise ValueError("Protocol SHA changed")
    document = json.loads(path.read_text())
    if document["config"] != CONFIG:
        raise ValueError("Audit rules changed")
    for relative,digest in document["inputs_and_source_sha256"].items():
        if sha(ROOT/relative) != digest:
            raise ValueError(f"Input/source changed: {relative}")
    return document


def records_by_race(frame):
    groups = {}
    for row in frame.to_dict("records"):
        groups.setdefault(row["race_id"],[]).append(row)
    return groups


def inspect_race(race,horses,results,payouts):
    started = [h for h in horses if h["entry_status"] == "started"]
    ids = {h["horse_id"] for h in started}
    result_ids = {r["horse_id"] for r in results}
    numbers = [h["horse_number"] for h in started]
    number_ok = all(n is not None and pd.notna(n) and int(n) == n and n >= 1 for n in numbers)
    unique_numbers = number_ok and len(set(numbers)) == len(numbers)
    missing,extra = ids-result_ids,result_ids-ids
    winner_ids = {r["horse_id"] for r in results if r["result_status"] == "finished" and r["finish_order"] == 1}
    winners = [h for h in started if h["horse_id"] in winner_ids]
    winner_numbers = {int(h["horse_number"]) for h in winners if pd.notna(h["horse_number"])}
    payout_numbers = {int(p["horse_number"]) for p in payouts}
    field_ok = len(started) >= 2 and unique_numbers and not missing and not extra and bool(winners)
    payout_ok = (field_ok and winner_ids <= ids and len(winner_numbers) == len(winners)
        and winner_numbers == payout_numbers and len(payout_numbers) == len(payouts)
        and all(p["payout_yen"] >= 100 for p in payouts))
    if not results:
        field_status = "no_results"
    elif not field_ok:
        field_status = "inconsistent_or_incomplete"
    else:
        field_status = "complete"
    payout_status = "missing" if not payouts else ("consistent" if payout_ok else "inconsistent")
    delta = None
    if not field_ok:
        price_status = "field_not_complete"
    elif not payouts:
        price_status = "payout_missing"
    elif not payout_ok:
        price_status = "payout_inconsistent"
    elif len(winners) > 1:
        price_status = "dead_heat_separate"
    else:
        price_status,delta = price_match(winners[0]["odds"],payouts[0]["payout_yen"])
    prices = [decimal(h["odds"]) for h in started]
    all_prices = bool(started) and all(p is not None and p >= 1 for p in prices)
    inverse_sum = float(sum((Decimal(1)/p for p in prices),Decimal(0))) if all_prices else None
    return dict(race_id=race["race_id"],race_date=race["race_date"],year=int(race["race_date"][:4]),
        track_type=race["track_type"],n_entries=len(horses),n_started=len(started),n_results=len(results),
        result_status_counts=json.dumps(dict(Counter(r["result_status"] for r in results)),sort_keys=True),
        duplicate_starter_numbers=number_ok and not unique_numbers,invalid_starter_number=not number_ok,
        missing_result_ids=json.dumps(sorted(missing)),nonstarter_result_ids=json.dumps(sorted(extra)),
        n_missing_results=len(missing),n_nonstarter_results=len(extra),n_winners=len(winners),
        winner_numbers=json.dumps(sorted(winner_numbers)),payout_numbers=json.dumps(sorted(payout_numbers)),
        n_payouts=len(payouts),field_status=field_status,payout_status=payout_status,
        winner_price_status=price_status,winner_payout_minus_odds100_yen=delta,
        all_started_odds_valid=all_prices,inverse_odds_sum=inverse_sum,
        underround=bool(inverse_sum is not None and inverse_sum < 1-1e-12),
        latest_quote_observed_class=timestamp_class(race["win_odds_observed_at"],race["post_time"]),
        latest_quote_official_class=timestamp_class(race["win_odds_official_at"],race["post_time"]),
        quote_observed_present=pd.notna(race["win_odds_observed_at"]),
        quote_official_present=pd.notna(race["win_odds_official_at"]),
        post_time_present=pd.notna(race["post_time"]),current_field_digest=field_digest(ids))


def audit_races(data):
    hs,rs,ps = (records_by_race(data[name]) for name in ("horses","results","payouts"))
    rows = [inspect_race(r,hs.get(r["race_id"],[]),rs.get(r["race_id"],[]),ps.get(r["race_id"],[]))
            for r in data["races"].to_dict("records")]
    return pd.DataFrame(rows),hs,rs,ps


def resolved_mapping(frame):
    resolved = frame[(frame.source == "netkeiba") & frame.mapping_status.eq("mapped") & frame.canonical_id.notna()]
    if resolved.duplicated("source_id").any():
        raise ValueError("Ambiguous resolved mapping")
    return dict(zip(resolved.source_id,resolved.canonical_id,strict=True))


def attach_archives(ledger,data,pages,horses,payouts,current_horses,current_payouts):
    chosen = pages.loc[pages.selected].copy()
    if chosen.duplicated("race_id").any():
        raise ValueError("More than one selected archive")
    ah = records_by_race(horses)
    ap = records_by_race(payouts)
    mapping = resolved_mapping(data["id_mappings"])
    archive_results = []
    for page in chosen.to_dict("records"):
        rid = page["race_id"]
        db = [h for h in current_horses.get(rid,[]) if h["entry_status"] == "started"]
        ar = [h for h in ah.get(rid,[]) if h["is_started"]]
        dn = [h["horse_number"] for h in db]
        an = [h["horse_number"] for h in ar]
        clean_parse = (page["body_race_id_matches"] and page["result_parse_status"] == "ok"
            and page["html_date_in_scope"] == True
            and not any(h["row_parse_error"] or h["is_started"] is None for h in ah.get(rid,[])))
        numbers_ok = (bool(db) and len(set(dn)) == len(dn) and len(set(an)) == len(an)
            and set(dn) == set(an) and all(pd.notna(n) for n in dn+an))
        by_number = {h["horse_number"]:h for h in db}
        identity_ok = False
        raw_identity_ok = False
        different = missing = 0
        if numbers_ok:
            raw_identity_ok = all(h["horse_id"] == by_number[h["horse_number"]]["horse_id"] for h in ar)
            identity_ok = all(h["horse_id"] == by_number[h["horse_number"]]["horse_id"]
                or mapping.get(h["horse_id"]) == by_number[h["horse_number"]]["horse_id"] for h in ar)
            for h in ar:
                price = decimal(h["final_odds_text"])
                current = decimal(by_number[h["horse_number"]]["odds"])
                if price is None or current is None or price < 1 or current < 1:
                    missing += 1
                elif price != current:
                    different += 1
        exact_prices = numbers_ok and missing == 0 and different == 0
        db_payout = {int(p["horse_number"]):p["payout_yen"] for p in current_payouts.get(rid,[])}
        archive_payout = {int(p["horse_number"]):p["payout_yen"] for p in ap.get(rid,[])}
        p_valid = page["payout_parse_status"] == "ok" and bool(archive_payout)
        p_equal = p_valid and bool(db_payout) and db_payout == archive_payout
        hash_equal = bool(db_payout) and all(p["html_sha256"] == page["html_utf8_sha256"]
            for p in current_payouts.get(rid,[]))
        archive_results.append(dict(race_id=rid,archive_selected_path=page["archive_path"],
            archive_html_utf8_sha256=page["html_utf8_sha256"],archive_parse_usable=bool(clean_parse),
            archive_started_number_field_match=bool(numbers_ok),archive_raw_id_field_match=bool(raw_identity_ok),
            archive_resolved_id_field_match=bool(identity_ok),archive_missing_odds_count=missing,
            archive_different_odds_count=different,archive_all_started_odds_exact=bool(exact_prices),
            archive_full_field_price_verified=bool(clean_parse and identity_ok and exact_prices),
            archive_payout_parse_ok=bool(p_valid),archive_payout_matches_database=bool(p_equal),
            archive_payout_html_hash_matches=bool(hash_equal),
            archive_fetched_at=page["fetched_at"],archive_fetched_at_source=page["fetched_at_source"],
            archive_body_id_method=page["body_race_id_method"],
            archive_has_refund_text=bool(page["has_refund_text"]),
            archive_has_special_payout_text=bool(page["has_special_payout_text"])))
    return ledger.merge(pd.DataFrame(archive_results),on="race_id",how="left",validate="one_to_one")


def audit_saved_prices(data,ledger,current_horses):
    race_info = data["races"].set_index("race_id").to_dict("index")
    current = {rid:{h["horse_id"]:h for h in rows if h["entry_status"] == "started"}
        for rid,rows in current_horses.items()}
    mev_rows = []
    for (rid,model),group in data["mev"].groupby(["race_id","model_version"],sort=False):
        active = current.get(rid,{})
        info = race_info[rid]
        source = ["quote" if "oddsobs=quote" in v else "proxy" if "oddsobs=proxy" in v else "legacy_unknown"
            for v in group.logic_version]
        same_ids = set(group.horse_id) == set(active)
        n_price_differences = sum(decimal(p.odds_used) != decimal(active[p.horse_id]["odds"])
            for p in group.itertuples() if p.horse_id in active)
        n_number_differences = sum(p.horse_number != active[p.horse_id]["horse_number"]
            for p in group.itertuples() if p.horse_id in active)
        obs_after_compute = sum(pd.notna(p.odds_observed_at) and p.odds_observed_at > p.computed_at
            for p in group.itertuples())
        ev_residuals = [abs(decimal(p.expected_return)-decimal(p.win_prob)*decimal(p.odds_used))
            for p in group.itertuples()]
        ev_bad = sum(d != 0 for d in ev_residuals)
        classes = dict(Counter(timestamp_class(p.computed_at,info["post_time"]) for p in group.itertuples()))
        observed_classes = dict(Counter(timestamp_class(p.odds_observed_at,info["post_time"]) for p in group.itertuples()))
        mev_rows.append(dict(race_id=rid,model_version=model,n_rows=len(group),same_current_field=same_ids,
            missing_current_ids=json.dumps(sorted(set(active)-set(group.horse_id))),
            extra_saved_ids=json.dumps(sorted(set(group.horse_id)-set(active))),
            n_number_differences=n_number_differences,n_price_differences=n_price_differences,
            n_runs=int(group.run_id.nunique()),n_compute_times=int(group.computed_at.nunique()),
            source_counts=json.dumps(dict(Counter(source)),sort_keys=True),
            computed_time_classes=json.dumps(classes,sort_keys=True),observed_time_classes=json.dumps(observed_classes,sort_keys=True),
            pending_rows=int(group.result_pending_at_compute.sum()),observed_after_computed_rows=obs_after_compute,
            ev_identity_differences=ev_bad,ev_residual_gt_1e10=sum(d > Decimal("1e-10") for d in ev_residuals),
            ev_max_absolute_residual=float(max(ev_residuals,default=Decimal(0)))))
    mev = pd.DataFrame(mev_rows)
    latest_sources = {(p.race_id,p.run_id,p.horse_id):("quote" if "oddsobs=quote" in p.logic_version else
        "proxy" if "oddsobs=proxy" in p.logic_version else "legacy_unknown") for p in data["mev"].itertuples()
        if p.model_version == "mev-ens15-v1"}
    digests = ledger.set_index("race_id").current_field_digest.to_dict()
    picks = data["picks"].copy()
    picks["current_field_digest_match"] = [p.field_digest == digests.get(p.race_id) for p in picks.itertuples()]
    picks["computed_time_class"] = [timestamp_class(p.computed_at,p.post_time) for p in picks.itertuples()]
    picks["observed_time_class"] = [timestamp_class(p.odds_observed_at,p.post_time) for p in picks.itertuples()]
    picks["quote_source_provenance"] = [latest_sources.get((p.race_id,p.run_id,p.horse_id),"run_not_retained") for p in picks.itertuples()]
    scans = data["scans"].copy()
    scans["current_field_digest_match"] = [p.field_digest == digests.get(p.race_id) for p in scans.itertuples()]
    scans["computed_time_class"] = [timestamp_class(p.computed_at,p.post_time) for p in scans.itertuples()]
    chaos_rows = []
    for record in data["chaos"].to_dict("records"):
        field = json.loads(record["field_json"])
        ids = [h["horse_id"] for h in field]
        nums = [h["horse_number"] for h in field]
        rid = record["race_id"]
        chaos_rows.append(dict(race_id=rid,status=record["status"],n_saved=record["n"],n_field=len(field),
            unique_ids=len(ids) == len(set(ids)),unique_numbers=len(nums) == len(set(nums)),
            same_current_field=set(ids) == set(current.get(rid,{})),
            captured_time_class=timestamp_class(record["captured_at"],race_info[rid]["post_time"]),
            capture_strength=record["capture_strength"],capture_trigger=record["capture_trigger"]))
    recs = []
    for r in data["recommendations"].itertuples():
        selection = json.loads(r.selection_json)
        active = current.get(r.race_id,{})
        by_number = {h["horse_number"]:h for h in active.values()}
        unique = len(by_number) == len(active)
        chosen = by_number.get(selection.get("horse_number")) if unique else None
        if chosen is not None and chosen["horse_id"] != selection.get("horse_id"):
            chosen = None
        recs.append(dict(race_id=r.race_id,recommendation_id=r.recommendation_id,
            selection_resolves_current_starter=chosen is not None,
            frozen_odds_valid=decimal(r.odds_used) is not None and decimal(r.odds_used) >= 1,
            frozen_odds_equals_current=(chosen is not None and decimal(r.odds_used) == decimal(chosen["odds"])),
            computed_time_class=timestamp_class(r.computed_at,race_info[r.race_id]["post_time"]),
            is_estimated_odds=r.is_estimated_odds))
    return mev,picks,scans,pd.DataFrame(chaos_rows),pd.DataFrame(recs)


def audit_research(ledger,current_horses,current_payouts,archive_horses):
    columns = ["race_id","horse_id","horse_number","race_date","year","odds","won","race_ok","dead_heat","field_size"]
    research = pd.read_parquet(ROWS,columns=columns)
    eval151 = set(pd.read_parquet(PRIOR151,columns=["race_id"]).race_id)
    current = {rid:{h["horse_id"]:h for h in rows if h["entry_status"] == "started"}
        for rid,rows in current_horses.items()}
    rows = []
    archived = records_by_race(archive_horses)
    for rid,group in research.groupby("race_id",sort=False):
        prices = [decimal(p) for p in group.odds]
        valid = all(p is not None and p >= 1 for p in prices)
        inverse = float(sum((Decimal(1)/p for p in prices),Decimal(0))) if valid else None
        active = current.get(rid,{})
        n_different = sum(decimal(r.odds) != decimal(active[r.horse_id]["odds"])
            for r in group.itertuples() if r.horse_id in active)
        winners = group[group.won.eq(1)]
        dividends = current_payouts.get(rid,[])
        if bool(group.dead_heat.any()) or len(winners) > 1:
            old_price = "dead_heat_separate"
        elif len(winners) != 1:
            old_price = "winner_set_invalid"
        elif not dividends:
            old_price = "payout_missing"
        elif len(dividends) != 1 or winners.horse_number.iloc[0] != dividends[0]["horse_number"]:
            old_price = "payout_winner_set_mismatch"
        else:
            old_price,_ = price_match(winners.odds.iloc[0],dividends[0]["payout_yen"])
        ar = [h for h in archived.get(rid,[]) if h["is_started"]]
        old_numbers = group.horse_number.tolist()
        ar_numbers = [h["horse_number"] for h in ar]
        number_match = (bool(ar) and len(set(old_numbers)) == len(old_numbers)
            and len(set(ar_numbers)) == len(ar_numbers) and set(old_numbers) == set(ar_numbers))
        old_by_number = dict(zip(group.horse_number,group.odds,strict=True))
        archived_diff = sum(decimal(h["final_odds_text"]) != decimal(old_by_number[h["horse_number"]])
            for h in ar) if number_match else None
        rows.append(dict(race_id=rid,research_date=str(group.race_date.iloc[0]),research_year=int(group.year.iloc[0]),
            research_rows=len(group),research_race_ok=bool(group.race_ok.all()),research_dead_heat=bool(group.dead_heat.any()),
            research_winners=int(group.won.sum()),research_duplicate_numbers=bool(group.horse_number.duplicated().any()),
            research_field_size_matches=bool(group.field_size.eq(len(group)).all()),
            research_same_current_field=set(group.horse_id) == set(active),
            research_current_odds_differences=n_different,research_inverse_odds_sum=inverse,
            research_winner_vs_stored_payout=old_price,research_archive_number_field_match=number_match,
            research_archive_odds_differences=archived_diff,
            research_underround=bool(inverse is not None and inverse < 1-1e-12),in_151_evaluation=rid in eval151))
    result = pd.DataFrame(rows)
    return result.merge(ledger,on="race_id",how="left",validate="one_to_one")


def counts(frame,column):
    return {str(k):int(v) for k,v in frame[column].fillna("unknown").value_counts(dropna=False).items()}


def summarize(ledger,mev,picks,scans,chaos,recs,research,pages,data):
    def scope_summary(frame):
        return dict(races=len(frame),field=counts(frame,"field_status"),payout=counts(frame,"payout_status"),
            winner_price=counts(frame,"winner_price_status"),underround=int(frame.underround.sum()),
            archive_selected=int(frame.archive_selected_path.notna().sum()),
            archive_number_field_matches=int(frame.archive_started_number_field_match.fillna(False).sum()),
            archive_resolved_id_field_matches=int(frame.archive_resolved_id_field_match.fillna(False).sum()),
            archive_all_odds_exact=int(frame.archive_all_started_odds_exact.fillna(False).sum()),
            archive_full_field_price_verified=int(frame.archive_full_field_price_verified.fillna(False).sum()),
            archived_payout_matches=int(frame.archive_payout_matches_database.fillna(False).sum()),
            quoted_observed=counts(frame,"latest_quote_observed_class"),quoted_official=counts(frame,"latest_quote_official_class"))
    total = scope_summary(ledger)
    total["by_year"] = {str(y):scope_summary(g) for y,g in ledger.groupby("year")}
    total["by_track_type"] = {str(t):scope_summary(g) for t,g in ledger.groupby("track_type",dropna=False)}
    total["duplicate_starter_number_races"] = int(ledger.duplicate_starter_numbers.sum())
    total["incomplete_result_races"] = int((ledger.n_missing_results > 0).sum())
    total["payout_source_counts"] = counts(data["payouts"],"source")
    total["post_time_present_races"] = int(ledger.post_time_present.sum())
    total["quote_observed_present_races"] = int(ledger.quote_observed_present.sum())
    total["quote_official_present_races"] = int(ledger.quote_official_present.sum())
    archives = dict(versions=len(pages),race_ids=int(pages.race_id.nunique()),selected=int(pages.selected.sum()),
        result_parse=counts(pages.loc[pages.selected],"result_parse_status"),
        payout_parse=counts(pages.loc[pages.selected],"payout_parse_status"),
        read_errors=int(pages.archive_read_error.notna().sum()),
        selected_body_id_mismatch=int((pages.selected & ~pages.body_race_id_matches).sum()),
        selected_refund_pages=int((pages.selected & pages.has_refund_text).sum()),
        selected_special_payout_pages=int((pages.selected & pages.has_special_payout_text).sum()))
    snapshots = dict(mev=dict(groups=len(mev),rows=len(data["mev"]),models=counts(mev,"model_version"),
        field_mismatch_groups=int((~mev.same_current_field).sum()),price_changed_groups=int((mev.n_price_differences > 0).sum()),
        inconsistent_run_groups=int((mev.n_runs != 1).sum()),ev_identity_differences=int(mev.ev_identity_differences.sum()),
        ev_residual_gt_1e10_rows=int(mev.ev_residual_gt_1e10.sum()),
        ev_max_absolute_residual=float(mev.ev_max_absolute_residual.max()),
        observed_after_compute_rows=int(mev.observed_after_computed_rows.sum()),
        quote_source_rows=dict(sum((Counter(json.loads(s)) for s in mev.source_counts),Counter())),
        computed_time_rows=dict(sum((Counter(json.loads(s)) for s in mev.computed_time_classes),Counter())),
        observed_time_rows=dict(sum((Counter(json.loads(s)) for s in mev.observed_time_classes),Counter()))),
        picks=dict(rows=len(picks),kinds=counts(picks,"kind"),source=counts(picks,"quote_source_provenance"),
            time=counts(picks,"computed_time_class"),field_match=counts(picks,"current_field_digest_match")),
        scans=dict(rows=len(scans),time=counts(scans,"computed_time_class"),field_match=counts(scans,"current_field_digest_match")),
        chaos=dict(rows=len(chaos),status=counts(chaos,"status"),time=counts(chaos,"captured_time_class"),
            field_match=counts(chaos,"same_current_field")),
        recommendations=dict(rows=len(recs),selection_resolves=counts(recs,"selection_resolves_current_starter"),
            frozen_odds_valid=counts(recs,"frozen_odds_valid"),current_price_equal=counts(recs,"frozen_odds_equals_current"),
            time=counts(recs,"computed_time_class")))
    subsets = {}
    for name,frame in {"all_research":research,"151_evaluation":research[research.in_151_evaluation]}.items():
        subsets[name] = dict(races=len(frame),rows=int(frame.research_rows.sum()),
            research_underround=int(frame.research_underround.sum()),
            duplicate_number_races=int(frame.research_duplicate_numbers.sum()),
            current_field_mismatch=int((~frame.research_same_current_field).sum()),
            current_odds_changed_races=int((frame.research_current_odds_differences > 0).sum()),
            current_payout=counts(frame,"payout_status"),current_winner_price=counts(frame,"winner_price_status"),
            research_winner_vs_stored_payout=counts(frame,"research_winner_vs_stored_payout"),
            research_archive_compared_races=int(frame.research_archive_number_field_match.sum()),
            research_archive_price_changed_races=int(frame.research_archive_odds_differences.fillna(0).gt(0).sum()),
            current_archive_all_odds_exact=int(frame.archive_all_started_odds_exact.fillna(False).sum()))
    return dict(database=total,archives=archives,saved_snapshots=snapshots,research=subsets)


def markdown(result):
    db = result["database"]
    lines = ["# 155 保存オッズ・精算品質監査", "", "新規収集・修復・DB更新・学習なし。保存DBをREAD ONLY/REPEATABLE READで抽出して照合。", "",
        f"対象{db['races']:,}レース（2007年以降）。公式払戻のsourceは保存netkeiba_resultで、JRA独立照合ではない。", "",
        "## 現DBの独立品質軸", "", f"- 出走/結果: {db['field']}",f"- 払戻勝馬集合: {db['payout']}",
        f"- 通常勝馬価格: {db['winner_price']}",f"- 保存HTML選択あり: {db['archive_selected']:,}レース",
        f"- 原HTML全頭価格一致（馬番整合）: {db['archive_all_odds_exact']:,}レース",
        f"- 原HTMLのparse・ID・全頭価格まで整合: {db['archive_full_field_price_verified']:,}レース",
        f"- 原HTMLと保存払戻一致: {db['archived_payout_matches']:,}レース",
        f"- 最新quote取得時刻あり: {db['quote_observed_present_races']:,} / 発走時刻あり: {db['post_time_present_races']:,}",
        f"- 現在underround: {db['underround']:,}レース", "", "## 年別", "",
        "| 年 | 全race | 結果完全 | 公式払戻整合 | 勝馬価格exact | near10円 | mismatch | HTML全頭価格一致 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for year,r in db["by_year"].items():
        p = r["winner_price"]
        lines.append(f"| {year} | {r['races']:,} | {r['field'].get('complete',0):,} | {r['payout'].get('consistent',0):,} | "
            f"{p.get('exact',0):,} | {p.get('near_10_yen',0):,} | {p.get('mismatch',0):,} | {r['archive_all_odds_exact']:,} |")
    lines += ["", "## 研究保存物との違い", ""]
    for name,record in result["research"].items():
        lines += [f"- {name}: {json.dumps(record,ensure_ascii=False)}"]
    lines += ["", "## 保存snapshotの来歴", "", "```json",json.dumps(clean(result["saved_snapshots"]),ensure_ascii=False,indent=2),"```", "",
        "## 利用上の限界", "",
        "- 勝馬の価格一致は全頭確定の証明ではない。±10円は完全一致と分けた。同着は通常式から分離。",
        "- quote公表時刻NULLやhorse updated_atは確定価格/取得時点の証明ではない。部分更新でもrace時刻が付く。",
        "- 現在の修復済みDBを過去研究へ遡及させない。保存MEVのprice変更は必ずしも誤りでなく判断時と現在値の違い。",
        "- 元HTMLと現在DBはいずれも保存された限られた資料。未保存分を新規取得せず、不明として残した。",
        "- 実購入精算は現行purchase_viewが保存oddsを使用し、attentionの公式払戻精算とは別経路。今回は修正しない。",
        "",f"Protocol SHA256: `{result['protocol_sha256']}`", ""]
    return "\n".join(lines)


def run(out):
    verify(out)
    destination = out/"audit"
    if destination.exists():
        raise FileExistsError("Audit outputs exist")
    data,manifest = read_snapshot(out)
    print("LOADED snapshot",flush=True)
    pages,archive_horses,archive_payouts = archive_module().load_archives()
    print(f"PARSED archives {len(pages):,} versions",flush=True)
    ledger,hs,rs,ps = audit_races(data)
    ledger = attach_archives(ledger,data,pages,archive_horses,archive_payouts,hs,ps)
    print("AUDITED current races",flush=True)
    mev,picks,scans,chaos,recs = audit_saved_prices(data,ledger,hs)
    print("AUDITED saved snapshots",flush=True)
    research = audit_research(ledger,hs,ps,archive_horses)
    result = summarize(ledger,mev,picks,scans,chaos,recs,research,pages,data)
    result.update(protocol_sha256=sha(out/"protocol.json"),config=CONFIG,snapshot_settings=manifest["settings"])
    destination.mkdir()
    frames = dict(races=ledger,archive_pages=pages,archive_horses=archive_horses,archive_payouts=archive_payouts,
        mev=mev,picks=picks,scans=scans,chaos=chaos,recommendations=recs,research=research)
    for name,frame in frames.items():
        frame.to_parquet(destination/(name+".parquet"),index=False)
    anomalies = ledger[(ledger.field_status != "complete") | ledger.underround
        | ledger.winner_price_status.isin(["near_10_yen","mismatch","payout_inconsistent"])
        | ledger.archive_different_odds_count.fillna(0).gt(0)]
    anomalies.to_csv(destination/"anomalies.csv",index=False)
    research[(research.research_underround & research.in_151_evaluation)
        | research.race_id.eq("202504040407")].to_csv(destination/"known_151_cases.csv",index=False)
    write(destination/"summary.json",result)
    (destination/"report.md").write_text(markdown(result))
    print(json.dumps(clean(dict(races=len(ledger),summary=str(destination/"summary.json")))),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command",choices=["freeze","verify","run"])
    parser.add_argument("--out",type=Path,default=OUT)
    args = parser.parse_args()
    {"freeze":freeze,"verify":verify,"run":run}[args.command](args.out)
