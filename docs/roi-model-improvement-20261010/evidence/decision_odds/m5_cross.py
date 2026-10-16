"""交差保持率: 判断時は注入モデル(V1/V2 各 seed)、確定時は現行 seed-1 モデル。+ 3 seed 平均の判断時較正."""
import json, glob, numpy as np, pandas as pd, lightgbm as lgb, importlib.util
ROOT="/Users/kuwatawaku/workspace/horseracing/"; SCR="/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/decision_odds/"
sp=importlib.util.spec_from_file_location("r02", ROOT+"scripts/roi_explore/missed_20261004/r02_drift.py"); D=importlib.util.module_from_spec(sp); sp.loader.exec_module(D)
spec=json.load(open(ROOT+"artifacts/market_ev/mev-ens15-v1/seed_01/model.spec.json")); FEATS,CATS,MAPS=spec["features"],spec["cats"],spec["cat_maps"]
S=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet").sort_values(["pair_id","horse_number"]).reset_index(drop=True)
def X(df,logmin=None):
    x=df[FEATS].astype(float).copy()
    for c in CATS: x[c]=df[c].astype(object).map(lambda v: MAPS[c].get(str(v),np.nan) if v is not None and v==v else np.nan).astype(float)
    if logmin is not None: x["log_min"]=logmin
    return x.to_numpy(np.float32)
def at(v):
    d=S.copy(); d["odds"]=d[f"O_{v}"].astype(float); d["rid"]=d.race_id; d["race_id"]=d.pair_id
    m=D.market_block(d[["race_id","horse_id","horse_number","odds"]],"race_id")
    for c in D.MARKET_COLS: d[c]=m[c].to_numpy()
    d["popularity"]=d["odds_rank"]; d["race_id"]=d.rid; return d
J=at("J"); F=at("F"); h=J.hours.to_numpy()*60; y=S.won.to_numpy(float); pay=S.settle_multiplier.to_numpy()
base=lgb.Booster(model_file=ROOT+"artifacts/market_ev/mev-ens15-v1/seed_01/model_2026.txt"); pbF=base.predict(X(F)); evbF=pbF*S.O_F
ensF=S.EVe_F.to_numpy()  # 15-seed 本番
def report(name,pJ):
    evJ=pJ*S.O_J; m=evJ>1.2; m10=evJ>1.0
    print(f"{name:>14}: EV_J>1.2 n={m.sum():3d} won={int(y[m].sum()):2d} Σp̂={pJ[m].sum():.1f} ratio={y[m].sum()/pJ[m].sum():.2f} settledROI={pay[m].mean():.2f} | keep under prod-seed1 F: {((evbF>1.2)&m).sum()/m.sum():.2f} under ens15 F: {((ensF>1.2)&m).sum()/m.sum():.2f} | median EV_F(ens15)/EV_J={np.median(ensF[m]/evJ[m]):.2f} | EV_J>1.0 n={m10.sum()} won={int(y[m10].sum())} ratio={y[m10].sum()/pJ[m10].sum():.2f}")
    return pJ
pbJ=base.predict(X(J)); report("prod seed1",pbJ); report("prod ens15",(S.EVe_J/S.O_J).to_numpy())
ps=[]
for f in sorted(glob.glob(SCR+"spike_V2_s*_2026.txt")):
    b=lgb.Booster(model_file=f); p=b.predict(X(J,np.log(h))); ps.append(p); report(f.split("/")[-1][:-9],p)
pm=np.mean(ps,0); report("V2 3-seed mean",pm)
# by horizon for 3-seed mean
evJ=pm*S.O_J; hb=pd.cut(h,[0,30,60,180,600,3000],labels=["<30m","30-60m","1-3h","3-10h",">10h"])
for b_ in hb.categories:
    m=(evJ>1.2)&(hb==b_).to_numpy(); n=m.sum()
    if n: print(f"   V2mean EV_J>1.2 @{b_}: n={n} won={int(y[m].sum())} ratio={y[m].sum()/pm[m].sum():.2f} settledROI={pay[m].mean():.2f}")
# NLL at J: prod ens15 vs V2 mean
def nll(p): p=np.clip(p,1e-6,1-1e-6); return -np.mean(y*np.log(p)+(1-y)*np.log(1-p))
print("NLL_J ens15 %.5f  seed1 %.5f  V2mean %.5f"%(nll((S.EVe_J/S.O_J).to_numpy()),nll(pbJ),nll(pm)))
