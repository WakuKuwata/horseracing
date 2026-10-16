"""Adversarial re-derivation of NP-03 numbers. Different implementation: numpy only after load,
race aggregation via np.add.at on factorized ids, cluster (race-day) SE for the 12pt gap."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path("/Users/kuwatawaku/workspace/horseracing")
OUT = Path(sys.argv[1])
rows = pd.read_parquet(ROOT/"artifacts/market_ev/rows_2007.parquet",
    columns=["race_id","horse_id","race_date","year","odds","won","race_ok","dead_heat"])
rows = rows[(rows.race_date>="2010-01-01")&(rows.race_date<="2026-06-26")&rows.race_ok&~rows.dead_heat]
rows = rows.sort_values(["race_id","horse_id"]).reset_index(drop=True)
key = pd.MultiIndex.from_frame(rows[["race_id","horse_id"]])
tags = ["armC_binary_drop-sameday+weightlive_from2007_ens15"]+[f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2,16)]
P = np.empty((len(rows),15))
for i,t in enumerate(tags):
    p = pd.read_parquet(ROOT/"artifacts/roi_explore/results"/t/"predictions.parquet", columns=["race_id","horse_id","pred"])
    p = p.set_index(["race_id","horse_id"]).reindex(key)
    assert not p.pred.isna().any()
    P[:,i] = p.pred.to_numpy(float)
odds = rows.odds.to_numpy(float); won = rows.won.to_numpy(bool); yr = rows.year.to_numpy(int)
EV = 1+P/100
ev_ens = EV.mean(1)
pbar = (EV/odds[:,None]).mean(1)
rid, _ = pd.factorize(rows.race_id)
sump_r = np.zeros(rid.max()+1); np.add.at(sump_r, rid, pbar)
sump = sump_r[rid]
# check: for each race, sum_p = sum over ALL rows of population (consistent with investigator)
pay = np.where(won, odds*100, 0.0)
app = (yr>=2013)&(yr<=2026); R = (yr>=2023)
def roi(m):
    n=int(m.sum()); return dict(n=n, hits=int(won[m].sum()), roi=float(pay[m].sum()/(100*n)) if n else None)
S3 = app&(ev_ens>1.2); N1 = app&(ev_ens>1.2*sump)
out = {"S3":roi(S3),"N1":roi(N1)}
nN1 = N1.sum(); t = np.sort(ev_ens[app])[::-1][nN1-1]
CM = app&(ev_ens>=t); out["count_matched"]={"t":float(t),**roi(CM)}
out["S3_lt105"]=roi(S3&(sump<1.05)); out["S3_ge105"]=roi(S3&(sump>=1.05))
out["N1_ge105_R"]=roi(N1&R&(sump>=1.05)); out["N1_lt105_R"]=roi(N1&R&(sump<1.05))
out["S3_R"]=roi(S3&R); out["N1_R"]=roi(N1&R)
bins=[0,0.9,0.95,1.0,1.05,1.1,9]
out["S3_by_bin"]={f"{bins[i]}-{bins[i+1]}":roi(S3&(sump>=bins[i])&(sump<bins[i+1])) for i in range(len(bins)-1)}
out["N1_by_bin"]={f"{bins[i]}-{bins[i+1]}":roi(N1&(sump>=bins[i])&(sump<bins[i+1])) for i in range(len(bins)-1)}
# gap S3<1.05 vs S3>=1.05: iid SE and race-day cluster bootstrap
a=S3&(sump<1.05); b=S3&(sump>=1.05)
ret=pay/100
sd=ret[S3].std(ddof=1)
out["gap"]={"diff":float(ret[a].mean()-ret[b].mean()),"sd_ret_S3":float(sd),
  "se_iid":float(np.sqrt(ret[a].var(ddof=1)/a.sum()+ret[b].var(ddof=1)/b.sum()))}
day,_=pd.factorize(rows.race_date); nd=day.max()+1
rng=np.random.default_rng(7); B=4000
sa=np.bincount(day,weights=ret*a,minlength=nd); na=np.bincount(day,weights=a,minlength=nd)
sb=np.bincount(day,weights=ret*b,minlength=nd); nb=np.bincount(day,weights=b,minlength=nd)
diffs=[]
for _ in range(B):
    w=rng.multinomial(nd,np.ones(nd)/nd)
    diffs.append((w@sa)/(w@na)-(w@sb)/(w@nb))
diffs=np.array(diffs)
out["gap"]["se_cluster_day"]=float(diffs.std(ddof=1)); out["gap"]["ci95_cluster_day"]=[float(np.quantile(diffs,.025)),float(np.quantile(diffs,.975))]
# N1 vs count-matched gap, cluster
c=CM; d_=N1
sc=np.bincount(day,weights=ret*c,minlength=nd); nc=np.bincount(day,weights=c,minlength=nd)
sdn=np.bincount(day,weights=ret*d_,minlength=nd); ndn=np.bincount(day,weights=d_,minlength=nd)
dd=[]
for _ in range(B):
    w=rng.multinomial(nd,np.ones(nd)/nd); dd.append((w@sdn)/(w@ndn)-(w@sc)/(w@nc))
dd=np.array(dd); out["N1_minus_CM"]={"diff":float(ret[d_].mean()-ret[c].mean()),"se_cluster_day":float(dd.std(ddof=1)),"ci95":[float(np.quantile(dd,.025)),float(np.quantile(dd,.975))]}
# monotonicity check with cluster SE per bin for S3
pb={}
for k,(lo,hi) in {f"{bins[i]}-{bins[i+1]}":(bins[i],bins[i+1]) for i in range(len(bins)-1)}.items():
    m=S3&(sump>=lo)&(sump<hi); s=np.bincount(day,weights=ret*m,minlength=nd); n=np.bincount(day,weights=m,minlength=nd)
    v=[]; 
    for _ in range(1000):
        w=rng.multinomial(nd,np.ones(nd)/nd); v.append((w@s)/(w@n))
    pb[k]=float(np.std(v,ddof=1))
out["S3_bin_se_cluster"]=pb
(OUT/"np03.json").write_text(json.dumps(out,indent=1)); print(json.dumps(out,indent=1))
