"""M3 spike(規模の見積り・採否には使わない): 学習時に判断時相当のオッズずれを注入した seed-1 モデル(2026 用・2007-2025 学習)を
   作り、(i) 確定オッズでの 2026 OOS NLL、(ii) R02 443 集合の判断時オッズでの較正・EV>1.2 件数・保持率 を seed-1 基準と比べる。
   注入: u=log(O_J/O_F)= a(h)+b(h)·log q_F + s(h)·(√ρ·ε_race + √(1-ρ)·ε_i), ρ=0.16, h は R02 の組の horizon 分布から抽出。
   読み取りのみ・成果物は scratchpad."""
import sys, json, time, numpy as np, pandas as pd, lightgbm as lgb
ROOT="/Users/kuwatawaku/workspace/horseracing/"
SCR="/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/decision_odds/"
sys.path.insert(0, ROOT+"scripts/roi_explore/missed_20261004")
import importlib.util
sp=importlib.util.spec_from_file_location("r02", ROOT+"scripts/roi_explore/missed_20261004/r02_drift.py"); D=importlib.util.module_from_spec(sp); sp.loader.exec_module(D)
MARKET=D.MARKET_COLS
spec=json.load(open(ROOT+"artifacts/market_ev/mev-ens15-v1/seed_01/model.spec.json"))
FEATS, CATS, MAPS = spec["features"], spec["cats"], spec["cat_maps"]
PARAMS=dict(objective="binary", learning_rate=.05, num_leaves=63, min_data_in_leaf=1000, feature_fraction=.8,
            bagging_fraction=.8, bagging_freq=1, lambda_l2=10., verbosity=-1, num_threads=10, seed=1, max_bin=255)
VARIANT=sys.argv[1] if len(sys.argv)>1 else "V1"
SEED=int(sys.argv[2]) if len(sys.argv)>2 else 1
PARAMS["seed"]=SEED
# horizon model from M2 (band lookup on minutes)
HB=[(0,10,.341,.142,.241),(10,30,.522,.220,.273),(30,60,.639,.274,.301),(60,180,.674,.292,.343),(180,600,.803,.336,.338),(600,3000,1.029,.364,.741)]
def hparams(mins):
    a=np.empty(len(mins));b=np.empty(len(mins));s=np.empty(len(mins))
    for lo,hi,aa,bb,ss in HB:
        m=(mins>=lo)&(mins<hi); a[m]=aa;b[m]=bb;s[m]=ss
    return a,b,s
RHO=0.16
pairs=pd.read_parquet(ROOT+"artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet")
H_POOL=pairs.drop_duplicates("pair_id").hours.to_numpy()*60   # 564 組の horizon(分)

def design(df):
    x=df[FEATS].astype(float).copy()
    for c in CATS:
        x[c]=df[c].astype(object).map(lambda v: MAPS[c].get(str(v), np.nan) if v is not None and v==v else np.nan).astype(float)
    if VARIANT=="V2": x["log_min"]=df["log_min"].astype(float)
    return x.to_numpy(np.float32)

def perturb(df, rng, mins=None):
    """df ordered by race; returns copy with judged-like odds and recomputed market cols."""
    d=df.copy()
    rid=d.race_id.to_numpy(); starts=np.r_[0,np.flatnonzero(rid[1:]!=rid[:-1])+1]; n_r=len(starts)
    seg=np.repeat(np.arange(n_r), np.diff(np.r_[starts,len(d)]))
    if mins is None: mins=rng.choice(H_POOL, size=n_r)
    a,b,s=hparams(mins)
    eps_r=rng.standard_normal(n_r); eps_i=rng.standard_normal(len(d))
    qF=d.q.to_numpy(float)
    u=a[seg]+b[seg]*np.log(qF)+s[seg]*(np.sqrt(RHO)*eps_r[seg]+np.sqrt(1-RHO)*eps_i)
    oj=d.odds.to_numpy(float)*np.exp(u)
    # keep per-race overround Σ1/O equal to the final one (judged 1.260 vs final 1.265 ≈ same)
    invF=np.bincount(seg,weights=1/d.odds.to_numpy(float)); invJ=np.bincount(seg,weights=1/oj)
    oj=oj*(invJ/invF)[seg]
    oj=np.maximum(np.round(oj,1),1.0)
    d["odds"]=oj; d["popularity"]=np.nan
    m=D.market_block(d[["race_id","horse_id","horse_number","odds"]].reset_index(drop=True),"race_id")
    for c in MARKET: d[c]=m[c].to_numpy()
    d["popularity"]=d["odds_rank"]
    d["log_min"]=np.log(mins)[seg]
    return d

t0=time.time()
rows=pd.read_parquet(ROOT+"artifacts/market_ev/rows_2007.parquet")
rows=rows.loc[rows.race_ok & ~rows.dead_heat].sort_values(["race_id","horse_number"]).reset_index(drop=True)
tr=rows[rows.year<=2025].reset_index(drop=True); te=rows[rows.year==2026].reset_index(drop=True)
print("train rows",len(tr),"test 2026 rows",len(te),"load %.0fs"%(time.time()-t0),flush=True)
rng=np.random.default_rng(20261010+SEED)
pt=perturb(tr,rng)
if VARIANT=="V2":
    tr0=tr.copy(); tr0["log_min"]=0.0   # 未摂動=発走 1 分前相当
    trn=pd.concat([tr0,pt],ignore_index=True)
else:
    trn=pt
X=design(trn); y=trn.won.to_numpy(float)
cat_idx=list(range(len(FEATS),len(FEATS)+len(CATS)))
print("fit",VARIANT,X.shape,flush=True)
bst=lgb.train(PARAMS, lgb.Dataset(X,y,categorical_feature=cat_idx,free_raw_data=True), num_boost_round=300)
bst.save_model(SCR+f"spike_{VARIANT}_s{SEED}_2026.txt")
print("fit done %.0fs"%(time.time()-t0),flush=True)
base=lgb.Booster(model_file=ROOT+"artifacts/market_ev/mev-ens15-v1/seed_01/model_2026.txt")
def pred_base(df):
    x=df[FEATS].astype(float).copy()
    for c in CATS: x[c]=df[c].astype(object).map(lambda v: MAPS[c].get(str(v), np.nan) if v is not None and v==v else np.nan).astype(float)
    return base.predict(x.to_numpy(np.float32),num_threads=4)
def pred_new(df,mins_for_v2=None):
    d=df.copy()
    if VARIANT=="V2": d["log_min"]=0.0 if mins_for_v2 is None else np.log(np.asarray(mins_for_v2,float))
    return bst.predict(design(d),num_threads=4)
def nll(p,y): p=np.clip(p,1e-6,1-1e-6); return -np.mean(y*np.log(p)+(1-y)*np.log(1-p))
out={}
# (i) closing-odds OOS 2026
yte=te.won.to_numpy(float)
pb=pred_base(te); pn=pred_new(te)
# race-normalized winner NLL too
def wnll(p,df):
    pn_=p/pd.Series(p).groupby(df.race_id.to_numpy()).transform("sum").to_numpy()
    return -np.mean(np.log(pn_[df.won.to_numpy(bool)]))
out["closing_2026"]=dict(n=len(te),races=te.race_id.nunique(),nll_base=nll(pb,yte),nll_new=nll(pn,yte),
    wnll_base=wnll(pb,te),wnll_new=wnll(pn,te),
    n_ev12_base=int((pb*te.odds>1.2).sum()),n_ev12_new=int((pn*te.odds>1.2).sum()),
    roi_ev12_base=float(((pb*te.odds>1.2)*te.won*te.odds).sum()/max((pb*te.odds>1.2).sum(),1)),
    roi_ev12_new=float(((pn*te.odds>1.2)*te.won*te.odds).sum()/max((pn*te.odds>1.2).sum(),1)),
    sum_p_base=float(pb.sum()),sum_p_new=float(pn.sum()),won=float(yte.sum()))
# (i-b) closing-odds OOS on perturbed-copy of 2026 test (simulated judged) — proxy with known truth
tep=perturb(te,np.random.default_rng(7))
pbj=pred_base(tep); pnj=pred_new(tep,np.exp(tep.log_min))
def calib(p,o,y,thr=1.2):
    m=p*o>thr; return dict(n=int(m.sum()),won=int(y[m].sum()),ratio=float(y[m].sum()/max(p[m].sum(),1e-9)))
out["simulated_judged_2026"]=dict(nll_base=nll(pbj,yte),nll_new=nll(pnj,yte),wnll_base=wnll(pbj,tep),wnll_new=wnll(pnj,tep),
    ev12_base=calib(pbj,tep.odds.to_numpy(),yte),ev12_new=calib(pnj,tep.odds.to_numpy(),yte),
    ev10_base=calib(pbj,tep.odds.to_numpy(),yte,1.0),ev10_new=calib(pnj,tep.odds.to_numpy(),yte,1.0),
    retention_base=float(((pbj*tep.odds>1.2)&(pb*te.odds>1.2)).sum()/max((pbj*tep.odds>1.2).sum(),1)),
    retention_new=float(((pnj*tep.odds>1.2)&(pn*te.odds>1.2)).sum()/max((pnj*tep.odds>1.2).sum(),1)),
    roi_at_final_ev12J_base=float(((pbj*tep.odds>1.2)*te.won*te.odds).sum()/max((pbj*tep.odds>1.2).sum(),1)),
    roi_at_final_ev12J_new=float(((pnj*tep.odds>1.2)*te.won*te.odds).sum()/max((pnj*tep.odds>1.2).sum(),1)))
# (ii) real judged pairs: scored_pairs (443 races) with feature columns merged
S=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet").sort_values(["pair_id","horse_number"]).reset_index(drop=True)
res={}
for v in ("J","F"):
    d=S.copy(); d["odds"]=d[f"O_{v}"].astype(float); d["race_id_orig"]=d.race_id; d["race_id"]=d.pair_id
    m=D.market_block(d[["race_id","horse_id","horse_number","odds"]],"race_id")
    for c in MARKET: d[c]=m[c].to_numpy()
    d["popularity"]=d["odds_rank"]; d["race_id"]=d.race_id_orig
    res[v]=dict(base=pred_base(d), new=pred_new(d, d.hours.to_numpy()*60 if v=="J" else None), odds=d.odds.to_numpy())
# parity: base at J should equal seed-1 share of EVe_J? EVe_J is 15-seed mean so only approx; check correlation
y=S.won.to_numpy(float); oJ=res["J"]["odds"]; oF=res["F"]["odds"]
def band_tab(p,o,label):
    ev=p*o; rows_=[]
    for lo,hi in ((0,0.8),(0.8,1.0),(1.0,1.2),(1.2,9),(1.0,9)):
        m=(ev>=lo)&(ev<hi); rows_.append(dict(band=f"{lo}-{hi}",n=int(m.sum()),won=int(y[m].sum()),sum_p=round(float(p[m].sum()),2),ratio=round(float(y[m].sum()/max(p[m].sum(),1e-9)),3)))
    t=pd.DataFrame(rows_); print(f"\n[{label}]"); print(t.to_string(index=False)); return rows_
out["pairs443"]={}
for k in ("base","new"):
    pJ=res["J"][k]; pF=res["F"][k]
    out["pairs443"][k]=dict(nll_J=nll(pJ,y),nll_F=nll(pF,y),calib_J=band_tab(pJ,oJ,f"{k} 判断時"),calib_F=band_tab(pF,oF,f"{k} 確定時"),
        n_ev12_J=int((pJ*oJ>1.2).sum()),n_ev12_F=int((pF*oF>1.2).sum()),
        retention=float(((pJ*oJ>1.2)&(pF*oF>1.2)).sum()/max((pJ*oJ>1.2).sum(),1)),
        med_EVF_EVJ_of_Jpicks=float(np.median((pF*oF)[pJ*oJ>1.2]/(pJ*oJ)[pJ*oJ>1.2])) if (pJ*oJ>1.2).any() else None,
        roi_J_picks_settled=float((S.settle_multiplier.to_numpy()[pJ*oJ>1.2]).sum()/max((pJ*oJ>1.2).sum(),1)),
        won_J_picks=int(y[pJ*oJ>1.2].sum()))
    print(k,"n_ev12_J",out["pairs443"][k]["n_ev12_J"],"n_ev12_F",out["pairs443"][k]["n_ev12_F"],"retention",round(out["pairs443"][k]["retention"],3),
          "medEVF/EVJ",out["pairs443"][k]["med_EVF_EVJ_of_Jpicks"],"nll_J",round(out["pairs443"][k]["nll_J"],5),"nll_F",round(out["pairs443"][k]["nll_F"],5))
# parity check base vs stored ens: corr of base p at J with EVe_J/O_J
print("corr(base seed1 p_J, ens15 p_J)=%.4f  mean ratio=%.4f"%(np.corrcoef(res["J"]["base"],S.EVe_J/S.O_J)[0,1],np.mean(res["J"]["base"]/(S.EVe_J/S.O_J))))
json.dump(out,open(SCR+f"spike_{VARIANT}_s{SEED}_results.json","w"),indent=1,default=float)
print(json.dumps({k:v for k,v in out.items() if k!="pairs443"},indent=1,default=lambda x: round(float(x),5)))
print("total %.0fs"%(time.time()-t0))
