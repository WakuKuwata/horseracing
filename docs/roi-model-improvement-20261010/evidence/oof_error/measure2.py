"""Additions: 2D calibrators (model×market mixture, EV×odds cell multipliers), oracle reliability, seed-agreement CIs."""
import os
os.environ["OMP_NUM_THREADS"]="1"
import json, pathlib, time
import numpy as np, pandas as pd
from scipy.optimize import minimize
from sklearn.isotonic import IsotonicRegression
REPO=pathlib.Path("/Users/kuwatawaku/workspace/horseracing"); RES=REPO/"artifacts/roi_explore/results"; OUT=pathlib.Path(__file__).resolve().parent
rng=np.random.default_rng(20261010)
def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}",flush=True)
def seed_dir(s): return RES/f"armC_binary{'' if s==1 else f'_seed{s}'}_drop-sameday+weightlive_from2007_ens15"/"predictions.parquet"
base=pd.read_parquet(seed_dir(1)); P=np.empty((len(base),15))
for s in range(1,16):
    d=pd.read_parquet(seed_dir(s)); P[:,s-1]=(1+d["pred"].values/100)/d["odds"].values
rows=pd.read_parquet(REPO/"artifacts/market_ev/rows_2007.parquet",columns=["race_id","horse_id","won","race_ok","dead_heat"])
df=base.merge(rows,on=["race_id","horse_id"],how="left")
df=df[df.race_ok&~df.dead_heat&(df.race_date<="2026-06-26")].copy(); P=P[df.index.values]; df=df.reset_index(drop=True)
df["pbar"]=np.clip(P.mean(1),1e-6,1-1e-6); df["won_f"]=df.won.astype(float)
df["q"]=(1/df.odds)/df.groupby("race_id")["odds"].transform(lambda s:(1/s).sum())
df["pn"]=df.pbar/df.groupby("race_id")["pbar"].transform("sum"); df["ev"]=df.pbar*df.odds
EV_B=[-np.inf,0.8,1.0,1.2,1.5,np.inf]; EV_L=["<0.8","0.8-1.0","1.0-1.2","1.2-1.5",">1.5"]; OD_B=[0,5,10,20,40,100,np.inf]; OD_L=["<5","5-10","10-20","20-40","40-100","100+"]
df["ev_band"]=pd.cut(df.ev,EV_B,labels=EV_L,right=False); df["od_band"]=pd.cut(df.odds,OD_B,labels=OD_L,right=False)
codes_all,_=pd.factorize(df.race_id.values)
def wnll(p,mask):
    p=np.clip(p,1e-9,1); s=pd.Series(p).groupby(codes_all).transform("sum").values; w=df.won.values&mask
    return float(-np.log(p[w]/s[w]).mean())
def wnll_raw(p,mask):
    w=df.won.values&mask; return float(-np.log(np.clip(p[w],1e-9,1)).mean())
def ece(p,y,nb=20):
    o=np.argsort(p,kind="stable"); p=p[o]; y=y[o]; idx=np.array_split(np.arange(len(p)),nb)
    return float(sum(len(i)/len(p)*abs(y[i].mean()-p[i].mean()) for i in idx))
def boot(dates,num,den,B=1000):
    t=pd.DataFrame({"d":dates,"n":num,"m":den}).groupby("d").sum(); k=len(t); W=rng.multinomial(k,np.full(k,1/k),size=B); r=(W@t.n.values)/(W@t.m.values)
    return [float(np.quantile(r,.025)),float(np.quantile(r,.975))]
report={}
# ---------- (d) mixture softmax(a log q + b log pn), walk-forward ----------
lq=np.log(df.q.values); lp=np.log(np.clip(df.pn.values,1e-9,1)); won=df.won.values; years=df.year.values
def mix_nll(theta,mask):
    a,b=theta; s=a*lq+b*lp; s=np.where(mask,s,-np.inf)
    cd=codes_all[mask]; sm=s[mask]; mx=pd.Series(sm).groupby(cd).transform("max").values; e=np.exp(sm-mx); z=pd.Series(e).groupby(cd).transform("sum").values
    wm=won[mask]; return -(sm[wm]-mx[wm]-np.log(z[wm])).mean()
p_mix=np.full(len(df),np.nan); coefs=[]
for y in range(2013,2027):
    tr=(years>=2010)&(years<y); te=years==y
    r=minimize(mix_nll,x0=[1.0,0.0],args=(tr,),method="L-BFGS-B",bounds=[(0,5),(0,5)],options={"ftol":1e-12,"gtol":1e-8,"maxiter":1000})
    a,b=r.x; s=a*lq+b*lp; cd=codes_all[te]; sm=s[te]; mx=pd.Series(sm).groupby(cd).transform("max").values; e=np.exp(sm-mx); p_mix[te]=e/pd.Series(e).groupby(cd).transform("sum").values
    coefs.append(dict(year=y,a=float(a),b=float(b))); log(f"mix {y}: a={a:.3f} b={b:.3f}")
df["p_mix"]=p_mix
# ---------- (e) EV×odds cell multiplier, walk-forward (fit Σwon/Σp̄ per cell on 2010..y-1) ----------
p_cell=np.full(len(df),np.nan); cellfits=[]
for y in range(2013,2027):
    tr=df[(df.year>=2010)&(df.year<y)]; te=df.year==y
    g=tr.groupby(["ev_band","od_band"],observed=True).agg(w=("won_f","sum"),p=("pbar","sum"),n=("won_f","size"))
    g["r"]=np.where(g.n>=200,g.w/g.p,1.0)  # shrink thin cells to 1
    key=pd.MultiIndex.from_arrays([df.loc[te,"ev_band"],df.loc[te,"od_band"]])
    r=g["r"].reindex(key).fillna(1.0).values; p_cell[te.values]=np.clip(df.loc[te,"pbar"].values*r,1e-6,1-1e-6)
    cellfits.append({"year":y,"r_ev12_15":{str(k[1]):float(v) for k,v in g.loc[g.index.get_level_values(0)=="1.2-1.5","r"].items()}})
df["p_cell"]=p_cell
# ---------- (f) 2D logistic on (logit p̄, logit q) binary walk-forward ----------
def logit(p): p=np.clip(p,1e-6,1-1e-6); return np.log(p/(1-p))
from sklearn.linear_model import LogisticRegression
X=np.c_[logit(df.pbar.values),logit(df.q.values)]; p_lr=np.full(len(df),np.nan); lrco=[]
for y in range(2013,2027):
    tr=(years>=2010)&(years<y); te=years==y
    m=LogisticRegression(C=1e6,max_iter=1000).fit(X[tr],won[tr]); p_lr[te]=m.predict_proba(X[te])[:,1]; lrco.append(dict(year=y,b_p=float(m.coef_[0][0]),b_q=float(m.coef_[0][1]),c=float(m.intercept_[0])))
df["p_lr"]=np.clip(p_lr,1e-6,1-1e-6)
m13=(df.year>=2013).values; d13=df[m13]
def summ(col,label):
    p=df[col].values; r=dict(method=label,nll_norm=wnll(p,m13),nll_raw=wnll_raw(p,m13),ece=ece(p[m13],df.won_f.values[m13]))
    for thr in [1.0,1.2,1.5]:
        sel=d13[d13[col]*d13.odds>thr]; n=len(sel); sw=sel.won_f.sum()
        r[f"ev{thr}_n"]=int(n); r[f"ev{thr}_wins"]=int(sw); r[f"ev{thr}_roi"]=float((sel.won_f*sel.odds).sum()/n) if n else None
        r[f"ev{thr}_won_over_p"]=float(sw/sel[col].sum()) if n else None
        r[f"ev{thr}_roi_ci"]=boot(sel.race_date.values,(sel.won_f*sel.odds).values,np.ones(n)) if n>=50 else None
        s3=set(zip(d13[d13.ev>1.2].race_id,d13[d13.ev>1.2].horse_id)); r[f"ev{thr}_overlap_S3"]=len(s3&set(zip(sel.race_id,sel.horse_id)))/len(s3)
        # S3 picks' mean calibrated p vs their realized
        if thr==1.2:
            s3d=d13[d13.ev>1.2]; r["S3_set_won_over_pcal"]=float(s3d.won_f.sum()/s3d[col].sum()); r["S3_set_mean_evcal"]=float((s3d[col]*s3d.odds).mean())
    return r
t=pd.DataFrame([summ("pbar","raw p̄"),summ("pn","race-normalized p̄ (N1)"),summ("p_mix","softmax(a·log q+b·log p̄) WF"),summ("p_lr","logistic(logit p̄, logit q) WF"),summ("p_cell","EV×odds cell multiplier WF")])
t.to_csv(OUT/"t3b_2d_calibration_2013plus.csv",index=False); report["mix_coefs"]=coefs; report["lr_coefs"]=lrco; report["cell_r_ev12_15_by_year"]=cellfits
# year-by-year for mix and cell EV>1.2
yr=[]
for y,c in d13.groupby("year"):
    r=dict(year=int(y))
    for col,lab in [("pbar","raw"),("p_mix","mix"),("p_lr","lr"),("p_cell","cell")]:
        sel=c[c[col]*c.odds>1.2]; r[f"{lab}_n"]=int(len(sel)); r[f"{lab}_roi"]=float((sel.won_f*sel.odds).sum()/len(sel)) if len(sel) else None
    yr.append(r)
pd.DataFrame(yr).to_csv(OUT/"t3b_by_year.csv",index=False)
# ---------- oracle reliability (in-sample isotonic per year) : horse-level logloss gap ----------
def ll(p,y): p=np.clip(p,1e-9,1-1e-9); return float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean())
rel=[]
for col in ["pbar","pn","q"]:
    tot=0; cnt=0; gaps=[]
    for y,c in d13.groupby("year"):
        iso=IsotonicRegression(out_of_bounds="clip").fit(c[col].values,c.won_f.values); pc=iso.predict(c[col].values)
        gaps.append(ll(c[col].values,c.won_f.values)-ll(pc,c.won_f.values))
    rel.append(dict(col=col,horse_logloss=ll(d13[col].values,d13.won_f.values),mean_yearly_reliability_gap=float(np.mean(gaps)),max_yearly_gap=float(np.max(gaps))))
report["oracle_reliability_horse_logloss"]=rel
# oracle: in-sample per-year isotonic on pn then race-renormalize → winner NLL (upper bound of 1D calibration)
p_or=np.full(len(df),np.nan)
for y,c in d13.groupby("year"):
    iso=IsotonicRegression(out_of_bounds="clip",y_min=1e-6,y_max=1-1e-6).fit(c.pn.values,c.won_f.values); p_or[c.index.values]=iso.predict(c.pn.values)
report["oracle_insample_isotonic_pn_wnll"]=dict(before=wnll(df.pn.values,m13),after=wnll(np.nan_to_num(p_or,nan=0.5),m13))
# ---------- seed agreement CIs ----------
Pm=P[m13]; EVs=Pm*d13.odds.values[:,None]; n_over=(EVs>1.2).sum(1); ens_over=d13.ev.values>1.2
agree=[]
for lo_,hi_ in [(1,7),(8,10),(11,13),(14,15),(15,15)]:
    sel=(n_over>=lo_)&(n_over<=hi_)&ens_over; c=d13[sel]
    agree.append(dict(seeds=f"{lo_}-{hi_}",n=int(len(c)),wins=int(c.won_f.sum()),roi=float((c.won_f*c.odds).sum()/len(c)),roi_ci=boot(c.race_date.values,(c.won_f*c.odds).values,np.ones(len(c))),
                      won_over_p=float(c.won_f.sum()/c.pbar.sum()),won_over_p_ci=boot(c.race_date.values,c.won_f.values,c.pbar.values),won_over_q=float(c.won_f.sum()/c.q.sum()),
                      mean_ev=float(c.ev.mean()),mean_odds=float(c.odds.mean()),mean_rel_sd=float((Pm[sel].std(1,ddof=1)/Pm[sel].mean(1)).mean())))
report["seed_agreement"]=agree
# seed dispersion as reliability signal: within EV>1.2, split by rel sd tercile
relsd=Pm.std(1,ddof=1)/Pm.mean(1); terc=pd.qcut(relsd[ens_over],3,labels=["low","mid","high"]); c=d13[ens_over].assign(t=terc.astype(str))
report["ev12_by_relsd_tercile"]=[dict(tercile=k,n=int(len(g)),wins=int(g.won_f.sum()),roi=float((g.won_f*g.odds).sum()/len(g)),won_over_p=float(g.won_f.sum()/g.pbar.sum())) for k,g in c.groupby("t")]
# gap between ens p and market q in EV>1.2 — where does truth sit? fit w: Σwon = Σ exp(w log p + (1-w) log q)
sel=d13[ens_over]; lpp=np.log(sel.pbar.values); lqq=np.log(sel.q.values); W=sel.won_f.sum()
ws=np.linspace(0,1,101); vals=[np.exp(w*lpp+(1-w)*lqq).sum() for w in ws]; w_star=float(ws[np.argmin(np.abs(np.array(vals)-W))])
report["ev12_truth_weight_on_model_vs_market"]=dict(w_model=w_star,note="Σwon=Σ p̄^w q^(1-w) を満たす w(1=モデルが正しい,0=市場が正しい)")
json.dump(report,open(OUT/"report2.json","w"),indent=1,ensure_ascii=False)
pd.set_option("display.width",250); pd.set_option("display.max_columns",40)
print(t.T.to_string()); print(pd.DataFrame(yr).to_string(index=False)); print(json.dumps({k:v for k,v in report.items() if k not in("cell_r_ev12_15_by_year",)},indent=1,ensure_ascii=False))
print("cell r (EV1.2-1.5) by fit year:"); [print(x) for x in cellfits]
