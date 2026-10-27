"""Research-only market-model replay with an attested 2007+ annual refit.

Freeze policy definitions and snapshot existing variants before scoring. Writes only
under a new output directory; no database or production model mutation.
"""
from __future__ import annotations

import os
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

import recheck_models_20260930 as common
import evaluate as ev

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "artifacts/roi_strict2007_20260930/market_models"
RAW = ROOT / "artifacts/market_ev/rows_2007.parquet"
SPEC = ROOT / "artifacts/market_ev/mev-binary-v2/model.spec.json"
TAGS = {
    "seed1_saved": "armC_binary_drop-sameday+weightlive_serving_v2_2007",
    "seed2_saved": "armC_binary_seed2_drop-sameday+weightlive_from2007",
    "seed3_saved": "armC_binary_seed3_drop-sameday+weightlive_from2007",
    "rounds600_saved": "armC_binary_drop-sameday+weightlive_from2007_r600",
}
WINDOWS = {"D": (2010, 2015), "Q": (2016, 2018), "C": (2019, 2026),
           "ALL": (2010, 2026), "recent": (2025, 2026), "later": (2023, 2026)}
PARAMS = dict(objective="binary", learning_rate=.05, num_leaves=63, min_data_in_leaf=1000,
              feature_fraction=.8, bagging_fraction=.8, bagging_freq=1, lambda_l2=10.,
              verbosity=-1, num_threads=1, seed=1, max_bin=255)


def save(path: Path, obj) -> None:
    with path.open("x") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=ev._default)
        f.write("\n")


def eligible_train(years: np.ndarray, year: int) -> np.ndarray:
    return (years >= 2007) & (years < year)


def passes(stats: dict) -> bool:
    return bool(stats.get("n_hits", 0) >= 20 and stats.get("n_days", 0) >= 100
                and stats.get("largest_hit_share", 1.) <= .5 and stats.get("roi", 0) >= 1.)


def freeze(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if (out / "freeze.json").exists():
        raise RuntimeError("Run already frozen; use refit/score or a new --out")
    snap = out / "snapshots"
    snap.mkdir(exist_ok=True)
    sources = {"raw": RAW, "spec": SPEC}
    for name, tag in TAGS.items():
        sources[name] = ROOT / "artifacts/roi_explore/results" / tag / "predictions.parquet"
    hashes = {}
    for name, source in sources.items():
        dest = snap / (name + source.suffix)
        shutil.copyfile(source, dest)
        hashes[name] = {"source": str(source), "snapshot": dest.name, "sha256": common.sha(dest)}
        assert common.sha(source) == hashes[name]["sha256"]
    save(out / "freeze.json", dict(created_utc=datetime.now(timezone.utc).isoformat(),
          input=hashes, source_sha256=common.sha(Path(__file__)),
          dependency_sha256={str(Path(m.__file__)):common.sha(Path(m.__file__)) for m in (common, ev)},
          models=["seed1_refit", "seed2_saved", "seed3_saved", "rounds600_saved", "avg3"],
          diagnostic_model="seed1_saved: parity only; not an extra policy family",
          policies=common.policies(), windows=WINDOWS, n_comparisons=240,
          train_lower_bound="2007-01-01", outcome_date_upper_bound="2026-09-22",
          params=PARAMS, rounds=300, annual_test_years=list(range(2010, 2027)),
          training_rule="2007 <= train_year < target_year; only target-year rows are predicted",
          selection="D and Q each: ROI>=1, hits>=20, betting days>=100, largest-hit payout share<=0.5",
          confirmation="Exploratory day-cluster percentile CI and additive null-centered bootstrap; Holm one-sided alpha=.025 across D/Q survivors",
          bootstrap_repetitions=4000, seed=20260930, untouched_holdout=False,
          note="All model and policy families fixed before this run. The periods were used in prior research. Saved variant provenance is weaker than the newly attested refit."))
    print("frozen 5 models x 48 policies; input snapshots copied", flush=True)


def read_frozen(out: Path) -> dict:
    f = json.loads((out / "freeze.json").read_text())
    assert common.sha(Path(__file__)) == f["source_sha256"]
    for rec in f["input"].values():
        assert common.sha(out / "snapshots" / rec["snapshot"]) == rec["sha256"]
    for path, digest in f["dependency_sha256"].items():
        assert common.sha(Path(path)) == digest
    return f


def refit(out: Path) -> None:
    f = read_frozen(out)
    if (out / "refit_predictions.parquet").exists():
        raise RuntimeError("Refit already complete")
    d = pd.read_parquet(out / "snapshots/raw.parquet")
    assert d.race_date.min() >= "2007-01-01" and d.race_date.max() <= "2026-09-22"
    assert not d.duplicated(["race_id", "horse_id"]).any()
    d = d.loc[d.race_ok & ~d.dead_heat].reset_index(drop=True)
    spec = json.loads((out / "snapshots/spec.json").read_text())
    features, cats = spec["features"], spec["cats"]
    assert not set(features + cats) & {"won", "finish_order", "result_status", "last_3f", "margin_sec", "running_style"}
    x = d[features].astype(float).copy()
    maps = {}
    for c in cats:
        codes, vals = pd.factorize(d[c].astype(object).where(d[c].notna(), None))
        maps[c] = {str(v): int(i) for i, v in enumerate(vals)}
        x[c] = np.where(codes < 0, np.nan, codes).astype(float)
    # A label-free category vocabulary is encoded exactly as the existing research recipe.
    assert maps == spec["cat_maps"]
    x = x.to_numpy(dtype=np.float32)
    years = d.year.to_numpy(); y = d.won.to_numpy(dtype=float)
    odds = d.odds.to_numpy(dtype=float)
    pred = np.full(len(d), np.nan)
    cat_idx = list(range(len(features), len(features) + len(cats)))
    receipts = []; start = time.monotonic()
    models = out / "refit_boosters"; models.mkdir(exist_ok=True)
    for year in range(2010, 2027):
        tr = eligible_train(years, year); te = years == year
        assert tr.sum() >= 50000 and te.any()
        train = d.loc[tr]; target = d.loc[te]
        assert train.race_date.max() < target.race_date.min()
        ds = lgb.Dataset(x[tr], y[tr], categorical_feature=cat_idx, free_raw_data=True)
        bst = lgb.train(PARAMS, ds, num_boost_round=300)
        pred[te] = 100.0 * (bst.predict(x[te], num_threads=1) * odds[te] - 1.)
        path = models / f"model_{year}.txt"; bst.save_model(str(path))
        receipts.append(dict(year=year, train_rows=int(tr.sum()), test_rows=int(te.sum()),
                             train_min=str(train.race_date.min()), train_max=str(train.race_date.max()),
                             test_min=str(target.race_date.min()), test_max=str(target.race_date.max()),
                             booster_sha256=common.sha(path)))
        print(f"refit year={year} train={tr.sum():,} test={te.sum():,} elapsed={time.monotonic()-start:.1f}s", flush=True)
    d["pred"] = pred; d["ret"] = y * odds * 100. - 100.
    answer = d.loc[np.isfinite(pred), ["race_id", "horse_id", "race_date", "year", "odds", "ret", "pred"]]
    answer.to_parquet(out / "refit_predictions.parquet", index=False)
    previous = pd.read_parquet(out / "snapshots/seed1_saved.parquet")
    joined = answer.merge(previous, on=["race_id", "horse_id"], suffixes=("", "_saved"), validate="one_to_one")
    assert len(joined) == len(answer) == len(previous)
    diff = np.abs(joined.pred - joined.pred_saved)
    save(out / "refit_receipt.json", dict(folds=receipts, rows=len(answer), prediction_sha256=common.sha(out / "refit_predictions.parquet"),
         max_abs_prediction_difference_vs_saved=float(diff.max()), rows_difference_gt_1e_8=int((diff > 1e-8).sum()),
         elapsed_s=time.monotonic()-start, lightgbm=lgb.__version__, train_data_sha256=f["input"]["raw"]["sha256"],
         category_vocabulary="Label-free vocabulary from full 2007+ dataset, same as frozen existing recipe; no target encoding",
         model_selection_changed=False))


def evidence(bets: pd.DataFrame, b: int = 4000) -> dict:
    ci = common.bootstrap(bets, b)
    if bets.empty:
        return dict(ci95=ci, p_one_sided=1.)
    day = bets.assign(pay=bets.ret + 100.).groupby("race_date").agg(n=("ret", "size"), pay=("pay", "sum"))
    s=day.n.to_numpy()*100.; p=day.pay.to_numpy(); n=len(day); estimate=p.sum()/s.sum()
    rng=np.random.default_rng(20260930); count=0
    for a in range(0,b,200):
        ix=rng.integers(0,n,size=(min(200,b-a),n))
        rb=p[ix].sum(axis=1)/s[ix].sum(axis=1)
        count+=int(np.sum(rb-estimate >= estimate-1.))
    return dict(ci95=ci, p_one_sided=(1+count)/(b+1), test="additive centered day bootstrap, diagnostic on reused years")


def score(out: Path) -> None:
    freeze_ = read_frozen(out)
    if (out / "summary.json").exists():
        raise RuntimeError("Completed run already exists")
    base = pd.read_parquet(out / "refit_predictions.parquet").sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    assert common.sha(out / "refit_predictions.parquet") == json.loads((out / "refit_receipt.json").read_text())["prediction_sha256"]
    preds = {"seed1_refit": base.pred.to_numpy()}
    for name in ("seed2_saved", "seed3_saved", "rounds600_saved"):
        other=pd.read_parquet(out / "snapshots" / f"{name}.parquet").sort_values(["race_id", "horse_id"]).reset_index(drop=True)
        assert other[["race_id", "horse_id", "race_date", "year", "odds", "ret"]].equals(base[["race_id", "horse_id", "race_date", "year", "odds", "ret"]])
        preds[name]=other.pred.to_numpy()
    preds["avg3"]=(preds["seed1_refit"]+preds["seed2_saved"]+preds["seed3_saved"])/3.
    pats=freeze_["policies"]; records=[]; frozen_survivors=[]
    # Only discovery and qualification are scored before freezing the survivor list.
    for name, prediction in preds.items():
        d=base.copy(); d["pred"]=prediction
        for policy in pats:
            mask=common.select(d[["race_id", "horse_id", "odds", "pred"]],policy)
            r=dict(model=name,**policy,periods={})
            for w in ("D", "Q"):
                window=d.year.between(*WINDOWS[w]); selected=d.loc[mask & window]
                r["periods"][w]=common.metrics(selected,d.loc[window,"race_id"].nunique())
            r["screen_pass"]=all(passes(r["periods"][w]) for w in ("D","Q"))
            records.append(r)
            if r["screen_pass"]:
                frozen_survivors.append([name,policy["policy_id"]])
    save(out / "survivors_before_C.json", dict(created_utc=datetime.now(timezone.utc).isoformat(),
         survivors=frozen_survivors, count=len(frozen_survivors), independent_holdout=False))
    save(out / "discovery_qualification.json", records)
    pvals={}; flat=[]
    for r in records:
        d=base.copy(); d["pred"]=preds[r["model"]]
        mask=common.select(d[["race_id", "horse_id", "odds", "pred"]],r)
        for w in ("C","ALL","recent","later"):
            window=d.year.between(*WINDOWS[w]); selected=d.loc[mask & window]
            stats=common.metrics(selected,d.loc[window,"race_id"].nunique())
            if r["primary"] or (r["screen_pass"] and w == "C"):
                stats.update(evidence(selected))
            r["periods"][w]=stats
        if r["screen_pass"]:
            pvals[r["model"]+"/"+r["policy_id"]]=r["periods"]["C"]["p_one_sided"]
        r["yearly"]={str(y):common.metrics(g,d.loc[d.year==y,"race_id"].nunique()) for y,g in d.loc[mask].groupby("year")}
        for w,stats in r["periods"].items():
            flat.append(dict(model=r["model"],policy_id=r["policy_id"],period=w,screen_pass=r["screen_pass"],
                             **{k:v for k,v in stats.items() if k not in ("ci95","test")}))
        if r["primary"]:
            d.loc[mask].to_parquet(out / f"bets_{r['model']}_{r['policy_id']}.parquet",index=False)
    holm=ev.holm(pvals,alpha=.025)
    for r in records:
        r["diagnostic_holm_reject"]=holm.get(r["model"]+"/"+r["policy_id"],False)
    save(out / "scores.json",records)
    summary=dict(comparisons=len(records),n_survivors=len(frozen_survivors),
                 C_point_ge1=sum(r["screen_pass"] and r["periods"]["C"]["roi"]>=1 for r in records),
                 C_ci_lo_gt1=sum(r["screen_pass"] and r["periods"]["C"]["ci95"]["lo"]>1 for r in records),
                 n_diagnostic_holm=sum(holm.values()),primary=[r for r in records if r["primary"]],
                 can_adopt=False,untouched_holdout=False,source_date_min="2007-01-06",test_date_min=str(base.race_date.min()),
                 test_date_max=str(base.race_date.max()),rows=len(base),races=base.race_id.nunique(),
                 note="All training/history inputs 2007+. Baseline refit is attested; saved variants use documented existing recipe. Stored odds settlement remains approximate.")
    save(out / "summary.json",summary)
    pd.DataFrame(flat).to_csv(out / "scores.csv",index=False)
    print(json.dumps({k:summary[k] for k in ("comparisons","n_survivors","C_point_ge1","C_ci_lo_gt1","n_diagnostic_holm")}),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=("freeze","refit","score"))
    parser.add_argument("--out",type=Path,default=DEFAULT_OUT)
    a=parser.parse_args()
    {"freeze":freeze,"refit":refit,"score":score}[a.mode](a.out)


if __name__=="__main__":
    main()
