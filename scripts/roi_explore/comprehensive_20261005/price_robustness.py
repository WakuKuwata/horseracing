"""Decision-time normalization, residual model and fixed local-price robustness diagnostic."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/"training/src"), str(ROOT/"eval/src"), str(Path(__file__).parent)]
import residual_spike as R
spec=importlib.util.spec_from_file_location("r02_drift_reuse",ROOT/"scripts/roi_explore/missed_20261004/r02_drift.py")
D=importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
OUT=ROOT/"artifacts/roi_comprehensive_20261005/price_robustness"
PROTO=ROOT/"docs/roi-comprehensive-review-20261005/price-protocol.md"


def norm(p,rid):
    inv=pd.Series(p).groupby(rid,sort=False).transform("sum").to_numpy()
    out=p/inv
    np.testing.assert_allclose(pd.Series(out).groupby(rid).sum(),1,atol=1e-12)
    return out


def day_stats(d,masks):
    days,di=np.unique(d.race_date.to_numpy(),return_inverse=True)
    w=R.counts_boot(days)
    pay=d.settle_multiplier.to_numpy(float)
    won=d.won.to_numpy(bool)
    result={}; reps={}
    for k,m in masks.items():
        den=np.bincount(di,weights=m.astype(float),minlength=len(days))
        num=np.bincount(di,weights=m*pay,minlength=len(days))
        wd=w@den
        rep=np.divide(w@num,wd,out=np.full(R.B,np.nan),where=wd>0)
        reps[k]=rep
        n=int(m.sum())
        result[k]=dict(n=n,hits=int((m&won).sum()),roi=float(pay[m].sum()/n) if n else None,
                       ci95=np.nanquantile(rep,[.025,.975]).tolist() if n else None,
                       n_days=int(np.unique(d.race_date.to_numpy()[m]).size))
    for gate,parent in (("S1_price_guard","S1"),("S3_price_guard","S3"),("N1","S3"),
                        ("residual_EV12","S3"),("extended_EV12","S3")):
        if gate not in result or parent not in result or result[gate]["roi"] is None:
            continue
        if result[parent]["roi"] is None:
            continue
        diff=reps[gate]-reps[parent]
        result[gate]["parent"]=parent
        result[gate]["paired_roi_delta"]=result[gate]["roi"]-result[parent]["roi"]
        result[gate]["paired_delta_ci95"]=np.nanquantile(diff,[.025,.975]).tolist()
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/"freeze.json").exists():raise RuntimeError("Already frozen; refuse to overwrite")
    pp=D.OUT/"pairs.parquet"; fp=D.OUT/"feats_cand.parquet"
    fz=dict(protocol_sha256=R.sha(PROTO),script_sha256=R.sha(Path(__file__)),
            pair_sha256=R.sha(pp),feature_sha256=R.sha(fp),
            residual_recipe_sha256=R.sha(R.OUT/"recipes.json"),
            residual_result_sha256=R.sha(R.OUT/"results.json"),can_adopt=False,eligible_for_verdict=False)
    R.write(OUT/"freeze.json",fz)
    d=pd.read_parquet(pp)
    versions=d[["race_id","pair_id","hours","source"]].drop_duplicates()
    assert versions.groupby("pair_id").size().eq(1).all()
    keep=versions.sort_values(["race_id","hours","source","pair_id"],kind="stable").drop_duplicates("race_id")
    d=d[d.pair_id.isin(keep.pair_id) & ~d.dead_heat].copy()
    d=d.sort_values(["pair_id","horse_number"]).reset_index(drop=True)
    assert d.groupby("pair_id").won.sum().eq(1).all()
    feats=pd.read_parquet(fp)
    extras=[c for c in feats if c not in d and c not in {"odds","race_id","horse_id"}]
    d=d.merge(feats[["race_id","horse_id"]+extras],on=["race_id","horse_id"],validate="one_to_one",how="left")
    assert d.race_id.nunique()==d.pair_id.nunique()
    d["official_race"]=d.groupby("race_id").official_payout_yen.transform(lambda s:s.notna().any())
    d["settle_multiplier"]=np.where(d.won,np.where(d.official_payout_yen.notna(),d.official_payout_yen/100,d.O_settle),0.)
    recipe=json.loads((R.OUT/"recipes.json").read_text())["2026"]
    ins=json.loads((R.OUT/"inputs.json").read_text())
    X,_=R.design(d,ins["features"],ins["cats"],maps=recipe["cat_maps"])
    bst=lgb.Booster(model_file=str(R.OUT/"model_2026.txt"))
    assert R.sha(R.OUT/"model_2026.txt")==recipe["model_sha256"]
    raw=bst.predict(X,raw_score=True,num_threads=1)
    coeff=json.loads((R.OUT/"results.json").read_text())["coefficients"]["2026"]["extended"]
    starts=R.segments(d.pair_id.to_numpy())
    for v in ("J","F"):
        odds=d[f"O_{v}"].to_numpy(float)
        q=norm(1/odds,d.pair_id)
        pe=norm(d[f"EVe_{v}"].to_numpy()/odds,d.pair_id)
        pr=R.softmax(np.log(q)+raw,starts)
        pm=R.softmax(np.column_stack([np.log(q),np.log(pe),np.log(pr)])@np.array(coeff),starts)
        d[f"N1_{v}"]=pe*odds>1.2
        d[f"residual_EV12_{v}"]=pr*odds>1.2
        d[f"extended_EV12_{v}"]=pm*odds>1.2
        d[f"residual_p_{v}"]=pr
        d[f"extended_p_{v}"]=pm
    # The guard is tested only within the original S3 parent. Every scenario retains a full field.
    parents=d.J_S3.to_numpy(bool)
    scenarios=[]
    for rid,g in d.groupby("pair_id",sort=False):
        for target in g.loc[g.J_S3,"horse_number"]:
            for mult in (.9,1.1):
                h=g.copy(); h["odds"]=h.O_J.astype(float)
                h.loc[h.horse_number==target,"odds"]*=mult
                h["scenario"]=f"{rid}|{int(target)}|{mult}"
                h["target"]=h.horse_number==target
                h["mult"]=mult
                scenarios.append(h)
    scorer=D.Scorer()
    # Replay original judged vectors before using any perturbed output.
    orig=d.copy(); orig["odds"]=orig.O_J.astype(float)
    orig=orig.sort_values(["pair_id","horse_number"]).reset_index(drop=True)
    market=D.market_block(orig[["pair_id","horse_id","horse_number","odds"]],"pair_id")
    for c in D.MARKET_COLS:orig[c]=market[c].to_numpy()
    chaos=pd.read_parquet(D.OUT/"db_chaos.parquet")
    cmap={}
    for row in chaos.itertuples():
        field=json.loads(row.field_json)
        cmap[row.snap_id]={h["horse_id"]:h.get("popularity") for h in field}
    pj=[]
    # R02 pair identifiers include the time band; recover its chosen CHAOS snapshot with the same dedup recipe.
    cha=chaos[chaos.status=="active"].copy()
    cha["hours"]=cha.seconds_to_post/3600
    cha["tb"]=D.band(cha.hours.to_numpy(),D.TB_EDGES,D.TB)
    chosen_hours={(row.race_id,row.source):row.hours for row in keep.itertuples()}
    cha=cha[[abs(r.hours-chosen_hours.get((r.race_id,"CHAOS"),-1e9))<1e-12 for r in cha.itertuples()]]
    look={(r.race_id,r.tb):r.snap_id for r in cha.itertuples()}
    for row in orig.itertuples():
        if row.source=="CHAOS":
            val=cmap.get(look.get((row.race_id,row.tb)),{}).get(row.horse_id)
        else:
            val=row.popularity
        pj.append(val)
    orig["popularity"]=pd.to_numeric(pd.Series(pj),errors="coerce").to_numpy()
    fallback=orig.groupby("pair_id").odds.rank(method="min")
    orig["popularity"]=orig.popularity.fillna(fallback)
    replay,_=scorer.score(orig)
    delta=float(np.max(np.abs(replay*orig.odds.to_numpy()-orig.EVe_J.to_numpy())))
    if delta>1e-9:raise RuntimeError(f"Judged parity failure {delta}")
    guard=np.zeros(len(d),bool); pert=[]
    if scenarios:
        sc=pd.concat(scenarios,ignore_index=True).sort_values(["scenario","horse_number"]).reset_index(drop=True)
        market=D.market_block(sc[["scenario","horse_id","horse_number","odds"]],"scenario")
        for c in D.MARKET_COLS:sc[c]=market[c].to_numpy()
        sc["popularity"]=sc.groupby("scenario").odds.rank(method="min")
        p,_=scorer.score(sc)
        sc["perturbed_ev"]=p*sc.odds.to_numpy()
        pert=sc.loc[sc.target,["pair_id","horse_id","mult","perturbed_ev"]]
        ev=pert.pivot(index=["pair_id","horse_id"],columns="mult",values="perturbed_ev")
        matched=d[["pair_id","horse_id"]].merge(ev.reset_index(),how="left",validate="one_to_one")
        guard=parents & (matched[.9]>1.2).to_numpy() & (matched[1.1]>1.2).to_numpy()
        pert.to_csv(OUT/"perturbed_targets.csv",index=False)
    masks={"S1":d.J_S1.to_numpy(bool),"S3":parents,"N1":d.N1_J.to_numpy(bool),
           "residual_EV12":d.residual_EV12_J.to_numpy(bool),"extended_EV12":d.extended_EV12_J.to_numpy(bool),
           "S1_price_guard":guard&d.J_S1.to_numpy(bool),"S3_price_guard":guard}
    metrics=day_stats(d,masks)
    official=d.official_race.to_numpy(bool)
    om={k:v[official] for k,v in masks.items()}
    ometric=day_stats(d.loc[official].reset_index(drop=True),om) if official.any() else {}
    retention={}
    for k in ("N1","residual_EV12","extended_EV12"):
        j=d[k+"_J"].to_numpy(bool); f=d[k+"_F"].to_numpy(bool)
        retention[k]=dict(judged_n=int(j.sum()),final_n=int(f.sum()),intersection=int((j&f).sum()),
                          retention=float((j&f).sum()/j.sum()) if j.any() else None)
    for k in ("S1","S3"):
        j=d["J_"+k].to_numpy(bool); f=d["F_"+k].to_numpy(bool)
        retention[k]=dict(judged_n=int(j.sum()),final_n=int(f.sum()),intersection=int((j&f).sum()),
                          retention=float((j&f).sum()/j.sum()) if j.any() else None)
    rng=np.random.default_rng(20261005)
    cells=d.source.astype(str)+"|"+d.tb.astype(str)+"|"+d.bJ.astype(str)
    null={}
    for label,parent in (("S1","d.J_S1"),("S3","parents")):
        pm=masks[label]; gm=masks[label+"_price_guard"]
        groups=[]
        for c in sorted(cells[pm].unique()):
            ids=np.flatnonzero(pm & (cells==c).to_numpy()); n=int(gm[ids].sum())
            groups.append((ids,n))
        vals=[]
        for rep in range(1000):
            picks=np.concatenate([rng.choice(ids,n,replace=False) for ids,n in groups]) if groups else np.array([],int)
            vals.append(float(d.settle_multiplier.to_numpy()[picks].mean()) if len(picks) else np.nan)
        arr=np.array(vals); obs=metrics[label+"_price_guard"]["roi"]
        null[label]=dict(n_replicates=1000,mean=float(np.nanmean(arr)) if np.isfinite(arr).any() else None,
                        p_random_parent_roi_ge_gate=(1+int((arr>=obs).sum()))/1001 if obs is not None else None,
                        matched_strata="source x time_band x judged_odds_band",n_strata=len(groups))
    d.to_parquet(OUT/"scored_pairs.parquet",index=False)
    summary=dict(freeze=fz,population=dict(races=int(d.race_id.nunique()),horses=len(d),days=int(d.race_date.nunique()),
                  official_races=int(d.loc[official,"race_id"].nunique()),min_date=d.race_date.min(),max_date=d.race_date.max()),
                 judged_parity_max_abs_ev_delta=delta,metrics=metrics,official_subset=ometric,retention=retention,
                 guard_random_parent=null,interpretation="Exploratory reconstructed nonprice features, sparse outcomes")
    R.write(OUT/"results.json",summary)
    print(json.dumps(summary,ensure_ascii=False,indent=1),flush=True)


if __name__=="__main__":main()
