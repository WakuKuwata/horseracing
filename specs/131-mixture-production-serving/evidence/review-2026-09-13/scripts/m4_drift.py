"""M4: why do the 9/13 production runs (computed 9/12 14:14 UTC) differ from the prospective shadow captures (9/13 00:16 UTC)?
Rebuild the as-of features NOW for the 9/13 races and diff column-by-column against the persisted feature_snapshots."""
import os, json, numpy as np, pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.mixture_model import feature_profile
e=create_engine(os.environ['DATABASE_URL'])
cols=feature_profile()['full_columns']
with Session(e) as s:
    runs=s.execute(text("""select pr.prediction_run_id, pr.race_id from prediction_runs pr join races r using(race_id)
      where pr.model_version='mix-129-nj6' and r.race_date='2026-09-13'""")).all()
    rids=[r.race_id for r in runs]
    snaps=s.execute(text("""select pr.race_id, fs.horse_id, fs.features from feature_snapshots fs join prediction_runs pr using(prediction_run_id)
      join races r using(race_id) where pr.model_version='mix-129-nj6' and r.race_date='2026-09-13'""")).all()
    fr=build_feature_matrix(s, end_date=pd.Timestamp('2026-09-13').date(), wanted=frozenset(cols), target_race_ids=frozenset(rids))
old={(r.race_id,r.horse_id):r.features for r in snaps}
diff={}; nrows=0; changed_rows=0
new_ids=set(zip(fr.race_id,fr.horse_id)); old_ids=set(old)
print('started-set changes: only_new',len(new_ids-old_ids),'only_old',len(old_ids-new_ids))
for _,row in fr.iterrows():
    k=(row.race_id,row.horse_id)
    if k not in old: continue
    nrows+=1; ch=False
    for c in cols:
        if c in ('weight','weight_diff','carried_weight_ratio'): continue  # regime columns (now published)
        a=old[k].get(c); b=row[c]
        an=a is None or (isinstance(a,float) and np.isnan(a)); bn=pd.isna(b)
        if an and bn: continue
        try:
            same = (not an and not bn) and (abs(float(a)-float(b))<1e-9)
        except (TypeError,ValueError):
            same = (str(a)==str(b))
        if not same:
            diff.setdefault(c,[]).append((k,a,b)); ch=True
    changed_rows+=ch
print('rows',nrows,'changed rows',changed_rows)
for c,v in sorted(diff.items(), key=lambda kv:-len(kv[1])):
    print(c,len(v),v[:2])
