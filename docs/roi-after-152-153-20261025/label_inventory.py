"""Read-only label availability; no candidate learning or policy scoring."""
from pathlib import Path
import importlib.util
import hashlib
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
source=ROOT/'scripts/roi_explore/roi_improvement_142/models.py'
spec=importlib.util.spec_from_file_location('label_inventory142',source)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
path=ROOT/'artifacts/market_ev/rows_2007.parquet'
sha=m.sha
before=sha(path)
d=m.read_rows(path)
keys=['race_id','horse_id']
labels=pd.read_parquet(path,columns=keys+['race_date','finish_order','result_status'],filters=[('race_date','>=',m.LOWER),('race_date','<=',m.UPPER)])
# The full source contains an excluded structural alias race; do not repair it.
labels=labels.merge(d[keys],on=keys,how='inner',validate='many_to_one')
assert not labels.duplicated(keys).any()
labels=labels.rename(columns={'race_date':'label_date'})
d=d.merge(labels,on=keys,how='left',validate='one_to_one',indicator=True,sort=False)
assert d['_merge'].eq('both').all() and d.race_date.eq(d.label_date).all()
rows=[]
for year,g in d.groupby('year'):
    n=g.groupby('race_id').size()
    unique=[g.finish_order.eq(j).groupby(g.race_id).sum().eq(1) for j in (1,2,3)]
    stage1=unique[0]&n.ge(2)
    stage2=stage1&unique[1]&n.ge(3)
    stage3=stage2&unique[2]&n.ge(4)
    rows.append(dict(year=int(year),rows=len(g),races=len(n),valid_stage1=int(stage1.sum()),valid_stage2=int(stage2.sum()),valid_stage3=int(stage3.sum())))
assert sha(path)==before
result=dict(input_sha256=before,script_sha256=sha(Path(__file__)),parent_source_sha256=sha(source),
    labels=['finish_order','result_status'],label_only_join=True,primary_features_unchanged=True,
    by_year=rows,new_fits=0,new_policies_scored=0,
    note='Stage counts require each earlier stage unique and at least two horses remaining; incomplete/ambiguous later stages retain earlier stages only.',
    discovery_failure='Initial read used142.read_rows without label-only join; finish_order intentionally omitted by that API. Independent diagnosis confirmed before corrected read.')
out=ROOT/'artifacts/roi-after-152-153-20261025/label_inventory.json'
with out.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
print(json.dumps(result))
