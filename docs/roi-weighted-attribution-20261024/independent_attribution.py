"""Read-only descriptive decomposition of frozen143 saved bets; no models/tuning."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
p=ROOT/'artifacts/143-existing-feature-roi/weighted_comparison'
b=pd.read_parquet(p/'baseline_bets.parquet');w=pd.read_parquet(p/'weighted_baseline_bets.parquet')
k=['race_id','horse_id']
x=b.merge(w,on=k,how='outer',suffixes=('_b','_w'),validate='one_to_one',indicator=True)
x['kind']=x['_merge'].astype(str).map({'left_only':'dropped','right_only':'added','both':'common'})
x['race_date']=pd.to_datetime(x['race_date_b'].fillna(x['race_date_w']));x['year']=x.race_date.dt.year;x['month']=x.race_date.dt.month
x['odds']=x.odds_b.fillna(x.odds_w)
for col in ['stake_yen','payout_yen']:
 for arm in ['b','w']: x[f'{col}_{arm}']=x[f'{col}_{arm}'].fillna(0)
x['stake_delta']=x.stake_yen_w-x.stake_yen_b;x['payout_delta']=x.payout_yen_w-x.payout_yen_b;x['profit_delta']=x.payout_delta-x.stake_delta
x['odds_band']=pd.cut(x.odds,[0,10,20,40,100,np.inf],right=False).astype(str)
cols=['stake_yen_b','stake_yen_w','payout_yen_b','payout_yen_w','stake_delta','payout_delta','profit_delta']
def tab(d,by):
 out=d.groupby(by,observed=True)[cols].sum();out['rows']=d.groupby(by,observed=True).size();return out.reset_index().to_dict('records')
r=b[['race_date']].copy()
rb=pd.read_parquet(p/'baseline_races.parquet');rw=pd.read_parquet(p/'weighted_baseline_races.parquet')
r=rb.merge(rw,on=['race_id','race_date'],suffixes=('_b','_w'),validate='one_to_one');r.race_date=pd.to_datetime(r.race_date);r['year']=r.race_date.dt.year;r['month']=r.race_date.dt.month;r['profit_delta']=r.profit_yen_w-r.profit_yen_b;r['stake_delta']=r.stake_yen_w-r.stake_yen_b;r['payout_delta']=r.payout_yen_w-r.payout_yen_b
z=x[x.year==2026];rz=r[r.year==2026]
weights=[]
for year in range(2019,2027):
 rec=json.loads((ROOT/f'artifacts/143-existing-feature-roi/weighted_models/seed_01/year_{year}/weighted_baseline/receipt.json').read_text())['recipe'];q=rec['weights'];weights.append(dict(year=year,train_rows=rec['train_rows'],informative_rows=q['informative_rows'],informative_share=q['informative_rows']/rec['train_rows'],neutral_rows=q['neutral_rows'],minimum=q['minimum'],maximum=q['maximum'],raw_mean=q.get('raw_mean')))
top=rz.sort_values('profit_delta',ascending=False)
out=dict(note='Descriptive posthoc only. No fits or thresholds searched. Saved seed-mean ensemble. Entire result window reused; no causal/adoption verdict.',hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob('*_bets.parquet')},weights=weights,all_years=tab(x,['year','kind']),year2026=tab(z,['kind']),month2026=tab(z,['month','kind']),odds2026=tab(z,['odds_band','kind']),top2026_races=top.head(12).assign(race_date=lambda d:d.race_date.astype(str)).to_dict('records'),top_positive_removed={str(n):float(rz.profit_delta.sum()-top.head(n).profit_delta.sum()) for n in [0,1,3,5,10]},year2026_changed_races=int((rz.profit_delta!=0).sum()),year2026_positive_races=int((rz.profit_delta>0).sum()),year2026_negative_races=int((rz.profit_delta<0).sum()),year2026_positive_days=int((rz.groupby('race_date').profit_delta.sum()>0).sum()),year2026_negative_days=int((rz.groupby('race_date').profit_delta.sum()<0).sum()),calendar_through_june21=tab(x[(x.month<6)|((x.month==6)&(x.race_date.dt.day<=21))],['year']))
q=ROOT/'artifacts/roi-weighted-attribution-20261024';q.mkdir(exist_ok=True,parents=True);(q/'independent_attribution.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str)+'\n')
for name in ['weights','year2026','top_positive_removed','calendar_through_june21','top2026_races','month2026','odds2026']:
 print(name);print(json.dumps(out[name],ensure_ascii=False,indent=2,default=str))
