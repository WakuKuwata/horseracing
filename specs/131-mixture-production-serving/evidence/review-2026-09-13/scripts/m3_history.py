"""M3: research history population (111 snapshot started rows) vs production history (race_horses started rows).
For 2026 target rows, compare prior_gap_log computed both ways, and characterise the population difference."""
import os, sys, json
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
sys.path.insert(0,'/Users/kuwatawaku/workspace/horseracing/scripts')
import mixture_preweight_walkforward as w
import joint_residual_stack as research
from horseracing_serving import mixture_correction as mc
from horseracing_serving.mixture_serving import history_for
e=create_engine(os.environ['DATABASE_URL'])
matrix, races, folds = w.inputs()
frame = matrix.frame
KEYS = mc.KEYS
snap = frame[KEYS].copy(); snap['race_date']=pd.to_datetime(snap.race_date)
print('snapshot rows', len(snap), 'date range', snap.race_date.min(), snap.race_date.max())
with e.connect() as c:
    db = pd.DataFrame(c.execute(text("""select rh.race_id, rh.horse_id, r.race_date, rh.entry_status, r.track_type,
        exists(select 1 from race_results rr where rr.race_id=rh.race_id and rr.horse_id=rh.horse_id) as has_result
        from race_horses rh join races r using(race_id) where r.race_date between '2007-01-01' and '2026-08-23'""")).all(),
        columns=['race_id','horse_id','race_date','entry_status','track_type','has_result'])
db['race_date']=pd.to_datetime(db.race_date)
started = db[db.entry_status=='started']
ks = set(zip(snap.race_id, snap.horse_id)); kd = set(zip(started.race_id, started.horse_id))
only_db = started[[k not in ks for k in zip(started.race_id, started.horse_id)]]
only_snap = snap[[k not in kd for k in zip(snap.race_id, snap.horse_id)]]
print('started rows: db', len(kd), 'snapshot', len(ks), 'only_db', len(only_db), 'only_snapshot', len(only_snap))
print('only_db by track_type', only_db.track_type.value_counts().to_dict())
print('only_db has_result', only_db.has_result.value_counts().to_dict())
print('only_db by year', only_db.race_date.dt.year.value_counts().sort_index().to_dict())
print('only_db races', only_db.race_id.nunique(), 'sample', only_db.race_id.unique()[:5].tolist())
# prior gap both ways for the 2026 targets
years = pd.to_datetime(frame.race_date).dt.year
target = frame.loc[years==2026, KEYS+['days_since_last','sex']].copy()
a = mc.build_correction_inputs(target, snap)
raw_hist = db[['race_id','horse_id','race_date','entry_status']].copy()
tgt = target.copy(); tgt['race_date']=pd.to_datetime(tgt.race_date)
b_hist = history_for(tgt, raw_hist)     # production helper
b = mc.build_correction_inputs(target, b_hist)
assert (a.horse_id.values==b.horse_id.values).all()
pa, pb = a.prior_gap_log.to_numpy(), b.prior_gap_log.to_numpy()
both_nan = np.isnan(pa)&np.isnan(pb); eq = both_nan | (np.isclose(pa,pb,equal_nan=False))
print('2026 target rows', len(a), 'prior_gap_log differs', int((~eq).sum()), 'snap NaN', int(np.isnan(pa).sum()), 'db NaN', int(np.isnan(pb).sum()))
d = a.loc[~eq, KEYS].assign(snap=pa[~eq], db=pb[~eq])
print(d.head(10).to_string())
# also: does the snapshot's days_since_last correspond to started-only DB history? (population of gap vs prior_gap)
gap = target.days_since_last.to_numpy(float)
# recompute days_since_last from DB started history
tg = tgt.merge(started[['horse_id','race_date']].rename(columns={'race_date':'h'}), on='horse_id', how='left')
tg = tg[tg.h < tg.race_date].groupby(['race_id','horse_id']).h.max().reset_index()
m = target.merge(tg, on=['race_id','horse_id'], how='left')
dsl_db = (pd.to_datetime(m.race_date) - m.h).dt.days.to_numpy(float)
eq2 = (np.isnan(gap)&np.isnan(dsl_db)) | np.isclose(gap, dsl_db, equal_nan=False)
print('days_since_last (snapshot feature) vs DB started history: differs', int((~eq2).sum()), 'of', len(gap))
json.dump(dict(snapshot_rows=len(snap), db_started_rows=len(kd), only_db=len(only_db), only_snapshot=len(only_snap),
               only_db_track_type=only_db.track_type.value_counts().to_dict(), only_db_has_result={str(k):int(v) for k,v in only_db.has_result.value_counts().items()},
               only_db_by_year={int(k):int(v) for k,v in only_db.race_date.dt.year.value_counts().sort_index().items()},
               n_2026_targets=len(a), prior_gap_log_differs=int((~eq).sum()), days_since_last_differs=int((~eq2).sum())),
          open('/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/f61fd026-62ef-4c84-a63c-2295fc83511d/scratchpad/m/m3_history.json','w'), indent=1)
