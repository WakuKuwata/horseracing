"""Independent 157 material and numerical audit; imports no primary 157 modules."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import pickle
import re

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "artifacts/157-verified-roi-comparison"
OLD = ROOT / "artifacts/155-saved-odds-settlement-audit"
MODEL = ROOT / "artifacts/market_ev/mev-ens15-v1"
RECENT = ROOT / "artifacts/135-mixture-reproducibility/recent-owned-54586a4bf86c"
CAPTURE = ROOT / "artifacts/133-prospective-paired-comparison/records/capture"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def close(x, y, atol=1e-10):
    np.testing.assert_allclose(x, y, atol=atol, rtol=0, equal_nan=True)


def stamp(value):
    return pd.to_datetime(value, utc=True, errors="coerce")


def check(condition, label):
    if not condition:
        raise AssertionError(label)


def text(s):
    return re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]*>", "", s)))


def printed_race(content):
    """A separate strict parser for these saved All_Result_Table/Tansho documents."""
    table = re.search(r'<table\b[^>]*\bid="All_Result_Table"[^>]*>(.*?)</table>', content, re.S)
    check(table is not None, "No result table")
    result = {}
    for attrs, body in re.findall(r"<tr\b([^>]*)>(.*?)</tr>", table.group(1), re.S):
        if "HorseList" not in attrs:
            continue
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", body, re.S)
        check(len(cells) >= 11, "Short printed result row")
        rank = text(cells[0])
        if rank in {"取消", "除外"}:
            continue
        number = int(text(cells[2]))
        horse = re.search(r"/horse/([0-9A-Za-z]+)", cells[3])
        check(horse is not None and number not in result, "Bad printed horse identity")
        odds = Decimal(text(cells[10]).replace(",", ""))
        status = "finished" if rank.isdigit() else {
            "中止": "stopped", "中": "stopped", "失格": "disqualified", "失": "disqualified"
        }[rank]
        result[number] = dict(raw_id=horse.group(1), odds=odds, status=status,
                              rank=int(rank) if rank.isdigit() else None)
    win = re.search(r'<tr\b[^>]*class="Tansho"[^>]*>(.*?)</tr>', content, re.S)
    check(win is not None, "No official win payout row")
    number_cell = re.search(r'<td\b[^>]*class="Result"[^>]*>(.*?)</td>', win.group(1), re.S)
    payout_cell = re.search(r'<td\b[^>]*class="Payout"[^>]*>(.*?)</td>', win.group(1), re.S)
    nums = [int(t) for t in re.findall(r"<span\b[^>]*>\s*(\d+)\s*</span>", number_cell.group(1))]
    amounts = [int(v.replace(",", "")) for v in re.findall(r"([\d,]+)円", text(payout_cell.group(1)))]
    check(len(nums) == len(amounts) > 0, "Payout parsing count differs")
    return result, dict(zip(nums, amounts, strict=True))


def materials():
    manifest = json.loads((BASE / "snapshot-v2/manifest.json").read_text())
    check(manifest["settings"]["read_only"] == "on" and manifest["settings"]["isolation"] == "repeatable read", "Unsafe snapshot")
    db = {}
    for name, item in manifest["tables"].items():
        path = ROOT / item["path"]
        check(sha(path) == item["sha256"], "Changed snapshot " + name)
        db[name] = pd.read_parquet(path)
        check(len(db[name]) == item["rows"], "Snapshot length " + name)
    v = pd.read_parquet(BASE / "rebuilt/verified_rows.parquet")
    features = pd.read_parquet(BASE / "rebuilt/features.parquet")
    pop = pd.read_parquet(BASE / "rebuilt/population.parquet")
    oldmaps = pd.read_parquet(OLD / "snapshot/id_mappings.parquet")
    mapping = dict(zip(oldmaps.loc[oldmaps.source.eq("netkeiba") & oldmaps.mapping_status.eq("mapped"), "source_id"],
                       oldmaps.loc[oldmaps.source.eq("netkeiba") & oldmaps.mapping_status.eq("mapped"), "canonical_id"], strict=True))
    archived = pd.read_parquet(OLD / "audit/archive_pages.parquet")
    pages = archived.loc[archived.selected].set_index("race_id")
    entries = {r: g for r, g in db["entries"].groupby("race_id")}
    results = {r: g for r, g in db["results"].groupby("race_id")}
    checked = []
    for rid, g in v.groupby("race_id"):
        page = pages.loc[rid]
        gz = ROOT / page.archive_path
        check(sha(gz) == page.archive_gz_sha256, "Compressed archive changed")
        raw = gzip.decompress(gz.read_bytes())
        check(hashlib.sha256(raw).hexdigest() == page.archive_raw_sha256, "Raw archive changed")
        content = raw.decode(page.decode_encoding, errors="replace")
        check(hashlib.sha256(content.encode()).hexdigest() == page.html_utf8_sha256, "Decoded archive changed")
        printed, payouts = printed_race(content)
        field = entries[rid].loc[entries[rid].entry_status.eq("started")]
        check(set(field.horse_id) == set(results[rid].horse_id) == set(g.horse_id), "Starter/result set")
        check(len(field) == len(g) and field.horse_number.is_unique, "Whole field cardinality")
        check(set(printed) == set(g.horse_number), "HTML whole-number field")
        for r in g.itertuples():
            h = printed[r.horse_number]
            check(h["raw_id"] == r.horse_id or mapping.get(h["raw_id"]) == r.horse_id, "Unproven ID")
            check(h["raw_id"] == r.html_horse_id, "HTML raw ID")
            check(h["odds"] == Decimal(r.final_odds_decimal), "Full-field price")
            close(float(h["odds"]), r.odds, 0)
            check(h["status"] == r.result_status and (h["rank"] == r.finish_order or h["rank"] is None), "Printed rank/status")
            check(r.won == (h["rank"] == 1) and r.official_payout_yen == payouts.get(r.horse_number, 0), "Official accounting input")
        winners = set(g.loc[g.won, "horse_number"])
        check(winners == set(payouts), "Payout/winner set")
        if len(winners) == 1:
            winner = g.loc[g.won].iloc[0]
            check(Decimal(winner.final_odds_decimal)*100 == int(winner.official_payout_yen), "Final winner/payout")
        checked.append(rid)
    check(len(checked) == 96 and len(v) == 1308, "Verified dataset count")

    # Independently rebuild current market features and price-dependent horse/human histories.
    raw = db["raw"].copy()
    for rid, g in v.loc[v.track_type.ne("障")].groupby("race_id"):
        ix = raw.index[raw.race_id.eq(rid)]
        fixed = g.set_index("horse_id")
        check(len(ix) == len(g) and set(raw.loc[ix, "horse_id"]) == set(g.horse_id), "Raw whole field")
        raw.loc[ix, "odds"] = [fixed.loc[h, "odds"] for h in raw.loc[ix, "horse_id"]]
        raw.loc[ix, "popularity"] = [fixed.loc[h, "popularity"] for h in raw.loc[ix, "horse_id"]]
    raw["race_date"] = pd.to_datetime(raw.race_date)
    raw["odds"] = pd.to_numeric(raw.odds, errors="coerce")
    raw["won"] = raw.result_status.eq("finished") & raw.finish_order.eq(1)
    inverse = 1/raw.odds
    raw["q"] = inverse / inverse.groupby(raw.race_id).transform("sum")
    hist = raw.sort_values(["horse_id", "race_date", "race_id"]).copy()
    hist["prev_odds"] = hist.groupby("horse_id").odds.shift()
    hist["prev_q"] = hist.groupby("horse_id").q.shift()
    target = features.set_index(["race_id", "horse_id"])
    identity = pd.MultiIndex.from_frame(hist[["race_id", "horse_id"]])
    existing = hist.loc[identity.isin(target.index)].set_index(["race_id", "horse_id"]).reindex(target.index)
    for c in ("q", "prev_odds", "prev_q"):
        close(existing[c], target[c], 1e-12)
    for key, prefix in (("jockey_id", "jockey"), ("trainer_id", "trainer")):
        histories = {k: g.sort_values("race_date") for k, g in raw.groupby(key)}
        for r in features.itertuples():
            name = getattr(r, key)
            if pd.isna(name):
                continue
            source = histories[name]
            past = source.loc[source.race_date.lt(r.race_date)]
            window = past.loc[past.race_date.ge(r.race_date-pd.Timedelta(days=365))]
            close(getattr(r, prefix+"_excess_all"), past.won.sum()-past.q.sum(), 2e-8)
            close(getattr(r, prefix+"_excess_365"), window.won.sum()-window.q.sum(), 2e-8)

    # Re-evaluate every DB run before choosing the latest eligible one.
    bundle_path = ROOT / "artifacts/129-candidate-mixture-serving/bundle.json"
    bundle = json.loads(bundle_path.read_text())
    bundle_hash = sha(bundle_path)
    check(bundle["train_through"] == "2026-08-23", "Legacy train cutoff")
    registration = json.loads(db["model"].iloc[0].metrics_summary)
    check(registration["registration"]["bundle_sha256"] == bundle_hash, "Registered bundle")
    for member in bundle["members"]:
        for filename, digest in member["files"].items():
            check(sha(bundle_path.parent/member["artifact_dir"]/filename) == digest, "Legacy member bytes")
    rr = db["races"].set_index("race_id")
    ps = {k: g for k,g in db["predictions"].groupby("prediction_run_id")}
    fs = {k: g for k,g in db["features"].groupby("prediction_run_id")}
    elig, selected = [], {}
    for run in db["runs"].itertuples():
        race = rr.loc[run.race_id]
        post = stamp(race.post_time)
        need = {"feat=features-021", f"mix=6:{bundle_hash[:12]}", "coef=2026", "wregime=serving"}
        if not need <= set(run.logic_version.split(";")) or str(race.race_date) <= bundle["train_through"]:
            continue
        if pd.isna(post) or not stamp(run.computed_at) < post:
            continue
        p, f = ps.get(run.prediction_run_id), fs.get(run.prediction_run_id)
        field = entries[run.race_id].loc[entries[run.race_id].entry_status.eq("started")]
        ids = set(field.horse_id)
        if p is None or f is None or not (len(p)==len(f)==len(ids)) or set(p.horse_id)!=ids or set(f.horse_id)!=ids:
            continue
        if not field.horse_number.is_unique or field.horse_number.isna().any():
            continue
        times_ok = all(stamp(g[col]).notna().all() and stamp(g[col]).lt(post).all()
                       for g in (p,f) for col in ("created_at", "updated_at"))
        payloads = [json.loads(t) for t in f.features_json]
        null = all(all(k in t and t[k] is None for k in ("weight", "weight_diff", "carried_weight_ratio")) for t in payloads)
        prob = pd.to_numeric(p.win_prob, errors="coerce").to_numpy(float)
        if not times_ok or not null or not f.feature_version.eq("features-021").all() or not np.isfinite(prob).all() or not ((prob>0)&(prob<1)).all() or not np.isclose(prob.sum(),1,atol=1e-8,rtol=0):
            continue
        elig.append(run.prediction_run_id)
        sort = (stamp(run.computed_at), run.prediction_run_id)
        if run.race_id not in selected or sort > selected[run.race_id][0]:
            selected[run.race_id] = (sort, p.set_index("horse_id").win_prob.astype(float).to_dict())
    check(len(elig)==52 and len(selected)==40, "Eligible DB run count")
    forecast = {rid: (p, "db_prestart_preweight") for rid,(_,p) in selected.items()}
    receipt_paths = {}
    for name in ("execution-receipt.json", "input-receipt.json"):
        receipt = json.loads((RECENT/name).read_text())
        check(receipt["no_outcomes_read"] is True, "135 outcomes provenance")
        receipt_paths.update(receipt["files"])
    for path, digest in receipt_paths.items():
        check(sha(path)==digest, "135 preserved material SHA")
    with (RECENT/"predictions.pkl").open("rb") as handle:
        old = pickle.load(handle)
    for rid, ids in old["ids"].items():
        if rid not in forecast:
            forecast[rid] = (dict(zip(ids, np.asarray(old["predictions"]["preweight"]["baseline"][rid])[:,0], strict=True)), "135_owned_baseline_preweight")
    for path in sorted(CAPTURE.glob("*.json")):
        record = json.loads(path.read_text())
        rid = record["race_id"]
        if rid not in forecast:
            forecast[rid] = ({h: p[0] for h,p in record["regimes"]["preweight"]["new"].items()}, "133_capture_preweight")
    expected_ids, reasons = set(), {}
    for rid,g in v.groupby("race_id"):
        if g.track_type.iloc[0]=="障": reason="jump_not_in_mev_scope"
        elif g.dead_heat.any(): reason="dead_heat_not_in_151_primary"
        elif g.race_date.iloc[0] <= "2026-08-23": reason="legacy_model_training_period"
        elif rid not in forecast: reason="no_proven_preweight_prediction"
        elif set(forecast[rid][0]) != set(g.horse_id): reason="legacy_full_field_mismatch"
        else:
            reason="accepted"; expected_ids.add(rid)
        reasons[rid] = reason
    check(expected_ids == set(pop.race_id), "Independent comparison race population")
    coverage = pd.read_csv(BASE/"rebuilt/coverage.csv", dtype={"race_id":str}).set_index("race_id")
    check(all(coverage.loc[rid,"reason"]==reason for rid,reason in reasons.items()), "Coverage classification")
    for rid,g in pop.groupby("race_id"):
        check(set(g.horse_id)==set(forecast[rid][0]), "Primary legacy whole field")
        close(g.p_pred_preweight, [forecast[rid][0][h] for h in g.horse_id], 0)
        check(g.prediction_source.eq(forecast[rid][1]).all(), "Fixed priority source")
        close(g.q, (1/g.odds)/(1/g.odds).sum(), 1e-12)
        check(g.won.sum()==1 and g.race_date.iloc[0]>g.model_train_through.max(), "Evaluation after model training")
    return dict(verified_races=len(checked), verified_horses=len(v), comparison_races=pop.race_id.nunique(), comparison_horses=len(pop), days=pop.race_date.nunique(),
                html_price_and_payout_independent_parser=True, feature_checks=["q","prev_odds","prev_q","jockey_excess_all","jockey_excess_365","trainer_excess_all","trainer_excess_365"],
                independently_eligible_db_runs=len(elig), independently_selected_db_races=len(selected), exclusions=dict(Counter(reasons.values())),
                preserved135_sha_files=len(receipt_paths), source_races=pop.groupby("prediction_source").race_id.nunique().to_dict()), features, pop


def numerical(features, pop):
    out=BASE/"comparison"
    observed=pd.read_parquet(out/"predictions.parquet")
    summary=json.loads((out/"summary.json").read_text())
    protocol=json.loads((BASE/"protocol.json").read_text())
    check(sha(BASE/"protocol.json")== (BASE/"protocol.sha256").read_text().strip(), "Protocol identity")
    for p,digest in protocol["inputs_and_source_sha256"].items():
        check(sha(ROOT/p)==digest, "Frozen input/source changed: "+p)
    spec=json.loads((MODEL/"ensemble.spec.json").read_text())
    manifest=json.loads((MODEL/"ensemble_2026.json").read_text())
    check(spec["training_cutoff_by_year"]["2026"]["train_through"]==2025,"MEV cutoff")
    matrix=features[spec["features"]].astype(float).copy()
    for cat in spec["cats"]:
        mapping=spec["cat_maps"][cat]
        matrix[cat]=[mapping.get(str(v),np.nan) if pd.notna(v) else np.nan for v in features[cat]]
    x=matrix.to_numpy(np.float32)
    probs=[]
    for item in manifest["members"]:
        path=MODEL/item["path"]
        check(sha(path)==item["sha256"],"MEV booster checksum")
        booster=lgb.Booster(model_str=path.read_text())
        probs.append(booster.predict(x,num_threads=1))
    mev=features[["race_id","horse_id"]].copy();mev["raw"]=np.mean(probs,axis=0)
    mainmev=pd.read_parquet(out/"recomputed_mev.parquet").set_index(["race_id","horse_id"])
    mm=mev.set_index(["race_id","horse_id"]).reindex(mainmev.index)
    close(mm.raw,mainmev.p_mev_raw,1e-14)
    d=pop.merge(mev,on=["race_id","horse_id"],validate="one_to_one")
    d["p_mev"]=d.raw.clip(1e-6,1-1e-6)
    coeff=json.loads((ROOT/"artifacts/151-roi-prediction-ablation/comparison/summary.json").read_text())["coefficients"]["2026"]
    accounts={}; calculated={}
    for name,cols in {"roi_only":["q","p_mev"],"plus_prediction":["q","p_mev","p_pred_preweight"]}.items():
        check(coeff[name]["fit_through"]=="2025-12-28", "Blend cutoff")
        value=np.zeros(len(d)); chosen=np.zeros(len(d),bool)
        for rid,g in d.groupby("race_id",sort=False):
            logits=sum(coeff[name]["coefficients"][c]*np.log(g[c].to_numpy()) for c in cols)
            p=np.exp(logits-logits.max());p/=p.sum();value[g.index]=p
            candidates=[(int(ix),float(p[j]*g.odds.iloc[j]),int(g.horse_number.iloc[j]),str(g.horse_id.iloc[j])) for j,ix in enumerate(g.index) if p[j]*g.odds.iloc[j]>1]
            for ix,*_ in sorted(candidates,key=lambda t:(-t[1],t[2],t[3]))[:10]:chosen[ix]=True
        check(len(d)==len(observed),"Numerical population size")
        close(value,observed[name],1e-14);check(np.array_equal(chosen,observed[name+"_selected"]),"Ticket selection "+name)
        records=[]
        for rid,g in d.groupby("race_id",sort=False):
            picks=g.loc[chosen[g.index]]
            stake=100*len(picks);payout=int(picks.official_payout_yen.sum())
            records.append(dict(race_id=rid,race_date=g.race_date.iloc[0],post_time=g.post_time.iloc[0],stake=stake,payout=payout,tickets=len(picks),hits=int(picks.won.sum()),profit=payout-stake,budget=1000))
        race=pd.DataFrame(records).sort_values(["race_date","post_time","race_id"]).reset_index(drop=True)
        primary=pd.read_parquet(out/(name+"_races.parquet"))
        pd.testing.assert_frame_equal(race,primary,check_dtype=False)
        total_stake=int(race.stake.sum());total_payout=int(race.payout.sum());cum=np.r_[0.,race.profit.to_numpy().cumsum()]
        stats=dict(races=len(race),days=race.race_date.nunique(),tickets=int(race.tickets.sum()),hit_tickets=int(race.hits.sum()),stake_yen=total_stake,payout_yen=total_payout,profit_yen=total_payout-total_stake,roi=total_payout/total_stake if total_stake else None,no_bet_races=int(race.stake.eq(0).sum()),common_budget_yen=1000*len(race),profit_per_common_budget=float(race.profit.sum()/(1000*len(race))),winner_nll=float(-np.log(value[d.won.to_numpy(bool)]).mean()),max_drawdown_yen=float((np.maximum.accumulate(cum)-cum).max()))
        for k,v in stats.items():
            if v is None:check(summary["arms"][name][k] is None,k)
            else:close(v,summary["arms"][name][k],1e-12)
        calculated[name]=stats;accounts[name]=race
    a=accounts["roi_only"].set_index("race_id");b=accounts["plus_prediction"].set_index("race_id").reindex(a.index)
    daily=pd.DataFrame(dict(day=a.race_date,sa=a.stake,pa=a.payout,da=a.profit,ba=a.budget,sb=b.stake,pb=b.payout,db=b.profit)).groupby("day",sort=True).sum()
    totals=daily.to_numpy()[np.random.default_rng(20261026).integers(0,len(daily),size=(4000,len(daily)))].sum(axis=1)
    valid=(totals[:,0]>0)&(totals[:,4]>0)
    roi=np.full(4000,np.nan);roi[valid]=totals[valid,5]/totals[valid,4]-totals[valid,1]/totals[valid,0]
    profit=(totals[:,6]-totals[:,2])/totals[:,3]
    q=[.0125,.9875]
    if valid.all():close(np.quantile(roi,q),summary["paired"]["roi_difference_interval"],1e-12)
    else:check(summary["paired"]["roi_difference_interval"] is None,"Undefined ROI resample policy")
    close(np.quantile(profit,q),summary["paired"]["profit_common_budget_interval"],1e-12)
    check(int((~valid).sum())==summary["paired"]["undefined_roi_replicates"],"Undefined replicate count")
    close(summary["paired"]["profit_difference_yen"],b.profit.sum()-a.profit.sum(),0)
    return dict(protocol_files=len(protocol["inputs_and_source_sha256"]),mev_boosters=len(probs),mev_rows=len(mev),arms=calculated,paired=summary["paired"],main_summary_sha256=sha(out/"summary.json"),max_mev_abs_difference=float(np.max(np.abs(mm.raw-mainmev.p_mev_raw))))


def convert(x):
    if isinstance(x,np.generic):return x.item()
    if isinstance(x,Path):return str(x)
    raise TypeError(type(x))


if __name__=="__main__":
    parser=argparse.ArgumentParser(__doc__);parser.add_argument("--final",action="store_true");args=parser.parse_args()
    report, features, population=materials()
    if args.final:report["numerical"]=numerical(features,population)
    report["status"]="PASS";report["script_sha256"]=sha(__file__)
    path=Path(__file__).with_name("independent_final.json" if args.final else "independent_materials.json")
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=convert)+"\n")
    print(json.dumps(report,ensure_ascii=False,default=convert),flush=True)
