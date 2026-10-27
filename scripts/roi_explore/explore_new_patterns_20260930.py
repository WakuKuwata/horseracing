"""Frozen, research-only search of new 2007+ contextual betting rules."""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_key] = "1"

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

import recheck_models_20260930 as common
import evaluate as old_eval

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/roi_new_patterns_20260930"
ROWS = ROOT / "artifacts/roi_strict2007_20260930/input/rows.parquet"
PROVENANCE = ROWS.with_name("provenance.json")
PRED = ROOT / "artifacts/roi_strict2007_20260930/market_models/refit_predictions.parquet"
RECEIPT = PRED.with_name("refit_receipt.json")
DIV = ROOT / "artifacts/roi_recheck_20260930/data_audit/archived_win_dividends.jsonl"
WINDOWS = {"D": (2007, 2014), "Q": (2015, 2018), "C": (2019, 2022),
           "R": (2023, 2026), "POST": (2019, 2026), "ALL": (2007, 2026)}
FORBIDDEN = set(old_eval.dsl.FORBIDDEN) | {"ret", "result_status", "p", "p_top2", "p_top3", "ev"}


def save(path, value):
    with Path(path).open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=old_eval._default)
        f.write("\n")


def canonical(conditions):
    out = []
    for c in conditions:
        op = c["op"]; field = c["field"]
        if op == "in_band":
            atoms = [(field, "ge", c["lo"]), (field, "lt", c["hi"])]
        elif op in ("is_true", "is_false"):
            atoms = [(field, "eq", 1 if op == "is_true" else 0)]
        else:
            atoms = [(field, op, c.get("value"))]
        for field, op, value in atoms:
            if isinstance(value, (int, float, bool)):
                value = float(value)
            if isinstance(value, list):
                value = sorted(value, key=str)
            out.append(json.dumps([field, op, value], sort_keys=True))
    return tuple(sorted(set(out)))


def condition_mask(arr, cond):
    field, op, value = cond["field"], cond["op"], cond.get("value")
    if field in FORBIDDEN:
        raise ValueError(f"Outcome/unattested field is prohibited: {field}")
    x = arr[field]; ok = ~pd.isna(x)
    if op == "eq": ans = x == value
    elif op == "ne": ans = x != value
    elif op == "ge": ans = x >= value
    elif op == "gt": ans = x > value
    elif op == "le": ans = x <= value
    elif op == "lt": ans = x < value
    elif op == "in": ans = np.isin(x, value)
    else: raise ValueError(op)
    return ok & ans


def select(arr, pattern, cache):
    mask = np.ones(len(arr["race_id"]), dtype=bool)
    for c in pattern["conditions"]:
        key = json.dumps(c, sort_keys=True)
        if key not in cache:
            cache[key] = condition_mask(arr, c)
        mask &= cache[key]
    return mask


def support(stats):
    return bool(stats.get("n_bets", 0) >= 400 and stats.get("n_hits", 0) >= 30
                and stats.get("n_days", 0) >= 150 and stats.get("largest_hit_share", 1.) <= .15)


def failure_reasons(stats):
    reasons = []
    for key, minimum in (("n_bets", 400), ("n_hits", 30), ("n_days", 150)):
        if stats.get(key, 0) < minimum: reasons.append(f"{key}_lt_{minimum}")
    if stats.get("roi") is None or stats["roi"] < 1: reasons.append("roi_lt_1")
    if stats.get("largest_hit_share") is None or stats["largest_hit_share"] > .15:
        reasons.append("largest_hit_share_gt_0.15")
    return reasons


def shortlist(records):
    primary = [r["id"] for r in records if r["screen_pass"]]
    near = []
    for family in sorted({r["family"] for r in records}):
        pool = [r for r in records if r["family"] == family and not r["screen_pass"]
                and all(support(r["periods"][w]) for w in ("D", "Q"))]
        pool.sort(key=lambda r: (-min(r["periods"][w]["roi"] for w in ("D", "Q")),
                                 -sum(r["periods"][w]["roi"] for w in ("D", "Q")), r["id"]))
        near.extend(r["id"] for r in pool[:2])
    return primary, near


def stats(bets, universe):
    if "population" not in universe.attrs:
        universe.attrs["population"]=(int(universe.race_id.nunique()),int(universe.race_date.nunique()),int(universe.year.nunique()))
    nr,nd,ny=universe.attrs["population"]
    ans = common.metrics(bets, nr)
    ans["eligible_races"] = nr
    ans["eligible_days"] = nd
    ans["bets_per_100_races"] = len(bets) / max(1, ans["eligible_races"]) * 100
    ans["bets_per_calendar_year"] = len(bets) / max(1, ny)
    return ans


def evidence(bets, days, repetitions):
    if bets.empty: return {"ci95": None, "p_one_sided": 1.}
    daily = bets.assign(pay=bets.ret + 100).groupby("race_date").agg(n=("ret", "size"), pay=("pay", "sum"))
    daily = daily.reindex(days, fill_value=0)
    s = daily.n.to_numpy()*100.; p = daily.pay.to_numpy(); n = len(days)
    point = p.sum()/s.sum(); rng = np.random.default_rng(20260930); samples=[]
    for i in range(0, repetitions, 200):
        ix=rng.integers(0,n,size=(min(200,repetitions-i),n))
        den=s[ix].sum(axis=1); num=p[ix].sum(axis=1)
        samples.extend((num[den>0]/den[den>0]).tolist())
    samples=np.asarray(samples)
    return {"ci95":{"lo":float(np.quantile(samples,.025)),"hi":float(np.quantile(samples,.975))},
            "p_one_sided":float((1+np.sum(samples-point >= point-1))/(1+len(samples))),
            "bootstrap_repetitions":repetitions,"zero_stake_repetitions":repetitions-len(samples),
            "bootstrap_unit":"all eligible race dates including zero bet days", "selection_adjusted":False}


def freeze(out):
    import new_pattern_features_20260930 as features
    import new_pattern_definitions_20260930 as definitions
    out.mkdir(parents=True, exist_ok=True)
    if (out/"freeze.json").exists(): raise RuntimeError("Already frozen")
    old_files=sorted((ROOT/"artifacts/roi_explore/patterns").glob("*.json")); old_keys=set()
    for file in old_files:
        items=json.loads(file.read_text()); items=items["patterns"] if isinstance(items,dict) else items
        for p in items:
            if p.get("bet_type","win") != "win" or p.get("stake","flat") != "flat" or p.get("select",{}).get("rule","all") != "all": continue
            old_keys.add(canonical(p.get("race_filter",[])+p.get("horse_filter",[])))
    pats=[]; duplicates=[]; seen=set()
    for p in definitions.patterns():
        assert p["id"] not in {x["id"] for x in pats}
        assert all(c["field"] not in FORBIDDEN for c in p["conditions"])
        assert any(c=={"field":"days_since_last","op":"ge","value":14} for c in p["conditions"])
        key=canonical(p["conditions"])
        if key in seen or key in old_keys:
            duplicates.append({"id":p["id"],"reason":"exact normalized conditions already defined"}); continue
        seen.add(key); p["uses_model"]=any(c["field"]=="market_er" for c in p["conditions"]); pats.append(p)
    inputs=[ROWS,PROVENANCE,PRED,RECEIPT,DIV,ROOT/"docs/roi-new-patterns-20260930/protocol.md",*old_files]
    sources=[Path(__file__),Path(features.__file__),Path(definitions.__file__),Path(common.__file__),Path(old_eval.__file__),Path(old_eval.dsl.__file__)]
    save(out/"patterns.json",pats)
    save(out/"freeze.json",{"created_utc":datetime.now(timezone.utc).isoformat(),"patterns_sha256":common.sha(out/"patterns.json"),
         "input_sha256":{str(p):common.sha(p) for p in inputs},"source_sha256":{str(p):common.sha(p) for p in sources},
         "n_patterns":len(pats),"duplicates_omitted":duplicates,"windows":WINDOWS,
         "model_D_start":2010,"history_start":"2007-01-01","date_end":"2026-09-22",
         "stake":"100 JPY per matched horse, all selections, no stake escalation",
         "selection":"D and Q both ROI>=1, bets>=400, hits>=30, betting_days>=150, largest hit payout share<=.15",
         "near_miss":"Non-survivors meeting support in both D/Q; at most 2 per family by min(D ROI,Q ROI) desc, sum(D ROI,Q ROI) desc, id asc. Never promoted to main candidates.",
         "confirmation":"C and R evaluated separately; diagnostic Holm over 2*S tests, alpha=.025. Both period results required for a sustained historical result.",
         "bootstrap":"B=max(4000,200*(2*S)); day ratio bootstrap with eligible zero-bet days included; additive-centered diagnostic p",
         "reuse":"All periods already studied; not an untouched holdout. Stored odds are approximate settlement.",
         "can_adopt":False})
    print(f"Frozen {len(pats)} new rules; {len(duplicates)} exact duplicates omitted",flush=True)


def score(out):
    import new_pattern_features_20260930 as features
    if (out/"scores.json").exists(): raise RuntimeError("Already completed")
    freeze_=json.loads((out/"freeze.json").read_text())
    for group in ("input_sha256","source_sha256"):
        assert all(common.sha(Path(p))==h for p,h in freeze_[group].items())
    assert common.sha(out/"patterns.json")==freeze_["patterns_sha256"]
    provenance=json.loads(PROVENANCE.read_text()); assert provenance["rows_sha256"]==common.sha(ROWS)
    assert provenance["strict2007_histories_rebuilt"]
    raw=pd.read_parquet(ROWS); d=features.build_features(raw); del raw
    receipt=json.loads(RECEIPT.read_text()); assert common.sha(PRED)==receipt["prediction_sha256"]
    pred=pd.read_parquet(PRED,columns=["race_id","horse_id","pred","odds","ret"])
    pred=pred.rename(columns={"odds":"prediction_odds","ret":"prediction_ret"})
    d=d.merge(pred,on=["race_id","horse_id"],how="left",validate="one_to_one")
    d=d.loc[d.race_ok & ~d.dead_heat].sort_values(["race_date","race_id","horse_id"]).reset_index(drop=True)
    d["ret"]=np.where(d.won,d.odds*100,0)-100
    has=d.pred.notna(); assert has.equals(d.year.ge(2010))
    assert np.allclose(d.loc[has,"odds"],d.loc[has,"prediction_odds"])
    assert np.allclose(d.loc[has,"ret"],d.loc[has,"prediction_ret"])
    d["market_er"]=1+d.pred/100
    pats=json.loads((out/"patterns.json").read_text()); fields={c["field"] for p in pats for c in p["conditions"]}
    arr={c:d[c].to_numpy() for c in fields|{"race_id"}}; cache={}; records=[]
    core_cols=["race_id","horse_id","horse_number","race_date","year","odds","won","ret"]
    d=d[list(dict.fromkeys(core_cols+sorted(fields)))].copy()
    basic=d[core_cols].copy(); universes={}
    def universe(p,w):
        lo,hi=WINDOWS[w]
        if p["uses_model"]:lo=max(lo,2010)
        if (lo,hi) not in universes:universes[(lo,hi)]=basic.loc[basic.year.between(lo,hi)].copy()
        return universes[(lo,hi)]
    year_universes={y:basic.loc[basic.year.eq(y)].copy() for y in range(2007,2027)}
    for p in pats:
        mask=select(arr,p,cache); r={**p,"periods":{}}
        for w in ("D","Q"):
            u=universe(p,w); r["periods"][w]=stats(u.loc[mask[u.index]],u)
        r["failure_reasons"]={w:failure_reasons(r["periods"][w]) for w in ("D","Q")}
        r["screen_pass"]=not any(r["failure_reasons"].values()); records.append(r)
    primary,near=shortlist(records); selected=set(primary+near); b=max(4000,200*2*len(primary))
    save(out/"discovery_qualification.json",records)
    save(out/"selection_before_later_periods.json",{"primary":primary,"near_miss":near,"bootstrap_repetitions":b,"created_utc":datetime.now(timezone.utc).isoformat()})
    print(f"D/Q completed: {len(primary)} main survivors, {len(near)} near misses frozen",flush=True)
    betsdir=out/"bets";betsdir.mkdir(exist_ok=True); pvals={}; flat=[]
    save(out/"data_summary.json",{"rows":len(d),"races":int(d.race_id.nunique()),"date_min":d.race_date.min(),"date_max":d.race_date.max(),"new_fields":sorted(fields),"pattern_families":dict(Counter(p["family"] for p in pats))})
    for i,r in enumerate(records):
        mask=select(arr,r,cache); bets=basic.loc[mask]
        r["role"]="primary" if r["id"] in primary else "near_miss" if r["id"] in near else "not_selected"
        for w in ("C","R","POST","ALL"):
            u=universe(r,w); bs=u.loc[mask[u.index]]; st=stats(bs,u)
            if r["id"] in selected: st.update(evidence(bs,sorted(u.race_date.unique()),b if r["screen_pass"] else 4000))
            r["periods"][w]=st
        r["yearly"]={str(y):stats(bets.loc[bets.year.eq(y)],year_universes[y]) for y in range(2010 if r["uses_model"] else 2007,2027)}
        assert sum(x["n_bets"] for x in r["yearly"].values())==r["periods"]["ALL"]["n_bets"]
        if r["screen_pass"]:
            for w in ("C","R"):pvals[r["id"]+"/"+w]=r["periods"][w]["p_one_sided"]
        if r["id"] in selected:
            keep=["race_id","horse_id","horse_number","race_date","year","odds","won","ret",*sorted(fields)]
            d.loc[mask,list(dict.fromkeys(keep))].to_parquet(betsdir/(r["id"]+".parquet"),index=False)
        for w,st in r["periods"].items():flat.append({"id":r["id"],"family":r["family"],"label":r["label"],"role":r["role"],"period":w,**st})
        if i%100==0:print(f"Later-period scoring {i}/{len(records)}",flush=True)
    rejected=old_eval.holm(pvals,alpha=.025)
    for r in records:
        r["C_R_point_ge1"]=all((r["periods"][w]["roi"] or 0)>=1 for w in ("C","R"))
        r["C_R_support"]=all(support(r["periods"][w]) for w in ("C","R"))
        r["C_R_holm"]=all(rejected.get(r["id"]+"/"+w,False) for w in ("C","R"))
    save(out/"scores.json",records);pd.DataFrame(flat).to_csv(out/"scores.csv",index=False)
    save(out/"summary.json",{"n_patterns":len(records),"n_primary":len(primary),"n_near_miss":len(near),
         "primary_both_later_ROI_ge1":sum(r["screen_pass"] and r["C_R_point_ge1"] for r in records),
         "primary_both_later_ROI_ge1_with_support":sum(r["screen_pass"] and r["C_R_point_ge1"] and r["C_R_support"] for r in records),
         "primary_both_later_CI_lo_gt1":sum(r["screen_pass"] and all(r["periods"][w].get("ci95") and r["periods"][w]["ci95"]["lo"]>1 for w in ("C","R")) for r in records),
         "primary_both_later_holm":sum(r["C_R_holm"] for r in records),"primary":primary,"near_miss":near,
         "failure_counts":{w:dict(Counter(k for r in records for k in r["failure_reasons"][w])) for w in ("D","Q")},
         "untouched_holdout":False,"can_adopt":False})
    assert all(common.sha(Path(p))==h for group in ("input_sha256","source_sha256") for p,h in freeze_[group].items())
    print((out/"summary.json").read_text(),flush=True)


if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("mode",choices=["freeze","score"]);ap.add_argument("--out",type=Path,default=OUT)
    args=ap.parse_args();{"freeze":freeze,"score":score}[args.mode](args.out)
