"""M2: 設計 B 用 — 確定オッズ O_F を条件とした判断時オッズのずれ分布 log(O_J/O_F) を帯×horizon で推定.
   レース内の共通成分(分散分解)・判断時の控除率(Σ1/O)も出す."""
import numpy as np, pandas as pd
ROOT="/Users/kuwatawaku/workspace/horseracing/"
P=pd.read_parquet(ROOT+"artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet")
pd.set_option("display.width",250); pd.set_option("display.max_columns",40)
P["mins"]=P.hours*60
P["u"]=np.log(P.O_J/P.O_F)          # 注入する向き: O_J = O_F·exp(u)
P["hb"]=pd.cut(P.mins,[0,10,30,60,180,600,3000],labels=["<10m","10-30m","30-60m","1-3h","3-10h",">10h"])
P["bF"]=pd.cut(P.O_F,[1,5,10,20,40,100,1e4],right=False,labels=["1-5","5-10","10-20","20-40","40-100","100+"])
P["lqF"]=np.log(P.q_F)
# overround
ov=P.groupby("pair_id").agg(sJ=("O_J",lambda s:(1/s).sum()),sF=("O_F",lambda s:(1/s).sum()),hb=("hb","first"),src=("source","first"))
print("Σ1/O 判断時 vs 確定:"); print(ov.groupby(["hb"],observed=True)[["sJ","sF"]].agg(["mean","std"]).round(4))
print(ov.groupby("src")[["sJ","sF"]].mean().round(4))
# mean/sd of u by bF x hb
def agg(g): return pd.Series(dict(n=len(g),mean=g.u.mean(),sd=g.u.std(),p10=g.u.quantile(.1),p90=g.u.quantile(.9)))
print("\n### log(O_J/O_F) by 確定オッズ帯 × horizon")
t=P.groupby(["hb","bF"],observed=True).apply(agg).round(3)
print(t.unstack(0).to_string())
print("\n### by 確定オッズ帯(全 horizon)"); print(P.groupby("bF",observed=True).apply(agg).round(3).to_string())
print("\n### by horizon(全帯)"); print(P.groupby("hb",observed=True).apply(agg).round(3).to_string())
print("\n### by source × horizon"); print(P.groupby(["source","hb"],observed=True).apply(agg).round(3).to_string())
# linear model: u ~ a(h) + b(h)*log q_F  (regression toward mean in log space)
from scipy import stats
print("\n### u = a + b·log(q_F) by horizon  (b<0 → 穴ほど判断時オッズが短い=確定で伸びる)")
for hb,g in P.groupby("hb",observed=True):
    sl,ic,r,p,se=stats.linregress(g.lqF,g.u); res=g.u-(ic+sl*g.lqF)
    # race-level common component of residual
    rm=res.groupby(g.pair_id).transform("mean"); vb=rm.var(); vt=res.var()
    print(f"{hb:>7}: a={ic:+.3f} b={sl:+.3f} (se {se:.3f}) resid sd={res.std():.3f}  race-common var share={vb/vt:.3f}  n={len(g)} races={g.pair_id.nunique()}")
# pooled with log(mins)
P["lm"]=np.log(P.mins)
import numpy.linalg as la
X=np.column_stack([np.ones(len(P)),P.lqF,P.lm,P.lqF*P.lm]); beta,*_=la.lstsq(X,P.u,rcond=None)
res=P.u-X@beta
print("\npooled u = %.3f %+.3f·logq %+.3f·log(min) %+.3f·logq·log(min); resid sd %.3f"%(*beta,res.std()))
# residual sd vs horizon after removing mean structure
P["res"]=res
print(P.groupby("hb",observed=True).res.agg(["std","count"]).round(3))
# within-race correlation: average pairwise corr of residuals approximated via var decomposition
rm=P.groupby("pair_id").res.transform("mean"); print("race-common var share of resid (pooled):",round(rm.var()/P.res.var(),3))
# and correlation between horses' u and their q_F constraint: Σq_J=1 forces sum of shifts; check corr(res, mean res of others)
oth=(P.groupby("pair_id").res.transform("sum")-P.res)/(P.groupby("pair_id").res.transform("size")-1)
print("corr(res_i, mean res of other horses in race):",round(np.corrcoef(P.res,oth)[0,1],3))
