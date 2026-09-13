"""M3b: per-race history_for (as production calls it) vs research snapshot history, 2026 target rows."""
import os, sys, json
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
sys.path.insert(0,'/Users/kuwatawaku/workspace/horseracing/scripts')
import mixture_preweight_walkforward as w
from horseracing_serving import mixture_correction as mc
from horseracing_serving.mixture_serving import history_for
e=create_engine(os.environ['DATABASE_URL'])
matrix, races, folds = w.inputs()
frame = matrix.frame; KEYS = mc.KEYS
snap = frame[KEYS].copy()
with e.connect() as c:
    db = pd.DataFrame(c.execute(text("""select rh.race_id, rh.horse_id, r.race_date, rh.entry_status from race_horses rh join races r using(race_id)
        where r.race_date between '2007-01-01' and '2026-08-23'""")).all(), columns=['race_id','horse_id','race_date','entry_status'])
db['race_date']=pd.to_datetime(db.race_date)
years = pd.to_datetime(frame.race_date).dt.year
target = frame.loc[years==2026, KEYS+['days_since_last','sex']].copy()
a = mc.build_correction_inputs(target, snap).set_index(['race_id','horse_id'])
grp = db.groupby('horse_id')
n_diff=0; n=0; rows=[]
for rid, t in target.groupby('race_id', sort=False):
    tt = t.copy(); tt['race_date']=pd.to_datetime(tt.race_date)
    raw = pd.concat([grp.get_group(h) for h in tt.horse_id if h in grp.indices], ignore_index=True) if any(h in grp.indices for h in tt.horse_id) else db.iloc[0:0]
    h = history_for(tt, raw)
    b = mc.build_correction_inputs(t, h).set_index(['race_id','horse_id'])
    pa = a.loc[b.index, 'prior_gap_log'].to_numpy(); pb = b.prior_gap_log.to_numpy()
    eq = (np.isnan(pa)&np.isnan(pb)) | np.isclose(pa,pb,equal_nan=False)
    n += len(eq); n_diff += int((~eq).sum())
    if (~eq).any(): rows.append((rid, int((~eq).sum())))
print('2026 rows', n, 'prior_gap_log differs (per-race history_for vs snapshot):', n_diff, rows[:5])
json.dump(dict(n_rows=n, prior_gap_log_differs=n_diff), open('/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/f61fd026-62ef-4c84-a63c-2295fc83511d/scratchpad/m/m3b_history.json','w'))
