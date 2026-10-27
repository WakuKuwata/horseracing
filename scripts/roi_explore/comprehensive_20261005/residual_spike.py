"""Exploratory race-normalized market residual spike; frozen protocol, no production writes."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import scipy
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "training/src"), str(ROOT / "eval/src")]
from horseracing_training.cond_logit import cond_logit_objective

OUT = ROOT / "artifacts/roi_comprehensive_20261005/residual_spike"
PROTO = ROOT / "docs/roi-comprehensive-review-20261005/residual-protocol.md"
ROWS = ROOT / "artifacts/roi_strict2007_20260930/input/rows.parquet"
ENSPEC = ROOT / "artifacts/market_ev/mev-ens15-v1/ensemble.spec.json"
RESULTS = ROOT / "artifacts/roi_explore/results"
DROP = {"odds", "q", "popularity", "odds_rank", "q_share_of_fav", "fav_q", "fav_odds", "second_odds",
        "odds_gap12", "q_entropy_norm", "n_fav_under_2", "n_odds_under_10"}
YEARS = (2023, 2024, 2025, 2026)
MAX_DATE = "2026-06-26"
B = 4000
PARAMS = dict(learning_rate=.05, num_leaves=63, min_data_in_leaf=1000,
              feature_fraction=.8, bagging_fraction=1., lambda_l2=10., seed=1,
              deterministic=True, force_row_wise=True, num_threads=1, verbosity=-1)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))


def segments(rids):
    return np.r_[0, np.flatnonzero(rids[1:] != rids[:-1]) + 1].astype(int)


def softmax(scores, starts):
    sizes = np.diff(np.r_[starts, len(scores)])
    e = np.exp(scores - np.repeat(np.maximum.reduceat(scores, starts), sizes))
    return e / np.repeat(np.add.reduceat(e, starts), sizes)


def objective(starts, offset):
    def fobj(pred, data):
        p = softmax(pred + offset, starts)
        return p - data.get_label(), np.maximum(p * (1-p), 1e-6)
    return fobj


def verify_objective():
    rng = np.random.default_rng(20261005)
    sizes = [3, 5, 2]
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    pred = rng.normal(size=10)
    off = rng.normal(size=10)
    y = np.zeros(10); y[[1, 7, 9]] = 1
    class Data:
        def get_label(self): return y
        def get_weight(self): return None
    g, h = objective(starts, off)(pred, Data())
    rg, rh = cond_logit_objective(sizes, off)(pred, Data())
    np.testing.assert_allclose(g, rg, atol=1e-14, rtol=1e-14)
    np.testing.assert_allclose(h, rh, atol=1e-14, rtol=1e-14)
    def loss(x): return -np.log(softmax(x+off, starts)[y == 1]).sum()
    eps = 1e-6
    fd = []
    for i in range(10):
        delta = np.zeros(10); delta[i] = eps
        fd.append((loss(pred+delta)-loss(pred-delta))/(2*eps))
    np.testing.assert_allclose(g, fd, atol=1e-8, rtol=1e-8)
    p = softmax(pred+off, starts)
    np.testing.assert_allclose(np.add.reduceat(p, starts), 1, atol=1e-14)
    np.testing.assert_allclose(p, softmax(pred+off+np.repeat([9., -2., 50.], sizes), starts), atol=1e-14)
    return dict(oracle_equivalent=True, finite_difference_gradient=True, shift_invariant=True, sum_one=True)


def design(d, num, cats, train=None, maps=None):
    X = d[num].astype(float).copy()
    if maps is None:
        maps = {c: sorted(set(d.loc[train, c].dropna().astype(str))) for c in cats}
    for c in cats:
        code = {v: k for k, v in enumerate(maps[c])}
        X[c] = d[c].astype("string").map(code).astype(float)
    return X, maps


def load():
    spec = json.loads(ENSPEC.read_text())
    num = [c for c in spec["features"] if c not in DROP and c not in spec["cats"]]
    cats = [c for c in spec["cats"] if c not in DROP]
    cols = list(dict.fromkeys(["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "q", "won",
                              "race_ok", "dead_heat"] + num + cats))
    d = pd.read_parquet(ROWS, columns=cols)
    d["race_date"] = d.race_date.astype(str).str[:10]
    d = d[(d.year >= 2007) & (d.race_date <= MAX_DATE) & d.race_ok & ~d.dead_heat].copy()
    d = d.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    assert len(DROP) == 12 and not DROP.intersection(num+cats)
    assert d.groupby("race_id").won.sum().eq(1).all()
    assert d[["race_id", "horse_id"]].duplicated().sum() == 0
    evsum = np.zeros(len(d))
    hashes = {}
    for s in range(1, 16):
        tag = ("armC_binary_drop-sameday+weightlive_from2007_ens15" if s == 1 else
               f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15")
        p = RESULTS / tag / "predictions.parquet"
        v = pd.read_parquet(p, columns=["race_id", "horse_id", "pred"])
        aligned = d[["race_id", "horse_id"]].merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
        assert aligned.loc[d.year >= 2010, "pred"].notna().all()
        evsum += (1+aligned.pred.fillna(0).to_numpy()/100)/15
        hashes[str(p.relative_to(ROOT))] = sha(p)
    d["p_ens"] = evsum/d.odds.to_numpy(float)
    d.loc[d.year < 2010, "p_ens"] = np.nan
    return d, num, cats, hashes


def counts_boot(days):
    rng = np.random.default_rng(20261005)
    return rng.multinomial(len(days), np.full(len(days), 1/len(days)), size=B).astype(float)


def gain_summary(race, col):
    point = float(race[col].mean())
    d = race.groupby("race_date")[col].agg(["sum", "size"])
    w = counts_boot(d.index)
    reps = (w@d["sum"].to_numpy())/(w@d["size"].to_numpy())
    p = (1+int(((reps-point) >= point).sum()))/(B+1)
    return dict(n_races=len(race), n_days=len(d), nll_gain=point,
                ci95=np.quantile(reps,[.025,.975]).tolist(), p_centered_one_sided=p,
                annual={str(y): float(g[col].mean()) for y,g in race.groupby("year")})


def fit_mix(Z, won, starts):
    n_races = len(starts)
    def f(b):
        p = softmax(Z@b, starts)
        val = -np.log(np.clip(p[won], 1e-15, 1)).sum()/n_races
        grad = Z.T@(p-won.astype(float))/n_races
        return val, grad
    initial = np.r_[1., np.zeros(Z.shape[1]-1)]
    fit = minimize(f, initial, jac=True, bounds=[(0,5)]*Z.shape[1], method="L-BFGS-B",
                   options=dict(maxiter=1000, ftol=1e-12, gtol=1e-8))
    if not fit.success:
        raise RuntimeError(str(fit.message))
    return fit.x, dict(converged=bool(fit.success), nll=float(fit.fun), iterations=int(fit.nit))


def roi_summary(d, selections):
    out = {}
    base = (d.p_ens*d.odds > 1.2).to_numpy(bool)
    windows = {"ALL": np.ones(len(d),bool), "2024plus": (d.year>=2024).to_numpy()}
    for label, wm in windows.items():
        ds = d.loc[wm]
        days, di = np.unique(ds.race_date.to_numpy(),return_inverse=True)
        w = counts_boot(days)
        pay = (ds.won*ds.odds).to_numpy(float)
        b = base[wm]
        all_masks = {"S3": b} | {k: m[wm] for k,m in selections.items()}
        per = {}
        for k, m in all_masks.items():
            # Mixed probabilities do not exist in the first calibration year.
            # Report them only beside the same-period S3 in 2024plus.
            if label == "ALL" and k.startswith("extended_mix_"):
                continue
            den = np.bincount(di, weights=m.astype(float),minlength=len(days))
            num = np.bincount(di, weights=m*pay,minlength=len(days))
            dr = w@den
            reps = np.divide(w@num,dr,out=np.full(B,np.nan),where=dr>0)
            roi = float(num.sum()/den.sum()) if den.sum() else None
            annual = {}
            for y in sorted(ds.year.unique()):
                ym=(ds.year==y).to_numpy() & m
                annual[str(y)] = dict(n=int(ym.sum()), hits=int(ds.won.to_numpy()[ym].sum()),
                                     roi=float(pay[ym].sum()/ym.sum()) if ym.any() else None)
            per[k] = dict(n=int(m.sum()),hits=int((m&ds.won.to_numpy()).sum()), roi=roi,
                          ci95=np.nanquantile(reps,[.025,.975]).tolist() if m.any() else None,
                          mean_parent_ens_ev=float((ds.p_ens*ds.odds).to_numpy()[m].mean()) if m.any() else None,
                          annual=annual)
            if k != "S3":
                bd = np.bincount(di, weights=b.astype(float),minlength=len(days))
                bn = np.bincount(di, weights=b*pay,minlength=len(days))
                brep = (w@bn)/(w@bd)
                diff = reps-brep
                per[k]["paired_roi_difference_vs_S3"] = None if roi is None else roi-float(bn.sum()/bd.sum())
                per[k]["paired_difference_ci95"] = np.nanquantile(diff,[.025,.975]).tolist() if m.any() else None
        out[label]=per
    return out


def main():
    started=time.monotonic(); OUT.mkdir(parents=True,exist_ok=True)
    if (OUT / "freeze.json").exists():
        raise RuntimeError("This frozen experiment has already started; do not overwrite it.")
    tests=verify_objective()
    freeze=dict(protocol_sha256=sha(PROTO),script_sha256=sha(Path(__file__)),rows_sha256=sha(ROWS),
                enspec_sha256=sha(ENSPEC),years=YEARS,settle_max=MAX_DATE,rounds=300,params=PARAMS,
                objective_checks=tests,can_adopt=False,eligible_for_verdict=False,
                runtime=dict(python=platform.python_version(),lightgbm=lgb.__version__,pandas=pd.__version__,
                             numpy=np.__version__,scipy=scipy.__version__))
    write(OUT/"freeze.json",freeze)
    d,num,cats,hashes=load()
    write(OUT/"inputs.json",dict(prediction_hashes=hashes,features=num,cats=cats,rows=len(d),
                                races=int(d.race_id.nunique()),date_min=d.race_date.min(),date_max=d.race_date.max()))
    preds=[]; recipes={}
    for year in YEARS:
        tr=(d.year<year).to_numpy(); te=(d.year==year).to_numpy()
        X,maps=design(d,num,cats,train=tr)
        train=d.loc[tr]; test=d.loc[te]
        off=np.log(train.q.to_numpy(float))
        starts=segments(train.race_id.to_numpy())
        ds=lgb.Dataset(X.loc[tr],label=train.won.to_numpy(float),categorical_feature=cats)
        params=PARAMS|{"objective":objective(starts,off)}
        model=lgb.train(params,ds,num_boost_round=300)
        apstarts=segments(test.race_id.to_numpy())
        p=softmax(np.log(test.q.to_numpy(float))+model.predict(X.loc[te],raw_score=True,num_threads=1),apstarts)
        assert np.isfinite(p).all() and np.all(p>0)
        assert float(train.year.max())<year
        np.testing.assert_allclose(np.add.reduceat(p,apstarts),1,atol=1e-12)
        pred=test[["race_id","horse_id","horse_number","race_date","year","odds","q","won","p_ens"]].copy()
        pred["p_residual"]=p
        pred.to_parquet(OUT/f"predictions_{year}.parquet",index=False)
        preds.append(pred)
        modelpath=OUT/f"model_{year}.txt"; model.save_model(modelpath)
        recipes[str(year)]=dict(train_min=train.race_date.min(),train_max=train.race_date.max(),
            train_n=len(train),test_n=len(test),cat_maps=maps,model_sha256=sha(modelpath),
            prediction_sha256=sha(OUT/f"predictions_{year}.parquet"))
        write(OUT/"recipes.json",recipes)
        print(f"year {year} complete, train {len(train)}, test {len(test)}, elapsed {time.monotonic()-started:.1f}s",flush=True)
        del X,ds,model
    ap=pd.concat(preds,ignore_index=True).sort_values(["race_id","horse_number"]).reset_index(drop=True)
    starts=segments(ap.race_id.to_numpy())
    pnorm=ap.p_ens.to_numpy()/np.repeat(np.add.reduceat(ap.p_ens.to_numpy(),starts),np.diff(np.r_[starts,len(ap)]))
    ap["p_ens_norm"]=pnorm; ap["p_base_mix"]=np.nan; ap["p_extended_mix"]=np.nan
    coefficients={}
    for year in YEARS[1:]:
        tr=(ap.year<year).to_numpy(); te=(ap.year==year).to_numpy()
        Z=np.log(ap[["q","p_ens_norm","p_residual"]].to_numpy())
        ts=segments(ap.loc[tr,"race_id"].to_numpy()); ases=segments(ap.loc[te,"race_id"].to_numpy())
        y=ap.loc[tr,"won"].to_numpy(bool)
        a,ai=fit_mix(Z[tr,:2],y,ts); b,bi=fit_mix(Z[tr],y,ts)
        ap.loc[te,"p_base_mix"]=softmax(Z[te,:2]@a,ases)
        ap.loc[te,"p_extended_mix"]=softmax(Z[te]@b,ases)
        coefficients[str(year)]=dict(base=a.tolist(),extended=b.tolist(),base_fit=ai,extended_fit=bi,
                                     calibration_min=ap.loc[tr,"race_date"].min(),calibration_max=ap.loc[tr,"race_date"].max())
    winners=ap[ap.won].copy()
    winners["gain_A"]=np.log(winners.p_residual/winners.p_ens_norm)
    wm=winners[winners.year>=2024].copy()
    wm["gain_B"]=np.log(wm.p_extended_mix/wm.p_base_mix)
    gains={"A_residual_vs_ens":gain_summary(winners,"gain_A"),"B_incremental_vs_q_ens":gain_summary(wm,"gain_B")}
    ranked=sorted(gains,key=lambda k:gains[k]["p_centered_one_sided"])
    adj=0.
    for rank,k in enumerate(ranked):
        adj=max(adj,min(1.,(2-rank)*gains[k]["p_centered_one_sided"]))
        gains[k]["holm_adjusted_p"]=adj
    sels={}
    for nm,col in (("residual","p_residual"),("extended_mix","p_extended_mix")):
        for threshold in (1.,1.2,1.4):
            sels[f"{nm}_EV_gt_{threshold}"]=(ap[col]*ap.odds>threshold).to_numpy(bool)
    summary=dict(freeze=freeze,features=num+cats,n_features=len(num+cats),coefficients=coefficients,
                 evaluation_availability=dict(residual="2023plus",extended_mix="2024plus",
                                              all_mixed_comparison="omitted; use same-period 2024plus"),
                 nll=gains,roi=roi_summary(ap,sels),elapsed_sec=time.monotonic()-started,
                 interpretation="Exploratory reused history; stored odds settlement; one seed; no adoption proof")
    ap.to_parquet(OUT/"predictions.parquet",index=False)
    winners.to_csv(OUT/"race_losses.csv",index=False)
    write(OUT/"results.json",summary)
    print(json.dumps({k:v for k,v in gains.items()},ensure_ascii=False),flush=True)
    print(f"complete {time.monotonic()-started:.1f}s",flush=True)


if __name__=="__main__":
    main()
