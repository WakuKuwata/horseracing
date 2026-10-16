"""パリミュチュエルでは判断時に買っても払戻は確定オッズ. 判断時の買い EV は p̂(J)×E[O_F|J]. 
   (1) 確定オッズ射影 Ô_F = O_J·exp(c(h)+d(h)·log q_J) を leave-one-day-out で推定し,
   (2) p̂ は 本番 ens15(J) / V2 3-seed 平均(J) の 2 通りで EV_buy=p̂×Ô_F を作り, 443 集合で件数・較正・精算 ROI・ens15 確定ピックとの重なりを見る."""
import json, glob, numpy as np, pandas as pd, lightgbm as lgb, importlib.util
from scipy import stats
ROOT="/Users/kuwatawaku/workspace/horseracing/"; SCR="/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/decision_odds/"
sp=importlib.util.spec_from_file_location("r02", ROOT+"scripts/roi_explore/missed_20261004/r02_drift.py"); D=importlib.util.module_from_spec(sp); sp.loader.exec_module(D)
spec=json.load(open(ROOT+"artifacts/market_ev/mev-ens15-v1/seed_01/model.spec.json")); FEATS,CATS,MAPS=spec["features"],spec["cats"],spec["cat_maps"]
P=pd.read_parquet(ROOT+"artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet"); P["mins"]=P.hours*60
P["qJ"]=(1/P.O_J)/P.groupby("pair_id").O_J.transform(lambda s:(1/s).sum()); P["lqJ"]=np.log(P.qJ); P["fw"]=np.log(P.O_F/P.O_J)
edges=[0,30,60,180,600,3000]; P["hb"]=pd.cut(P.mins,edges,labels=False)
S=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet").sort_values(["pair_id","horse_number"]).reset_index(drop=True)
S["mins"]=S.hours*60; S["hb"]=pd.cut(S.mins,edges,labels=False); S["qJ"]=(1/S.O_J)/S.groupby("pair_id").O_J.transform(lambda s:(1/s).sum()); S["lqJ"]=np.log(S.qJ)
# forward projector, leave-one-day-out
print("forward fit (all days) log(O_F/O_J)=c(h)+d(h)·log q_J:")
for b in range(5):
    g=P[P.hb==b]; sl,ic,r,p,se=stats.linregress(g.lqJ,g.fw); print(f"  hb{b} [{edges[b]},{edges[b+1]})min: c={ic:+.3f} d={sl:+.3f} resid sd={np.std(g.fw-(ic+sl*g.lqJ)):.3f} n={len(g)}")
lOF_hat=np.full(len(S),np.nan)
for day in S.race_date.unique():
    fit=P[P.race_date!=day]; tgt=S.race_date==day
    for b in range(5):
        g=fit[fit.hb==b]; m=tgt&(S.hb==b)
        if m.sum()==0: continue
        if len(g)<50: g=fit
        sl,ic,*_=stats.linregress(g.lqJ,g.fw); lOF_hat[m.to_numpy()]=np.log(S.O_J[m])+ic+sl*S.lqJ[m]
S["OF_hat"]=np.exp(lOF_hat)
print("projection quality: corr(log Ô_F, log O_F)=%.3f  sd resid=%.3f  (naive O_J: corr=%.3f sd=%.3f)"%(np.corrcoef(lOF_hat,np.log(S.O_F))[0,1],np.std(np.log(S.O_F)-lOF_hat),np.corrcoef(np.log(S.O_J),np.log(S.O_F))[0,1],np.std(np.log(S.O_F/S.O_J))))
# p̂ sources
def X(df,logmin=None):
    x=df[FEATS].astype(float).copy()
    for c in CATS: x[c]=df[c].astype(object).map(lambda v: MAPS[c].get(str(v),np.nan) if v is not None and v==v else np.nan).astype(float)
    if logmin is not None: x["log_min"]=logmin
    return x.to_numpy(np.float32)
d=S.copy(); d["odds"]=d.O_J.astype(float); d["rid"]=d.race_id; d["race_id"]=d.pair_id
m=D.market_block(d[["race_id","horse_id","horse_number","odds"]],"race_id")
for c in D.MARKET_COLS: d[c]=m[c].to_numpy()
d["popularity"]=d["odds_rank"]
pV2=np.mean([lgb.Booster(model_file=f).predict(X(d,np.log(S.mins.to_numpy()))) for f in sorted(glob.glob(SCR+"spike_V2_s*_2026.txt"))],0)
pEns=(S.EVe_J/S.O_J).to_numpy()
y=S.won.to_numpy(float); pay=S.settle_multiplier.to_numpy(); ensF=S.EVe_F.to_numpy()
def rep(name,ev,p,thr=1.2):
    m=ev>thr; n=m.sum()
    if n==0: print(f"{name:>34}: n=0"); return
    print(f"{name:>34} >{thr}: n={n:3d} won={int(y[m].sum()):2d} Σp̂={p[m].sum():5.1f} ratio={y[m].sum()/p[m].sum():.2f} settledROI={pay[m].mean():.2f} | ens15 F EV>1.2 overlap={((ensF>1.2)&m).sum()/n:.2f} | med O_F/O_J={np.exp(np.median(np.log(S.O_F/S.O_J)[m])):.2f} med EV_F(ens15)={np.median(ensF[m]):.2f}")
for thr in (1.2,1.0):
    print(f"--- threshold {thr}")
    rep("ens15 p̂_J × O_J (現状)",pEns*S.O_J,pEns,thr)
    rep("ens15 p̂_J × Ô_F (射影のみ)",pEns*S.OF_hat,pEns,thr)
    rep("V2 p̂_J × O_J (注入のみ)",pV2*S.O_J,pV2,thr)
    rep("V2 p̂_J × Ô_F (注入+射影)",pV2*S.OF_hat,pV2,thr)
    rep("参照: ens15 確定 EV_F(買えない)",ensF,S.EVe_F/S.O_F,thr)
# how far is V2 p̂ from q_F (the calibrated closing truth)? and ens15 p̂_J
qF=S.q_F.to_numpy()
for nm,p in (("ens15 J",pEns),("V2 J",pV2),("ens15 F",(S.EVe_F/S.O_F).to_numpy())):
    print(f"NLL vs won: {nm}: {-np.mean(y*np.log(np.clip(p,1e-6,1))+(1-y)*np.log(np.clip(1-p,1e-6,1))):.5f}; mean |log p/q_F|={np.mean(np.abs(np.log(p/qF))):.3f}")
