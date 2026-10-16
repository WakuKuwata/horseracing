"""V2(seed1) の判断時ピックの中身: オッズ帯・出所・horizon 別、精算 ROI、horizon 応答(同じ判断時オッズで h を変えた時の p̂)."""
import sys, json, numpy as np, pandas as pd, lightgbm as lgb, importlib.util
ROOT="/Users/kuwatawaku/workspace/horseracing/"; SCR="/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/decision_odds/"
sp=importlib.util.spec_from_file_location("r02", ROOT+"scripts/roi_explore/missed_20261004/r02_drift.py"); D=importlib.util.module_from_spec(sp); sp.loader.exec_module(D)
spec=json.load(open(ROOT+"artifacts/market_ev/mev-ens15-v1/seed_01/model.spec.json")); FEATS,CATS,MAPS=spec["features"],spec["cats"],spec["cat_maps"]
pd.set_option("display.width",250)
S=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet").sort_values(["pair_id","horse_number"]).reset_index(drop=True)
base=lgb.Booster(model_file=ROOT+"artifacts/market_ev/mev-ens15-v1/seed_01/model_2026.txt")
new=lgb.Booster(model_file=SCR+"spike_V2_s1_2026.txt") if __import__("os").path.exists(SCR+"spike_V2_s1_2026.txt") else lgb.Booster(model_file=SCR+"spike_V2_2026.txt")
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
J=at("J"); F=at("F"); h=J.hours.to_numpy()*60
pbJ=base.predict(X(J)); pnJ=new.predict(X(J,np.log(h))); pbF=base.predict(X(F)); pnF=new.predict(X(F,0.0))
S["evbJ"]=pbJ*S.O_J; S["evnJ"]=pnJ*S.O_J; S["evbF"]=pbF*S.O_F; S["evnF"]=pnF*S.O_F
S["bJ2"]=pd.cut(S.O_J,[1,5,10,20,40,100,1e4],right=False,labels=["1-5","5-10","10-20","20-40","40-100","100+"])
S["hb"]=pd.cut(h,[0,10,30,60,180,600,3000],labels=["<10m","10-30m","30-60m","1-3h","3-10h",">10h"])
pay=S.settle_multiplier.to_numpy()
for nm,m in (("base EV_J>1.2",S.evbJ>1.2),("V2 EV_J>1.2",S.evnJ>1.2)):
    g=S[m]; print(f"\n### {nm}: n={m.sum()} won={int(g.won.sum())} settled ROI={pay[m].sum()/m.sum():.3f}  Σp̂={ (pnJ if 'V2' in nm else pbJ)[m].sum():.2f}")
    print(g.groupby("bJ2",observed=True).agg(n=("won","size"),won=("won","sum"),roi=("settle_multiplier","mean"),medOF_OJ=("d",lambda s:np.exp(s.median()))).round(3).T.to_string())
    print(g.groupby("source").agg(n=("won","size"),won=("won","sum"),roi=("settle_multiplier","mean")).round(3).T.to_string())
    print(g.groupby("hb",observed=True).agg(n=("won","size"),won=("won","sum"),roi=("settle_multiplier","mean")).round(3).T.to_string())
both=(S.evbJ>1.2)&(S.evnJ>1.2); print("\noverlap base∩V2 at J:",int(both.sum()),"base only",int(((S.evbJ>1.2)&~(S.evnJ>1.2)).sum()),"V2 only",int((~(S.evbJ>1.2)&(S.evnJ>1.2)).sum()))
# ROI at settlement by band of EV (V2 J) — dose response
for lo,hi in ((1.0,1.1),(1.1,1.2),(1.2,1.3),(1.3,9)):
    m=(S.evnJ>=lo)&(S.evnJ<hi); mb=(S.evbJ>=lo)&(S.evbJ<hi)
    print(f"EV_J [{lo},{hi}): V2 n={m.sum()} won={int(S.won[m].sum())} ratio={S.won[m].sum()/max(pnJ[m].sum(),1e-9):.2f} roi={pay[m].sum()/max(m.sum(),1):.2f} | base n={mb.sum()} won={int(S.won[mb].sum())} ratio={S.won[mb].sum()/max(pbJ[mb].sum(),1e-9):.2f} roi={pay[mb].sum()/max(mb.sum(),1):.2f}")
# horizon response: same judged odds, vary assumed h
print("\n### horizon 応答: 判断時オッズ固定で h を変えた p̂ の中央値比(対 h=1 分)・EV>1.2 件数")
for mins in (1,5,10,30,60,180,400,900):
    p=new.predict(X(J,np.log(mins)))
    r=p/pnF_dummy if False else None
    print(f"h={mins:>4}min: median p̂/p̂(h=1)={np.median(p/new.predict(X(J,0.0))):.3f}  by O_J band:",
          {b:round(float(np.median((p/new.predict(X(J,0.0)))[S.bJ2==b])),3) for b in S.bJ2.cat.categories}, " n EV>1.2:",int((p*S.O_J>1.2).sum()))
