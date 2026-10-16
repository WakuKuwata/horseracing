"""OOF error structure of mev-ens15 (15 seed). Read-only. Single thread."""
import os
os.environ["OMP_NUM_THREADS"] = "1"; os.environ["OPENBLAS_NUM_THREADS"] = "1"; os.environ["MKL_NUM_THREADS"] = "1"
import json, pathlib, sys, time
import numpy as np, pandas as pd
from sklearn.isotonic import IsotonicRegression

REPO = pathlib.Path("/Users/kuwatawaku/workspace/horseracing")
RES = REPO / "artifacts/roi_explore/results"
OUT = pathlib.Path(__file__).resolve().parent
MAX_DATE = "2026-06-26"
rng = np.random.default_rng(20261010)
def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def seed_dir(s):
    return RES / f"armC_binary{'' if s==1 else f'_seed{s}'}_drop-sameday+weightlive_from2007_ens15" / "predictions.parquet"

# ---------- load ----------
base = pd.read_parquet(seed_dir(1))
P = np.empty((len(base), 15))
for s in range(1, 16):
    d = pd.read_parquet(seed_dir(s))
    if s > 1:
        assert (d["race_id"].values == base["race_id"].values).all() and (d["horse_id"].values == base["horse_id"].values).all()
        assert np.allclose(d["odds"].values, base["odds"].values)
    P[:, s-1] = (1.0 + d["pred"].values / 100.0) / d["odds"].values
rows = pd.read_parquet(REPO / "artifacts/market_ev/rows_2007.parquet",
                       columns=["race_id","horse_id","won","field_size","race_ok","dead_heat","q","odds","race_date"])
df = base.merge(rows[["race_id","horse_id","won","field_size","race_ok","dead_heat","q"]], on=["race_id","horse_id"], how="left", validate="one_to_one")
assert df["won"].notna().all()
df = df[df["race_ok"] & ~df["dead_heat"] & (df["race_date"] <= MAX_DATE)].copy()
P = P[df.index.values]; df = df.reset_index(drop=True)
# check one winner per race
g = df.groupby("race_id")["won"].sum()
assert (g == 1).all(), g.value_counts()
log(f"rows={len(df)} races={df.race_id.nunique()} dates={df.race_date.nunique()} years {df.year.min()}..{df.year.max()}")
pbar = P.mean(1)
pbar_c = np.clip(pbar, 1e-6, 1 - 1e-6)
df["pbar"] = pbar_c
df["ev"] = df["pbar"] * df["odds"]
# market q recomputed on same population
inv = 1.0 / df["odds"].values
df["q_rc"] = inv / df.groupby("race_id")["odds"].transform(lambda s: (1.0/s).sum()).values
log(f"q from rows vs recomputed: max abs diff {np.abs(df.q - df.q_rc).max():.3e}")
df["q"] = df["q_rc"]
df["won_f"] = df["won"].astype(float)
# race-normalized pbar
df["pn"] = df["pbar"] / df.groupby("race_id")["pbar"].transform("sum")
df["sum_p"] = df.groupby("race_id")["pbar"].transform("sum")
log(f"Σp̄ per race: mean {df.drop_duplicates('race_id').sum_p.mean():.4f} median {df.drop_duplicates('race_id').sum_p.median():.4f}")
report = {"n_rows": int(len(df)), "n_races": int(df.race_id.nunique()), "sum_p_race_mean": float(df.drop_duplicates('race_id').sum_p.mean())}

# ---------- helpers ----------
def ece_eqmass(p, y, nb=20):
    o = np.argsort(p, kind="stable"); p = p[o]; y = y[o]
    idx = np.array_split(np.arange(len(p)), nb)
    return float(sum(len(i)/len(p) * abs(y[i].mean() - p[i].mean()) for i in idx))

def winner_nll(d, col):
    w = d[d.won]
    return float(-np.log(np.clip(w[col].values, 1e-9, 1)).mean())

def boot_ratio(dates, num, den, B=1000):
    """race-day cluster bootstrap for Σnum/Σden."""
    t = pd.DataFrame({"d": dates, "n": num, "m": den}).groupby("d").sum()
    n, m = t.n.values, t.m.values
    k = len(n); W = rng.multinomial(k, np.full(k, 1/k), size=B)
    r = (W @ n) / (W @ m)
    return float(np.quantile(r, .025)), float(np.quantile(r, .975))

EV_B = [-np.inf, 0.8, 1.0, 1.2, 1.5, np.inf]; EV_L = ["<0.8","0.8-1.0","1.0-1.2","1.2-1.5",">1.5"]
OD_B = [0, 5, 10, 20, 40, 100, np.inf]; OD_L = ["<5","5-10","10-20","20-40","40-100","100+"]
df["ev_band"] = pd.cut(df["ev"], EV_B, labels=EV_L, right=False)
df["od_band"] = pd.cut(df["odds"], OD_B, labels=OD_L, right=False)

# ---------- 1. calibration tables (2013+) ----------
m = df[df.year >= 2013]
cells = []
for (eb, ob), c in m.groupby(["ev_band","od_band"], observed=True):
    n = len(c); sw = c.won_f.sum(); sp = c.pbar.sum(); sq = c.q.sum(); spn = c.pn.sum()
    lo, hi = boot_ratio(c.race_date.values, c.won_f.values, c.pbar.values) if n >= 200 else (np.nan, np.nan)
    qlo, qhi = boot_ratio(c.race_date.values, c.won_f.values, c.q.values) if n >= 200 else (np.nan, np.nan)
    roi = (c.won_f * c.odds).sum() / n
    rlo, rhi = boot_ratio(c.race_date.values, (c.won_f*c.odds).values, np.ones(n)) if n >= 200 else (np.nan, np.nan)
    cells.append(dict(ev_band=eb, odds_band=ob, n=int(n), wins=int(sw), won_over_p=sw/sp, won_over_p_ci=[lo,hi],
                      won_over_pn=sw/spn, won_over_q=sw/sq, won_over_q_ci=[qlo,qhi], roi=roi, roi_ci=[rlo,rhi],
                      mean_p=c.pbar.mean(), mean_q=c.q.mean(), mean_ev=c.ev.mean(), net_loss_yen=float(((c.won_f*c.odds)-1).sum()*100)))
cal = pd.DataFrame(cells)
cal.to_csv(OUT/"t1_calibration_cells_2013plus.csv", index=False)
# marginal by EV band and by odds band
def marg(key):
    out=[]
    for k, c in m.groupby(key, observed=True):
        n=len(c); sw=c.won_f.sum()
        lo,hi=boot_ratio(c.race_date.values,c.won_f.values,c.pbar.values)
        rlo,rhi=boot_ratio(c.race_date.values,(c.won_f*c.odds).values,np.ones(n))
        out.append(dict(band=k,n=int(n),wins=int(sw),won_over_p=sw/c.pbar.sum(),won_over_p_ci=[lo,hi],won_over_pn=sw/c.pn.sum(),
                        won_over_q=sw/c.q.sum(),roi=(c.won_f*c.odds).sum()/n,roi_ci=[rlo,rhi],net_loss_yen=float(((c.won_f*c.odds)-1).sum()*100)))
    return pd.DataFrame(out)
marg_ev = marg("ev_band"); marg_od = marg("od_band")
marg_ev.to_csv(OUT/"t1_marginal_ev_band.csv", index=False); marg_od.to_csv(OUT/"t1_marginal_odds_band.csv", index=False)
# EV>1.2 by year
yr=[]
for y, c in df[df.ev > 1.2].groupby("year"):
    n=len(c); sw=c.won_f.sum()
    yr.append(dict(year=int(y), n=int(n), wins=int(sw), won_over_p=sw/c.pbar.sum(), won_over_q=sw/c.q.sum(), roi=(c.won_f*c.odds).sum()/n, mean_odds=c.odds.mean()))
ev12_year = pd.DataFrame(yr); ev12_year.to_csv(OUT/"t1_ev12_by_year.csv", index=False)
log("T1 done")

# ---------- 2. yearly NLL / ECE ----------
rows2=[]
for y, c in df.groupby("year"):
    r = dict(year=int(y), n_races=int(c.race_id.nunique()), n_rows=int(len(c)),
             nll_raw=winner_nll(c,"pbar"), nll_norm=winner_nll(c,"pn"), nll_q=winner_nll(c,"q"))
    r["dnll_norm_minus_q"] = r["nll_norm"]-r["nll_q"]; r["dnll_raw_minus_q"]=r["nll_raw"]-r["nll_q"]
    r["ece_raw"]=ece_eqmass(c.pbar.values,c.won_f.values); r["ece_norm"]=ece_eqmass(c.pn.values,c.won_f.values); r["ece_q"]=ece_eqmass(c.q.values,c.won_f.values)
    r["sum_p_mean"]=float(c.drop_duplicates("race_id").sum_p.mean())
    rows2.append(r)
c = df[df.year>=2013]
rows2.append(dict(year="2013+", n_races=int(c.race_id.nunique()), n_rows=int(len(c)), nll_raw=winner_nll(c,"pbar"), nll_norm=winner_nll(c,"pn"), nll_q=winner_nll(c,"q"),
                  dnll_norm_minus_q=winner_nll(c,"pn")-winner_nll(c,"q"), dnll_raw_minus_q=winner_nll(c,"pbar")-winner_nll(c,"q"),
                  ece_raw=ece_eqmass(c.pbar.values,c.won_f.values), ece_norm=ece_eqmass(c.pn.values,c.won_f.values), ece_q=ece_eqmass(c.q.values,c.won_f.values),
                  sum_p_mean=float(c.drop_duplicates("race_id").sum_p.mean())))
t2 = pd.DataFrame(rows2); t2.to_csv(OUT/"t2_yearly_nll_ece.csv", index=False)
# paired race-day bootstrap CI for 2013+ ΔNLL (norm − q)
w = c[c.won].copy(); w["d"] = -np.log(w.pn) + np.log(w.q)
t = w.groupby("race_date")["d"].agg(["sum","size"]); k=len(t); W=rng.multinomial(k,np.full(k,1/k),size=2000)
dd = (W@t["sum"].values)/(W@t["size"].values)
report["dnll_norm_minus_q_2013plus"] = dict(point=float(w.d.mean()), ci=[float(np.quantile(dd,.025)), float(np.quantile(dd,.975))])
log("T2 done")

# ---------- 3. walk-forward post-hoc calibration ----------
def logit(p): p=np.clip(p,1e-6,1-1e-6); return np.log(p/(1-p))
def expit(z): return 1/(1+np.exp(-z))
def fit_gamma(d):
    """power on race-normalized p: pn^g / Σ pn^g, winner NLL golden-section."""
    rid = d.race_id.values; lp = np.log(np.clip(d.pn.values,1e-9,1)); won = d.won.values
    codes, _ = pd.factorize(rid)
    def nll(g):
        s = g*lp; mx = pd.Series(s).groupby(codes).transform("max").values
        e = np.exp(s-mx); z = pd.Series(e).groupby(codes).transform("sum").values
        return -(s[won]-mx[won]-np.log(z[won])).mean()
    a,b=0.3,3.0; gr=(np.sqrt(5)-1)/2
    c_=b-gr*(b-a); d_=a+gr*(b-a)
    for _ in range(40):
        if nll(c_)<nll(d_): b=d_
        else: a=c_
        c_=b-gr*(b-a); d_=a+gr*(b-a)
    return (a+b)/2
def apply_gamma(d,g):
    s = g*np.log(np.clip(d.pn.values,1e-9,1)); codes,_=pd.factorize(d.race_id.values)
    e=np.exp(s - pd.Series(s).groupby(codes).transform("max").values); return e/pd.Series(e).groupby(codes).transform("sum").values
def fit_band_intercepts(d):
    """per odds band b: logit p' = logit p + b_k, MLE by Newton (binary)."""
    out={}
    for k, c in d.groupby("od_band", observed=True):
        z=logit(c.pbar.values); y=c.won_f.values; b=0.0
        for _ in range(50):
            p=expit(z+b); gr_=(y-p).sum(); h=(p*(1-p)).sum()
            if h<=0: break
            step=gr_/h; b+=step
            if abs(step)<1e-10: break
        out[k]=b
    return out
def apply_band(d,bs):
    z=logit(d.pbar.values)+d.od_band.map(bs).astype(float).values; return expit(z)

cal_rows=[]; applied = {k: np.full(len(df), np.nan) for k in ["iso","gamma","band"]}
for y in range(2013, 2027):
    tr = df[(df.year>=2010)&(df.year<y)]; te_idx = np.flatnonzero(df.year.values==y); te=df.iloc[te_idx]
    iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1-1e-6).fit(tr.pbar.values, tr.won_f.values)
    applied["iso"][te_idx]=iso.predict(te.pbar.values)
    g=fit_gamma(tr); applied["gamma"][te_idx]=apply_gamma(te,g)
    bs=fit_band_intercepts(tr); applied["band"][te_idx]=apply_band(te,bs)
    cal_rows.append(dict(year=y, n_train_rows=int(len(tr)), gamma=g, band_intercepts={str(k):float(v) for k,v in bs.items()}))
    log(f"calib {y}: gamma={g:.4f} band={ {str(k):round(float(v),3) for k,v in bs.items()} }")
for k,v in applied.items(): df[f"p_{k}"]=np.clip(v,1e-6,1-1e-6)
# normalized versions for NLL comparison
for k in ["iso","band"]:
    df[f"pn_{k}"]=df[f"p_{k}"]/df.groupby("race_id")[f"p_{k}"].transform("sum")
df["pn_gamma"]=df["p_gamma"]
m=df[df.year>=2013]
def summarize(col_raw, col_norm, label):
    r=dict(method=label, nll_raw=winner_nll(m,col_raw), nll_norm=winner_nll(m,col_norm), ece_raw=ece_eqmass(m[col_raw].values,m.won_f.values),
           ece_norm=ece_eqmass(m[col_norm].values,m.won_f.values))
    for thr in [1.0,1.2,1.5]:
        sel=m[m[col_raw]*m.odds>thr]; n=len(sel); sw=sel.won_f.sum()
        r[f"ev{thr}_n"]=int(n); r[f"ev{thr}_wins"]=int(sw); r[f"ev{thr}_roi"]=float((sel.won_f*sel.odds).sum()/n) if n else np.nan
        r[f"ev{thr}_won_over_p"]=float(sw/sel[col_raw].sum()) if n else np.nan
        if n>=50:
            lo,hi=boot_ratio(sel.race_date.values,(sel.won_f*sel.odds).values,np.ones(n)); r[f"ev{thr}_roi_ci"]=[lo,hi]
        # overlap with original S3
        s3=set(zip(m[m.ev>1.2].race_id,m[m.ev>1.2].horse_id)); cur=set(zip(sel.race_id,sel.horse_id))
        r[f"ev{thr}_overlap_with_S3"]=len(s3&cur)/max(len(s3),1) if thr==1.2 else None
    return r
t3=pd.DataFrame([summarize("pbar","pn","raw p̄ (none)"), summarize("p_iso","pn_iso","isotonic(all horses)"),
                 summarize("p_gamma","pn_gamma","power γ race-normalized"), summarize("p_band","pn_band","odds-band logit intercept")])
t3.to_csv(OUT/"t3_posthoc_calibration_2013plus.csv", index=False)
# yearly EV>1.2 ROI per method
yr3=[]
for y,c in m.groupby("year"):
    r=dict(year=int(y))
    for col,lab in [("pbar","raw"),("p_iso","iso"),("p_gamma","gamma"),("p_band","band")]:
        sel=c[c[col]*c.odds>1.2]; r[f"{lab}_n"]=int(len(sel)); r[f"{lab}_roi"]=float((sel.won_f*sel.odds).sum()/len(sel)) if len(sel) else np.nan
        r[f"{lab}_nll_norm"]=winner_nll(c,{"pbar":"pn","p_iso":"pn_iso","p_gamma":"pn_gamma","p_band":"pn_band"}[col])
    yr3.append(r)
pd.DataFrame(yr3).to_csv(OUT/"t3_posthoc_by_year.csv", index=False)
report["calibrators"]=cal_rows
log("T3 done")

# ---------- 4. seed dispersion ----------
m_idx = np.flatnonzero(df.year.values>=2013); Pm=P[m_idx]; dm=df.iloc[m_idx]
sd = Pm.std(1, ddof=1); rel = sd/Pm.mean(1)
EVs = Pm*dm.odds.values[:,None]; n_over = (EVs>1.2).sum(1)
ens_over = dm.ev.values>1.2
r4 = dict(rel_sd_median=float(np.median(rel)), rel_sd_p90=float(np.quantile(rel,.9)),
          rel_sd_median_ev_gt_1_2=float(np.median(rel[ens_over])),
          n_horses_any_seed_over=int((n_over>0).sum()), n_horses_all15_over=int((n_over==15).sum()), n_ens_over=int(ens_over.sum()),
          frac_any_seed_split=float(((n_over>0)&(n_over<15)).sum()/(n_over>0).sum()),
          frac_ens_over_with_lt15_seeds=float(((n_over<15)&ens_over).sum()/ens_over.sum()),
          frac_ens_over_with_le10_seeds=float(((n_over<=10)&ens_over).sum()/ens_over.sum()),
          hist_n_seeds_over_given_ens_over={int(k):int(v) for k,v in zip(*np.unique(n_over[ens_over],return_counts=True))})
# ROI by number of seeds agreeing (among ens_over)
agree=[]
for lo_,hi_ in [(1,7),(8,10),(11,13),(14,14),(15,15)]:
    sel=(n_over>=lo_)&(n_over<=hi_)&ens_over; c=dm[sel]
    if len(c): agree.append(dict(seeds=f"{lo_}-{hi_}",n=int(len(c)),wins=int(c.won_f.sum()),roi=float((c.won_f*c.odds).sum()/len(c)),won_over_p=float(c.won_f.sum()/c.pbar.sum())))
r4["roi_by_seed_agreement"]=agree
# single-seed NLL vs ensemble NLL (race-normalized winner NLL, 2013+)
codes,_=pd.factorize(dm.race_id.values); won=dm.won.values
def nll_norm_vec(p):
    p=np.clip(p,1e-9,1); s=pd.Series(p).groupby(codes).transform("sum").values; return float(-np.log(p[won]/s[won]).mean())
def nll_raw_vec(p): return float(-np.log(np.clip(p[won],1e-9,1)).mean())
single_norm=[nll_norm_vec(Pm[:,s]) for s in range(15)]; single_raw=[nll_raw_vec(Pm[:,s]) for s in range(15)]
r4["single_seed_nll_norm_mean"]=float(np.mean(single_norm)); r4["single_seed_nll_norm_sd"]=float(np.std(single_norm,ddof=1))
r4["ens_nll_norm"]=nll_norm_vec(Pm.mean(1)); r4["ens_minus_single_norm"]=r4["ens_nll_norm"]-r4["single_seed_nll_norm_mean"]
r4["single_seed_nll_raw_mean"]=float(np.mean(single_raw)); r4["ens_nll_raw"]=nll_raw_vec(Pm.mean(1)); r4["ens_minus_single_raw"]=r4["ens_nll_raw"]-r4["single_seed_nll_raw_mean"]
# seed curve k=1,3,5,7,10,15 (normalized NLL), 20 random subsets
curve={}
for k in [1,3,5,7,10,15]:
    vals=[]
    for _ in range(20 if k<15 else 1):
        idx=rng.choice(15,k,replace=False); vals.append(nll_norm_vec(Pm[:,idx].mean(1)))
    curve[k]=float(np.mean(vals))
r4["seed_curve_nll_norm"]=curve
# single-seed calibration Σwon/Σp in EV>1.2 (own EV) vs ensemble
ss=[]
for s in range(15):
    sel=EVs[:,s]>1.2; ss.append(float(won[sel].sum()/Pm[sel,s].sum()))
r4["single_seed_won_over_p_ev12_mean"]=float(np.mean(ss)); r4["ens_won_over_p_ev12"]=float(won[ens_over].sum()/dm.pbar.values[ens_over].sum())
r4["single_seed_ev12_n_mean"]=float(np.mean([(EVs[:,s]>1.2).sum() for s in range(15)]))
report["seed"]=r4
log("T4 done")

# dump
def conv(o):
    if isinstance(o,(np.floating,)): return float(o)
    if isinstance(o,(np.integer,)): return int(o)
    raise TypeError(str(type(o)))
json.dump(report, open(OUT/"report.json","w"), indent=1, ensure_ascii=False, default=conv)
pd.set_option("display.width",250); pd.set_option("display.max_columns",40); pd.set_option("display.float_format",lambda x:f"{x:.4f}")
print("\n== T1 cells (2013+) =="); print(cal.to_string(index=False))
print("\n== T1 marginal EV band =="); print(marg_ev.to_string(index=False))
print("\n== T1 marginal odds band =="); print(marg_od.to_string(index=False))
print("\n== T1 EV>1.2 by year =="); print(ev12_year.to_string(index=False))
print("\n== T2 yearly =="); print(t2.to_string(index=False))
print("\n== T3 posthoc (2013+) =="); print(t3.T.to_string())
print("\n== T3 by year =="); print(pd.DataFrame(yr3).to_string(index=False))
print("\n== T4 seed =="); print(json.dumps(r4, indent=1, default=conv))
print("\n== report =="); print(json.dumps({k:v for k,v in report.items() if k!="calibrators"}, indent=1, default=conv))
