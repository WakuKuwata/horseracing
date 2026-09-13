"""M1: from-scratch reimplementation of the production WIN from the persisted inputs.
Uses ONLY: bundle.json coefficients, member boosters (lightgbm), member calibrator/preprocessor pickles
(TE encoders), the persisted feature_snapshots rows, and SQL for the prior-gap history.
Does NOT import mixture_model / mixture_correction / predictor."""
import os, sys, json, pickle, math
import numpy as np, pandas as pd, lightgbm as lgb
from sqlalchemy import create_engine, text
from horseracing_training.target_encoding import apply_encoded_columns  # shared TE application only

ROOT='/Users/kuwatawaku/workspace/horseracing'
BDIR=f'{ROOT}/artifacts/model_versions/mix-129-nj6'
bundle=json.load(open(f'{BDIR}/bundle.json'))
e=create_engine(os.environ['DATABASE_URL'])
date_from, date_to, regime_filter = sys.argv[1], sys.argv[2], sys.argv[3]  # e.g. 2026-09-13 2026-09-13 serving
with e.connect() as c:
    runs=c.execute(text("""select pr.prediction_run_id, pr.race_id, r.race_date, pr.logic_version from prediction_runs pr join races r using(race_id)
      where pr.model_version='mix-129-nj6' and r.race_date between :a and :b and pr.logic_version like :w order by pr.race_id"""),
      dict(a=date_from,b=date_to,w=f'%wregime={regime_filter}%')).all()
    snaps=c.execute(text("""select fs.prediction_run_id, fs.horse_id, fs.features from feature_snapshots fs join prediction_runs pr using(prediction_run_id)
      join races r using(race_id) where pr.model_version='mix-129-nj6' and r.race_date between :a and :b"""),dict(a=date_from,b=date_to)).all()
    horses={s.horse_id for s in snaps}
    hist=c.execute(text("""select rh.horse_id, r.race_date from race_horses rh join races r using(race_id)
      where rh.entry_status='started' and r.race_date>='2007-01-01' and rh.horse_id = any(:h)"""),dict(h=list(horses))).all()
hist_by={}
for h,d in hist: hist_by.setdefault(h,set()).add(pd.Timestamp(d))
by_run={}
for s in snaps: by_run.setdefault(s.prediction_run_id,{})[s.horse_id]=s.features
members=[]
for m in bundle['members']:
    d=f"{BDIR}/{m['artifact_dir']}"
    booster=lgb.Booster(model_file=f'{d}/model.txt')
    prep=pickle.load(open(f'{d}/preprocessor.pkl','rb')); calib=pickle.load(open(f'{d}/calibrator.pkl','rb'))
    meta=json.load(open(f'{d}/metadata.json'))
    members.append(dict(id=m['id'], booster=booster, prep=prep, calib=calib, cols=prep['feature_cols'], cat=prep['categorical_cols'],
                        terms=m['correction']['terms'], beta=np.array(m['correction']['coefficients'],dtype=float)))
worst=0.0; n=0; per_race=[]
for run in runs:
    feats=by_run[run.prediction_run_id]
    ids=sorted(feats)                       # production sorts horse_id
    rows=pd.DataFrame([feats[h] for h in ids]); rows['horse_id']=ids
    day=pd.Timestamp(run.race_date)
    # --- correction terms by hand ---
    gap=pd.to_numeric(rows['days_since_last'],errors='coerce').to_numpy(float)
    prior=np.full(len(ids),np.nan)
    for i,h in enumerate(ids):
        ds=sorted(x for x in hist_by.get(h,()) if x<day)
        if len(ds)>=2: prior[i]=(ds[-1]-ds[-2]).days
    doy=day.dayofyear-1; ylen=366. if day.is_leap_year else 365.
    theta=2*math.pi*doy/ylen
    sex=rows['sex']; female=np.where(sex.isna(),np.nan,(sex=='牝').to_numpy(float))
    H=dict(gap_log=np.log1p(gap), prior_gap_log=np.log1p(prior), female_sin=female*math.sin(theta), female_cos=female*math.cos(theta))
    qs=[]
    for m in members:
        X=rows.copy()
        for c in m['cat']: X[c]=X[c].astype('category')
        for c in m['cols']:
            if c not in m['cat'] and c not in m['prep']['encoders']: X[c]=pd.to_numeric(X[c],errors='coerce')
        enc={c:en.transform(X[c]) for c,en in m['prep']['encoders'].items()}
        Xm=apply_encoded_columns(X[m['cols']].copy(),enc,m['cols'])
        raw=np.asarray(m['booster'].predict(Xm),dtype=float)
        z=raw-raw.max(); sm=np.exp(z)/np.exp(z).sum()             # race softmax by hand
        cal=np.asarray(m['calib'].transform(sm),dtype=float)
        cal=np.clip(cal,1e-6,1-1e-6); p=cal/cal.sum()              # base_eps 1e-6 + renormalize
        cols=[np.nan_to_num(H[t],nan=0.) for t in m['terms'] if t!='centered_logp']
        if 'centered_logp' in m['terms']:
            lp=np.log(p); cols.append(lp-lp.mean())
        zc=np.column_stack(cols)@m['beta']
        q=p*np.exp(zc-zc.max()); q=q/q.sum()
        qs.append(q)
    win=np.mean(qs,axis=0)
    raw_win=np.array([float(feats[h]['_raw_win']) for h in ids])
    d=np.abs(win-raw_win).max(); worst=max(worst,d); n+=1
    per_race.append((run.race_id,float(d),int(np.isnan(prior).sum()),len(ids)))
print(json.dumps(dict(n_runs=n,regime=regime_filter,max_abs_diff_vs_persisted_raw_win=worst,
    races=[r for r in per_race if r[1]>1e-10][:10], n_horses=sum(r[3] for r in per_race)),indent=1))
