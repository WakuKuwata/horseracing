"""Independent 158 audit. Imports no primary 158/157 implementation and reads no closing odds."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import gzip
import hashlib
import html
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "artifacts/158-prerace-roi-comparison"
OLD = ROOT / "artifacts/155-saved-odds-settlement-audit"
MEV = ROOT / "artifacts/market_ev/mev-ens15-v1"
BUNDLE = ROOT / "artifacts/129-candidate-mixture-serving/bundle.json"
EVIDENCE = Path(__file__).resolve().parent


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check(value, message):
    if not value:
        raise AssertionError(message)


def time(value):
    return pd.to_datetime(value, utc=True, errors="coerce")


def close(x, y, label, atol=1e-10):
    try:
        np.testing.assert_allclose(x, y, atol=atol, rtol=0, equal_nan=True)
    except AssertionError as exc:
        raise AssertionError(label) from exc


def write(name, data):
    (EVIDENCE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def grouped(df, key="race_id"):
    return {str(k): g for k, g in df.groupby(key, sort=False)}


def printed_payout(content):
    # Only official Tansho cells are parsed. Neither result-table odds nor any closing-price
    # diagnostic is extracted or used by the decision audit.
    win = re.search(r'<tr\b[^>]*class="Tansho"[^>]*>(.*?)</tr>', content, re.S)
    check(win is not None, "No printed official payout")
    numbers = re.search(r'<td\b[^>]*class="Result"[^>]*>(.*?)</td>', win.group(1), re.S)
    amounts = re.search(r'<td\b[^>]*class="Payout"[^>]*>(.*?)</td>', win.group(1), re.S)
    nums = [int(v) for v in re.findall(r"<span\b[^>]*>\s*(\d+)\s*</span>", numbers.group(1))]
    stripped = html.unescape(re.sub(r"<[^>]*>", "", amounts.group(1)))
    pays = [int(v.replace(",", "")) for v in re.findall(r"([\d,]+)円", stripped)]
    check(len(nums) == len(pays) > 0 and len(nums) == len(set(nums)), "Bad printed payout set")
    return dict(zip(nums, pays, strict=True))


def load_material():
    manifest = json.loads((BASE / "snapshot/manifest.json").read_text())
    check(manifest["settings"]["read_only"] == "on" and manifest["settings"]["isolation"] == "repeatable read", "Unsafe DB snapshot")
    db = {}
    for name, item in manifest["tables"].items():
        path = ROOT / item["path"]
        check(sha(path) == item["sha256"], "Snapshot SHA changed: " + name)
        db[name] = pd.read_parquet(path)
        check(len(db[name]) == item["rows"], "Row count changed: " + name)
    check("odds" not in db["entries"].columns, "Closing-price column exported")
    spec = json.loads((MEV / "ensemble.spec.json").read_text())
    check(spec["training_cutoff_by_year"]["2026"]["train_through"] == 2025, "MEV training sees target")
    members = json.loads((MEV / "ensemble_2026.json").read_text())
    check([m["seed"] for m in members["members"]] == list(range(1, 16)), "Wrong MEV seeds")
    for m in members["members"]:
        check(sha(MEV / m["path"]) == m["sha256"], "MEV member changed")
    bundle = json.loads(BUNDLE.read_text())
    check(bundle["train_through"] == "2026-08-23" and bundle["coefficient_train_through"] < "2026-01-01", "Legacy cutoff differs")
    for m in bundle["members"]:
        for name, digest in m["files"].items():
            check(sha(BUNDLE.parent / m["artifact_dir"] / name) == digest, "Legacy member changed")
    registered = db["model"].iloc[0]
    metrics = json.loads(registered.metrics_summary)
    check(registered.model_version == "mix-129-nj6" and metrics["registration"]["bundle_sha256"] == sha(BUNDLE), "Legacy registration differs")
    check(sha(Path(registered.weights_uri)) == sha(BUNDLE), "Registered bundle differs")
    for m in json.loads(Path(registered.weights_uri).read_text())["members"]:
        for name, digest in m["files"].items():
            check(sha(Path(registered.weights_uri).parent / m["artifact_dir"] / name) == digest, "Registered member differs")
    return db, bundle


def eligibility(db, bundle):
    races = db["races"].set_index("race_id")
    entries, results, payouts, mevs = [grouped(db[k]) for k in ("entries", "results", "payouts", "mev")]
    predictions, features = [grouped(db[k], "prediction_run_id") for k in ("predictions", "features")]
    runs = grouped(db["runs"])
    pages = pd.read_parquet(OLD / "audit/archive_pages.parquet", columns=[
        "race_id", "archive_path", "archive_gz_sha256", "archive_raw_sha256", "html_utf8_sha256", "decode_encoding", "selected"])
    selected_pages = pages[pages.selected].set_index("race_id")
    manifest_sha = sha(MEV / "ensemble_2026.json")
    mev_tokens = {"mev-ens15-v1", "seeds=1-15", "threads=1", "deterministic", "features=roi-explore-2026-09", "drop=sameday,weightlive", "data>=2007", "oddsobs=quote"}
    old_tokens = {"feat=features-021", f"mix=6:{sha(BUNDLE)[:12]}", "coef=2026", "wregime=serving"}
    flat_quotes, chosen, judge_rows, label_rows, fail = [], {}, [], [], {}
    official_verified = 0
    for rid, race in races.iterrows():
        ee = entries[rid]
        active = ee[ee.entry_status.eq("started")]
        rr, pp = results[rid], payouts[rid]
        check(ee.horse_id.notna().all() and ee.horse_number.notna().all() and not ee.horse_number.duplicated().any(), "Invalid entry field")
        check(set(rr.horse_id) == set(active.horse_id) and len(rr) == len(active), "Result field differs")
        check(rr.result_status.isin(["finished", "stopped", "disqualified"]).all(), "Bad status")
        ff = rr[rr.result_status.eq("finished")]
        check(ff.finish_order.notna().all() and ff.finish_order.between(1, len(active)).all(), "Bad rank")
        ids = set(active.horse_id)
        numbered = active.set_index("horse_id").horse_number.to_dict()
        winner_ids = set(rr.loc[rr.result_status.eq("finished") & rr.finish_order.eq(1), "horse_id"])
        winner_numbers = {numbered[h] for h in winner_ids}
        check(winner_numbers == set(pp.horse_number) and len(pp) == len(winner_numbers), "Official winner set differs")
        check(pp.source.eq("netkeiba_result").all() and pp.payout_yen.ge(100).all(), "Bad official source/value")
        page = selected_pages.loc[rid]
        gz = ROOT / page.archive_path
        check(sha(gz) == page.archive_gz_sha256, "HTML gzip changed")
        raw = gzip.decompress(gz.read_bytes())
        check(hashlib.sha256(raw).hexdigest() == page.archive_raw_sha256, "HTML raw changed")
        content = raw.decode(page.decode_encoding, errors="replace")
        check(hashlib.sha256(content.encode()).hexdigest() == page.html_utf8_sha256, "HTML text changed")
        dividend = {int(n): int(v) for n, v in zip(pp.horse_number, pp.payout_yen, strict=True)}
        check(printed_payout(content) == dividend, "Printed official payout differs")
        check(all(any(pages.race_id.eq(rid) & pages.html_utf8_sha256.eq(x)) for x in pp.html_sha256), "Official origin HTML absent")
        official_verified += 1
        flags = []
        if race.track_type == "障": flags.append("jump")
        if len(winner_ids) != 1: flags.append("dead_heat")
        if str(race.race_date) <= bundle["train_through"]: flags.append("in_legacy_training")
        g = mevs.get(rid)
        if g is None:
            flags.append("missing_mev")
        else:
            if len(g) != len(ids) or set(g.horse_id) != ids or g.horse_id.duplicated().any(): flags.append("mev_field")
            if not all(numbered.get(h) == n for h, n in zip(g.horse_id, g.horse_number, strict=True)): flags.append("mev_numbers")
            if not g.logic_version.map(lambda x: mev_tokens <= set(x.split(";"))).all(): flags.append("mev_quote_source")
            if any(g[c].isna().any() or g[c].nunique() != 1 for c in ["run_id", "computed_at", "odds_observed_at", "booster", "booster_sha256"]): flags.append("mixed_mev")
            post = time(race.post_time); compute = pd.to_datetime(g.computed_at, utc=True); observed = pd.to_datetime(g.odds_observed_at, utc=True)
            if pd.isna(post) or not compute.lt(post).all() or not observed.le(compute).all(): flags.append("mev_timing")
            if not g.result_pending_at_compute.all(): flags.append("mev_result_known")
            if not g.booster.eq("ensemble_2026.json").all() or not g.booster_sha256.eq(manifest_sha).all(): flags.append("mev_manifest")
            odds = pd.to_numeric(g.odds_used, errors="coerce").to_numpy(float); p = pd.to_numeric(g.win_prob, errors="coerce").to_numpy(float)
            if not np.isfinite(odds).all() or not (odds >= 1).all() or not np.isfinite(p).all() or not ((p > 0) & (p < 1)).all(): flags.append("mev_values")
            if not all(Decimal(x.win_prob)*Decimal(x.odds_used) == Decimal(x.expected_return) for x in g.itertuples()): flags.append("mev_ev")
        if flags:
            fail[rid] = flags
            continue
        flat_quotes.append(rid)
        decision = time(g.computed_at.iloc[0]); good = []
        for row in runs.get(rid, pd.DataFrame()).to_dict("records"):
            runid = row["prediction_run_id"]
            if not old_tokens <= set(row["logic_version"].split(";")): continue
            if not all(pd.notna(time(row[k])) and time(row[k]) <= decision for k in ["computed_at", "created_at", "updated_at"]): continue
            pred, feat = predictions.get(runid), features.get(runid)
            if pred is None or feat is None or len(pred) != len(ids) or len(feat) != len(ids) or set(pred.horse_id) != ids or set(feat.horse_id) != ids: continue
            if pred.horse_id.duplicated().any() or feat.horse_id.duplicated().any() or not feat.feature_version.eq("features-021").all(): continue
            if not all(pd.to_datetime(frame[c], utc=True).notna().all() and pd.to_datetime(frame[c], utc=True).le(decision).all() for frame in [pred, feat] for c in ["created_at", "updated_at"]): continue
            payload = [json.loads(s) for s in feat.features_json]
            if not all(all(k in f and f[k] is None for k in ["weight", "weight_diff", "carried_weight_ratio"]) for f in payload): continue
            pold = pd.to_numeric(pred.win_prob).to_numpy(float)
            if not np.isfinite(pold).all() or not ((pold > 0) & (pold < 1)).all() or not np.isclose(pold.sum(), 1., atol=1e-8, rtol=0): continue
            good.append(row)
        if not good:
            fail[rid] = ["no_asof_preweight_legacy"]
            continue
        selected = max(good, key=lambda r: (time(r["computed_at"]), r["prediction_run_id"]))
        old = predictions[selected["prediction_run_id"]].set_index("horse_id")
        chosen[rid] = selected["prediction_run_id"]
        inverses = 1 / pd.to_numeric(g.odds_used).to_numpy(float)
        for i, m in enumerate(g.itertuples()):
            judge_rows.append(dict(race_id=rid, horse_id=m.horse_id, horse_number=int(m.horse_number), race_date=str(race.race_date), post_time=post, decision_at=decision,
                judgment_odds=float(m.odds_used), q=float(inverses[i]/inverses.sum()), p_mev=float(np.clip(float(m.win_prob), 1e-6, 1-1e-6)), p_pred_preweight=float(old.loc[m.horse_id].win_prob)))
            label_rows.append(dict(race_id=rid, horse_id=m.horse_id, won=m.horse_id in winner_ids, official_payout_yen=dividend.get(int(m.horse_number), 0)))
    judgments = pd.DataFrame(judge_rows).sort_values(["race_date", "post_time", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
    labels = pd.DataFrame(label_rows)
    return judgments, labels, dict(official_verified_races=official_verified, mev_quote_eligible_races=len(flat_quotes), mev_quote_dates=dict(Counter(str(races.loc[r].race_date) for r in flat_quotes)),
        paired_races=len(chosen), paired_horses=len(judgments), paired_dates=dict(Counter(judgments.drop_duplicates("race_id").race_date)), chosen_old_runs=chosen,
        excluded_races=fail)


def audit_primary(judgments, labels, info):
    prepared = pd.read_parquet(BASE / "prepared/judgments.parquet")
    keys = ["race_id", "horse_id"]
    check(set(map(tuple, judgments[keys].to_numpy())) == set(map(tuple, prepared[keys].to_numpy())), "Primary judgment population differs")
    aligned = judgments.merge(prepared, on=keys, suffixes=("_i", "_p"), validate="one_to_one")
    for c in ["horse_number", "judgment_odds", "q", "p_mev", "p_pred_preweight"]:
        close(aligned[c+"_i"], aligned[c+"_p"], "Judgment column differs: " + c)
    for c in ["race_date", "post_time", "decision_at"]:
        check(aligned[c+"_i"].eq(aligned[c+"_p"]).all(), "Judgment metadata differs: " + c)
    selected = prepared.drop_duplicates("race_id").set_index("race_id")
    check(selected.old_prediction_run_id.to_dict() == info["chosen_old_runs"], "Selected legacy run IDs differ")
    original_mev = pd.read_parquet(BASE / "snapshot/mev.parquet")
    quote_times = original_mev.drop_duplicates("race_id").set_index("race_id").odds_observed_at
    ages = []
    for rid, row in selected.iterrows():
        observed = time(quote_times.loc[rid])
        check(time(row.quote_observed_at) == observed, "Quote timestamp differs")
        age = (time(row.decision_at)-observed).total_seconds()
        close([age], [row.quote_age_seconds], "Quote age differs")
        check(time(row.old_available_at) <= time(row.decision_at), "Old vector available after judgment")
        ages.append(age)
    info["quote_age_seconds"] = dict(min=float(min(ages)), max=float(max(ages)),
        within_60=int(sum(a<=60 for a in ages)), within_1800=int(sum(a<=1800 for a in ages)),
        within_3600=int(sum(a<=3600 for a in ages)))
    check(not any(c in prepared for c in ["won", "official_payout_yen", "closing_odds", "final_odds"]), "Settlement leaked into prepared decisions")
    protocol = json.loads((BASE / "protocol.json").read_text())
    check(sha(BASE / "protocol.json") == (BASE / "protocol.sha256").read_text().strip(), "Protocol changed")
    for path, digest in protocol["inputs_and_source_sha256"].items(): check(sha(ROOT/path) == digest, "Frozen input changed: " + path)
    info["protocol_file_count"] = len(protocol["inputs_and_source_sha256"])
    coefs = json.loads((ROOT / "artifacts/151-roi-prediction-ablation/comparison/summary.json").read_text())["coefficients"]["2026"]
    d = judgments.copy(); numerical = {}; summary = {}; accounts = {}
    for arm, cols in [("roi_only", ["q", "p_mev"]), ("plus_prediction", ["q", "p_mev", "p_pred_preweight"])]:
        logit = sum(np.log(d[c].to_numpy(float))*coefs[arm]["coefficients"][c] for c in cols)
        pp = np.zeros(len(d))
        for _, indexes in d.groupby("race_id").groups.items():
            ix = np.asarray(list(indexes)); z=np.exp(logit[ix]-logit[ix].max()); pp[ix]=z/z.sum()
        choices = d[keys+["horse_number", "judgment_odds"]].copy(); choices["ev"]=pp*choices.judgment_odds.to_numpy()
        picked = choices[choices.ev.gt(1)].sort_values(["race_id", "ev", "horse_number", "horse_id"], ascending=[True,False,True,True],kind="stable").groupby("race_id",sort=False).head(10).index
        buy = np.zeros(len(d),bool);buy[picked]=True
        d[arm]=pp; d[arm+"_selected"]=buy; d[arm+"_ev"]=choices.ev
        joined=d.merge(labels,on=keys,validate="one_to_one")
        joined["stake"]=buy.astype(int)*100; joined["payout"]=buy*joined.official_payout_yen
        joined["tickets"]=buy.astype(int); joined["hits"]=(buy&joined.won).astype(int)
        races=joined.groupby(["race_id","race_date","post_time"],dropna=False,sort=False)[["stake","payout","tickets","hits"]].sum().reset_index().sort_values(["race_date","post_time","race_id"]).reset_index(drop=True)
        races["profit"]=races.payout-races.stake;races["budget"]=1000;accounts[arm]=races
        cumulative=np.r_[0.,races.profit.cumsum()]
        stake=int(races.stake.sum());payout=int(races.payout.sum())
        summary[arm]=dict(tickets=int(buy.sum()),stake_yen=stake,payout_yen=payout,profit_yen=payout-stake,roi=payout/stake if stake else None,
            no_bet_races=int(races.stake.eq(0).sum()),hit_tickets=int(races.hits.sum()),common_budget_yen=1000*len(races),profit_per_common_budget=(payout-stake)/(1000*len(races)),winner_nll=float(-np.log(pp[joined.won.to_numpy(bool)]).mean()),max_drawdown_yen=float((np.maximum.accumulate(cumulative)-cumulative).max()))
    primary=pd.read_parquet(BASE/"comparison/decisions.parquet"); check(set(map(tuple,primary[keys].to_numpy()))==set(map(tuple,d[keys].to_numpy())),"Decision population differs")
    alignment=d.merge(primary,on=keys,suffixes=("_i","_p"),validate="one_to_one")
    for arm in summary:
        close(alignment[arm+"_i"],alignment[arm+"_p"],"Blend differs")
        close(alignment[arm+"_ev_i"],alignment[arm+"_ev_p"],"EV differs")
        check(alignment[arm+"_selected_i"].eq(alignment[arm+"_selected_p"]).all(),"Selection differs")
        original=pd.read_parquet(BASE/"comparison"/(arm+"_races.parquet")); agree=accounts[arm].merge(original,on=["race_id","race_date"],suffixes=("_i","_p"),validate="one_to_one")
        for c in ["stake","payout","profit","budget","tickets","hits"]: close(agree[c+"_i"],agree[c+"_p"],"Accounting differs")
    published=json.loads((BASE/"comparison/summary.json").read_text())
    for arm in summary:
        for key,value in summary[arm].items():
            if value is None: check(published["arms"][arm][key] is None,"Null metric differs")
            else:close([value],[published["arms"][arm][key]],"Summary metric differs: "+arm+"/"+key)
    a,b=accounts["roi_only"],accounts["plus_prediction"]
    z=a.merge(b,on=["race_id","race_date"],suffixes=("_a","_b"),validate="one_to_one")
    daily=z.groupby("race_date",sort=True)[["stake_a","payout_a","profit_a","budget_a","stake_b","payout_b","profit_b"]].sum().to_numpy(float)
    if len(daily)>=2:
        rng=np.random.default_rng(20261026);samples=daily[rng.integers(0,len(daily),size=(4000,len(daily)))].sum(axis=1)
        good=(samples[:,0]>0)&(samples[:,4]>0);roi=np.full(4000,np.nan);roi[good]=samples[good,5]/samples[good,4]-samples[good,1]/samples[good,0]
        common=(samples[:,6]-samples[:,2])/samples[:,3]
        financial=dict(undefined_roi_replicates=int((~good).sum()),roi_difference_interval=np.quantile(roi,[.0125,.9875]).tolist() if good.all() else None,
            profit_common_budget_interval=np.quantile(common,[.0125,.9875]).tolist(),profit_difference_yen=int(b.profit.sum()-a.profit.sum()),
            profit_common_budget_difference=float((b.profit.sum()-a.profit.sum())/a.budget.sum()),roi_difference=float(b.payout.sum()/b.stake.sum()-a.payout.sum()/a.stake.sum()) if a.stake.sum()>0 and b.stake.sum()>0 else None)
        for key,value in financial.items():
            if value is None:check(published["paired"][key] is None,"Null paired metric differs")
            else:close(value,published["paired"][key],"Paired metric differs: "+key)
        numerical["paired"]=financial
    numerical["arms"]=summary
    info.update(status="PASS",primary_summary_sha256=sha(BASE/"comparison/summary.json"),numerical=numerical,
        decision_price_source="MEV.odds_used only; no closing price exported or parsed",full_field_and_archive_verified=True)
    write("independent_final.json",info)


if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--score",action="store_true");args=parser.parse_args()
    db,bundle=load_material();j,l,a=eligibility(db,bundle)
    write("independent_prepared.json",a)
    print(json.dumps({k:v for k,v in a.items() if k not in ["excluded_races","chosen_old_runs"]},ensure_ascii=False),flush=True)
    if args.score:audit_primary(j,l,a);print("FINAL PASS",flush=True)
