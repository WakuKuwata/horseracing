import json, numpy as np, pandas as pd
ROOT="/Users/kuwatawaku/workspace/horseracing/"
C=ROOT+"artifacts/roi_comprehensive_20261005/calibration_roi/"
R=ROOT+"artifacts/roi_comprehensive_20261005/residual_spike/"
rec=json.load(open(C+"recipes.json"))
print("year | a(q only) | a,b (q+ens) | a+b | nll_q | nll_qens | dNLL")
for y,v in rec.items():
    a=v["fits"]["calibrated_q"]["coefficients"][0]; ab=v["fits"]["calibrated_q_ens"]["coefficients"]
    print(f"{y} | {a:.4f} | {ab[0]:.4f},{ab[1]:.4f} | {sum(ab):.4f} | {v['fits']['calibrated_q']['nll']:.5f} | {v['fits']['calibrated_q_ens']['nll']:.5f} | {v['fits']['calibrated_q']['nll']-v['fits']['calibrated_q_ens']['nll']:.5f}")
d=pd.read_parquet(C+"predictions.parquet")
print(d.columns.tolist(), len(d))
d=d.sort_values(["race_id","horse_number"]).reset_index(drop=True)
g=d.groupby("race_id",sort=False)
S=g.odds.transform(lambda o:(1/o).sum())
d["S"]=S
d["ev_q"]=d.p_calibrated_q*d.odds
d["ev_qe"]=d.p_calibrated_q_ens*d.odds
d["ev_s3"]=d.p_ens*d.odds
d["ev_n1"]=d.p_ens_norm*d.odds
race=g.agg(S=("S","first"),max_ev_q=("ev_q","max"),year=("year","first"),n=("odds","size"))
print("races",len(race),"S<1:",(race.S<1).sum(),"S<0.9:",(race.S<0.9).sum())
print("max ev_q among S>=1 races:",race.loc[race.S>=1,"max_ev_q"].max())
print("max ev_q among S<1 races:",race.loc[race.S<1,"max_ev_q"].max())
print("S quantiles:",race.S.quantile([0,.001,.01,.5,.99,1]).round(4).to_dict())
# theoretical bound: ev_q_i = q_i^(a-1)/(S*sum q^a) <= 1/(S*sum q^a); check numerically
print("ev_q>1.2 rows:",(d.ev_q>1.2).sum()," all in S<1:",bool((d.loc[d.ev_q>1.2,"S"]<1).all()))
# q+ens selections: where do they sit (odds bands)?
for nm,col in [("q_ens","ev_qe"),("S3","ev_s3"),("N1","ev_n1")]:
    m=d[col]>1.2
    sel=d[m]
    print(nm,"n",m.sum(),"roi",round((sel.won*sel.odds).sum()/m.sum(),4),"mean odds",round(sel.odds.mean(),2),"median odds",sel.odds.median(),"frac odds>=20",round((sel.odds>=20).mean(),3),"frac odds<5",round((sel.odds<5).mean(),3))
# overlap N1 vs S3
m1=d.ev_n1>1.2; m3=d.ev_s3>1.2
print("N1&S3",(m1&m3).sum(),"N1only",(m1&~m3).sum(),"S3only",(~m1&m3).sum())
for nm,m in [("N1only",m1&~m3),("S3only",~m1&m3),("both",m1&m3)]:
    sel=d[m]; print(nm,"n",m.sum(),"hits",int(sel.won.sum()),"roi",round((sel.won*sel.odds).sum()/max(m.sum(),1),4),"mean odds",round(sel.odds.mean(),2))
# per-race sum of p_ens: N1 differs from S3 only via sum p_ens
sp=g.p_ens.transform("sum")
print("sum p_ens quantiles",sp.groupby(d.race_id).first().quantile([.01,.1,.5,.9,.99]).round(3).to_dict())
# ----- residual vs q and ens vs q NLL -----
r=pd.read_parquet(R+"predictions.parquet").sort_values(["race_id","horse_number"]).reset_index(drop=True)
w=r[r.won]
for y,gg in w.groupby("year"):
    print(y,"n",len(gg),"NLL q",round(-np.log(gg.q).mean(),5),"NLL ens_norm",round(-np.log(gg.p_ens_norm).mean(),5),"NLL residual",round(-np.log(gg.p_residual).mean(),5),
          "ens-q",round((-np.log(gg.p_ens_norm)+np.log(gg.q)).mean(),5),"res-q",round((-np.log(gg.p_residual)+np.log(gg.q)).mean(),5))
print("ALL ens-q",round((-np.log(w.p_ens_norm)+np.log(w.q)).mean(),5),"res-q",round((-np.log(w.p_residual)+np.log(w.q)).mean(),5),"res-ens",round((-np.log(w.p_residual)+np.log(w.p_ens_norm)).mean(),5))
# ----- single seed vs ens15 NLL gap on same races (2023-2026) -----
RES=ROOT+"artifacts/roi_explore/results/"
def sp_(s): return RES+("armC_binary_drop-sameday+weightlive_from2007_ens15" if s==1 else f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15")+"/predictions.parquet"
keys=r[["race_id","horse_id","odds","won","year"]].copy()
seednll={}
for s in range(1,16):
    v=pd.read_parquet(sp_(s),columns=["race_id","horse_id","pred"])
    a=keys.merge(v,on=["race_id","horse_id"],how="left",validate="one_to_one")
    p=(1+a.pred.to_numpy()/100)/a.odds.to_numpy()
    pn=p/pd.Series(p).groupby(a.race_id.to_numpy()).transform("sum").to_numpy()
    seednll[s]=float(-np.log(pn[a.won.to_numpy(bool)]).mean())
ens=float(-np.log(r.p_ens_norm[r.won]).mean())
print("single-seed NLL (2023-26):",{k:round(v,5) for k,v in seednll.items()})
print("mean single seed",round(np.mean(list(seednll.values())),5),"ens15",round(ens,5),"gap ens - mean seed",round(ens-np.mean(list(seednll.values())),5))
