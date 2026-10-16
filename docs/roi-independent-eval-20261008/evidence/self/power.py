"""Power of the 138 checkpoint rule (300/600 picks, race-day bootstrap CI) under the empirical
payout distribution of S3 (ens15 EV>1.2, 100-yen win bets, stored-odds settlement). Read-only."""
import glob, numpy as np, pandas as pd
R="/Users/kuwatawaku/workspace/horseracing/artifacts/roi_explore/results"
files=[f"{R}/armC_binary_drop-sameday+weightlive_from2007_ens15/predictions.parquet"]+[f"{R}/armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15/predictions.parquet" for s in range(2,16)]
dfs=[pd.read_parquet(f) for f in files]
base=dfs[0][["race_id","horse_id","race_date","year","odds","ret"]].copy()
ev=np.mean([1+d["pred"].to_numpy()/100 for d in dfs],axis=0)
base["ev"]=ev
base=base[(base.race_date>="2013-01-01")&(base.race_date<="2026-06-26")]
# dead heats: ret>0 with multiple winners? use 'ret' as payout/100 proxy: payout = ret? check
print(base[["odds","ret"]].describe().T)
print("ret>0 share", (base.ret>0).mean())
s3=base[base.ev>1.2].copy()
# settlement: won*odds*100 — ret appears to be odds if won else 0? verify relation
w=s3[s3.ret>0]; print("ret vs odds for winners: ratio stats", (w.ret/w.odds).describe())
s3["pay"]=np.where(s3.ret>0, s3.odds, 0.0)  # in 100-yen units
print("S3 n", len(s3), "hits", (s3.pay>0).sum(), "ROI", s3.pay.sum()/len(s3))
print("per-bet pay mean", s3.pay.mean(), "sd", s3.pay.std(), "mean odds", s3.odds.mean())
# per-year counts
print(s3.groupby("year").size().to_string())
rng=np.random.default_rng(20261007)
s3=s3.sort_values(["race_date","race_id"]).reset_index(drop=True)
days=s3.race_date.to_numpy(); pays=s3.pay.to_numpy()
uday=np.unique(days); day_idx={d:np.where(days==d)[0] for d in uday}
def ci_rule(pay_sub, day_sub, B=2000):
    # race-day cluster bootstrap of ROI = sum(pay)/n
    ud=np.unique(day_sub); groups=[pay_sub[day_sub==d] for d in ud]
    tot=np.array([g.sum() for g in groups]); cnt=np.array([len(g) for g in groups])
    k=len(groups); idx=rng.integers(0,k,size=(B,k))
    roi=tot[idx].sum(1)/cnt[idx].sum(1)
    return np.percentile(roi,2.5), np.percentile(roi,97.5)
def simulate(true_roi, n, trials=400):
    # scale payouts so that population ROI == true_roi (multiplicative on payout), then draw n consecutive picks
    # starting at random day, preserving day clustering
    scale=true_roi/(pays.sum()/len(pays))
    res={"passed":0,"failed":0,"cross":0}
    for _ in range(trials):
        start=rng.integers(0,len(uday)-50)
        sel=[]; d=start
        while len(sel)<n and d<len(uday):
            sel.extend(day_idx[uday[d]]); d+=1
        sel=np.array(sel[:n])
        lo,hi=ci_rule(pays[sel]*scale, days[sel])
        if lo>1: res["passed"]+=1
        elif hi<1: res["failed"]+=1
        else: res["cross"]+=1
    return {k:v/trials for k,v in res.items()}
for n in (300,600,1200,2400):
    for tr in (0.85,1.0,1.12,1.21):
        print(n,tr,simulate(tr,n))
