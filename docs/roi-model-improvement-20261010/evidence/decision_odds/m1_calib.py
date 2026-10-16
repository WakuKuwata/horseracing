"""M1: 判断時 p̂ の較正(EV_J 帯別)・保持率・p̂/オッズの動き. 読み取りのみ."""
import numpy as np, pandas as pd, json, sys
from scipy import stats
ROOT="/Users/kuwatawaku/workspace/horseracing/"
SP=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet")
PR=pd.read_parquet(ROOT+"artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet")
pd.set_option("display.width",250); pd.set_option("display.max_columns",40)
rng=np.random.default_rng(20261010)
B=4000

def prep(d):
    d=d.copy()
    d["pJ"]=d.EVe_J/d.O_J; d["pF"]=d.EVe_F/d.O_F
    d["qJ"]=(1/d.O_J)/d.groupby("pair_id").O_J.transform(lambda s:(1/s).sum())
    d["qF"]=(1/d.O_F)/d.groupby("pair_id").O_F.transform(lambda s:(1/s).sum())
    d["ld"]=np.log(d.O_F/d.O_J); d["lp"]=np.log(d.pF/d.pJ); d["lev"]=np.log(d.EVe_F/d.EVe_J)
    d["mins"]=d.hours*60
    d["evb"]=pd.cut(d.EVe_J,[0,0.8,1.0,1.2,1.5,9],labels=["<0.8","0.8-1.0","1.0-1.2","1.2-1.5",">1.5"])
    d["evb2"]=pd.cut(d.EVe_J,[0,1.0,1.2,9],labels=["<=1.0","1.0-1.2",">1.2"])
    d["hb"]=pd.cut(d.mins,[0,10,30,60,180,600,3000],labels=["<10m","10-30m","30-60m","1-3h","3-10h",">10h"])
    return d

def block_ci(d, num_col, den_col, mask):
    g=d[mask].groupby("race_date").agg(n=(num_col,"sum"),s=(den_col,"sum"))
    if len(g)==0: return (np.nan,np.nan)
    n=g.n.to_numpy(); s=g.s.to_numpy(); k=len(g)
    idx=rng.integers(0,k,size=(B,k))
    r=n[idx].sum(1)/np.maximum(s[idx].sum(1),1e-9)
    return tuple(np.quantile(r,[.025,.975]))

def pois_ci(w, s):
    # Garwood exact interval for count w, scaled by Σp̂
    lo=stats.chi2.ppf(.025,2*w)/2 if w>0 else 0.0
    hi=stats.chi2.ppf(.975,2*w+2)/2
    return lo/s, hi/s

def calib_table(d, bands, label):
    rows=[]
    for b in list(bands.cat.categories)+["ALL"]:
        m=np.ones(len(d),bool) if b=="ALL" else (bands==b).to_numpy()
        if m.sum()==0: continue
        w=d.won[m].sum(); sJ=d.pJ[m].sum(); sF=d.pF[m].sum(); sq=d.qF[m].sum(); sqJ=d.qJ[m].sum()
        rJ=w/sJ; rF=w/sF
        pl,ph=pois_ci(w,sJ); bl,bh=block_ci(d,"won","pJ",m)
        rows.append(dict(band=b,n=int(m.sum()),races=d.race_id[m].nunique(),won=int(w),
            sum_pJ=round(sJ,2),ratio_J=round(rJ,3),poisCI_J=f"[{pl:.2f},{ph:.2f}]",blockCI_J=f"[{bl:.2f},{bh:.2f}]",
            sum_pF=round(sF,2),ratio_F=round(rF,3),ratio_qF=round(w/sq,3),ratio_qJ=round(w/sqJ,3),
            roi_J_at_F=round((d.ret[m].sum()/m.sum()+100)/100,3),
            mean_EVJ=round(d.EVe_J[m].mean(),3),mean_EVF=round(d.EVe_F[m].mean(),3),
            med_pF_pJ=round(np.exp(d.lp[m].median()),3),p25_pF_pJ=round(np.exp(d.lp[m].quantile(.25)),3),p75_pF_pJ=round(np.exp(d.lp[m].quantile(.75)),3),
            med_OF_OJ=round(np.exp(d.ld[m].median()),3),med_EVF_EVJ=round(np.exp(d.lev[m].median()),3),
            keep_EVF_gt12=round((d.EVe_F[m]>1.2).mean(),3)))
    t=pd.DataFrame(rows); print(f"\n### {label}"); print(t.to_string(index=False)); return t

out={}
# ---- 443 集合 (scored_pairs = 1 pair/race, nearest to post), ret 列 = 保存/公式精算
S=prep(SP)
S["ret"]=np.where(S.won,S.settle_multiplier*100-100,-100.0)  # settle_multiplier=公式払戻/100 or O_settle
print("443 set:",S.race_id.nunique(),"races",len(S),"horses; official races:",S[S.official_race].race_id.nunique())
print("source x tb (pairs):"); print(S.drop_duplicates("pair_id").groupby(["source","tb"]).size())
t1=calib_table(S,S.evb,"443 集合・EV_J 5 帯(判断時 p̂ の較正 Σwon/Σp̂_J; ratio_F=Σwon/Σp̂_F; ratio_qF=市場 q 基準)")
t2=calib_table(S,S.evb2,"443 集合・EV_J 3 帯")
O=S[S.official_race]
t3=calib_table(O,O.evb,"公式払戻あり集合・EV_J 5 帯")
t4=calib_table(O,O.evb2,"公式払戻あり集合・EV_J 3 帯")
# ---- stratify EV_J>1.2 by source and horizon band (use pairs.parquet: all 564 pairs)
P=prep(PR); P["ret"]=PR.ret
hi=P[P.EVe_J>1.2]
print("\n### EV_J>1.2 の層別(564 pairs 全体・レースは複数帯に出うる)")
def strat(g):
    w=g.won.sum(); sJ=g.pJ.sum()
    return pd.Series(dict(n=len(g),races=g.race_id.nunique(),won=int(w),ratio_J=round(w/sJ,3),ratio_F=round(w/g.pF.sum(),3),
        keep12=round((g.EVe_F>1.2).mean(),3),med_pF_pJ=round(np.exp(g.lp.median()),3),med_OF_OJ=round(np.exp(g.ld.median()),3),
        med_EVF_EVJ=round(np.exp(g.lev.median()),3),mean_EVF=round(g.EVe_F.mean(),3)))
print(hi.groupby("source").apply(strat).to_string())
print(hi.groupby("hb",observed=True).apply(strat).to_string())
print(hi.groupby(["source","tb"]).apply(strat).to_string())
# reference: all horses by hb
print("\n### 全馬 層別(較正の参照)")
print(P.groupby("hb",observed=True).apply(strat).to_string())
# ---- elasticity: log(pF/pJ) vs log(OF/OJ), all horses and EV_J>1.2
for nm,g in (("all",P),("EV_J>1.2",hi)):
    sl,ic,r,p,se=stats.linregress(g.ld,g.lp)
    print(f"\nelasticity d log p̂ / d log O ({nm}): slope={sl:.3f} se={se:.3f} r={r:.3f}; decomposition of median EV ratio: O ratio {np.exp(g.ld.median()):.3f} × p ratio {np.exp(g.lp.median()):.3f}")
# ---- 1 - EV_F/EV_J decomposition: p̂ part vs odds part among EV_J>1.2
print("\nEV_J>1.2: mean log(EV_F/EV_J)=%.3f = mean ld %.3f + mean lp %.3f"%(hi.lev.mean(),hi.ld.mean(),hi.lp.mean()))
# ---- 2026 full-year calibration reference from OOF predictions (closing odds): EV band ratio
