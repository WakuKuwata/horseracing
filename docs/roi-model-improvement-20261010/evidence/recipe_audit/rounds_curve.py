"""Iteration-count curve of the ens15 recipe (seed 1, 8 threads): predictions saved at several num_iteration."""
import json, pathlib, time
import numpy as np, pandas as pd, lightgbm as lgb
OUT=pathlib.Path('/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/recipe_audit')
ROOT=pathlib.Path('/Users/kuwatawaku/workspace/horseracing')
spec=json.loads((ROOT/'artifacts/market_ev/mev-ens15-v1/seed_01/model.spec.json').read_text())
d=pd.read_parquet(ROOT/'artifacts/market_ev/rows_2007.parquet')
d=d[d.race_ok & ~d.dead_heat].reset_index(drop=True)
feats,cats=spec['features'],spec['cats']
x=d[feats].astype(float).copy()
for c in cats:
    codes,vals=pd.factorize(d[c].astype(object).where(d[c].notna(),None))
    x[c]=np.where(codes<0,np.nan,codes).astype(float)
X=x.to_numpy(dtype=np.float32); y=d.won.to_numpy(dtype=float); years=d.year.to_numpy(); odds=d.odds.to_numpy(dtype=float)
cat_idx=list(range(len(feats),len(feats)+len(cats)))
ITERS=[50,100,150,200,250,300,400]
params=dict(objective='binary',learning_rate=.05,num_leaves=63,min_data_in_leaf=1000,feature_fraction=.8,bagging_fraction=.8,bagging_freq=1,lambda_l2=10.,verbose=-1,num_threads=8,seed=1,max_bin=255)
pred={k:np.full(len(d),np.nan) for k in ITERS}
t0=time.time()
for yv in range(2010,2027):
    tr=(years>=2007)&(years<yv); te=years==yv
    ds=lgb.Dataset(X[tr],y[tr],categorical_feature=cat_idx,free_raw_data=True)
    bst=lgb.train(params,ds,num_boost_round=max(ITERS))
    for k in ITERS: pred[k][te]=bst.predict(X[te],num_iteration=k)
    print(f'year {yv} train={tr.sum():,} test={te.sum():,} {time.time()-t0:.0f}s',flush=True)
ok=~np.isnan(pred[ITERS[0]])
out=pd.DataFrame({'race_id':d.race_id[ok].values,'horse_id':d.horse_id[ok].values,'year':years[ok],'odds':odds[ok],'won':y[ok]})
for k in ITERS: out[f'p_{k}']=pred[k][ok]
out.to_parquet(OUT/'rounds_curve_preds.parquet',index=False)
print('saved',len(out))
