"""Independent scalar reconstruction of all eleven122 historical-market feature columns."""
from __future__ import annotations
from bisect import bisect_left
from collections import defaultdict
import gc
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
import market_feature_screen as m
from horseracing_db.enums import EntryStatus


def finite(v):
    try:return math.isfinite(float(v))
    except (ValueError,TypeError):return False


def cell(v):return None if pd.isna(v) else str(v)


def dist(v):
    if not finite(v):return None
    v=float(v)
    return str(0 if v<=1400 else 1 if v<=1800 else 2 if v<=2200 else 3)


def scalar_history_features(history, targets):
    """Source tuples: day,rankpercentile,fav,top3,support,surface,distance,venue; targets day,cells,key."""
    history=sorted(history,key=lambda x:x[0]);targets=sorted(targets,key=lambda x:x[0])
    pos=0;rank_values=[];support=[];byaxis=[defaultdict(list) for _ in range(3)]
    for day,axes,key in targets:
        while pos<len(history) and history[pos][0]<day:
            row=history[pos];pos+=1
            if row[1] is not None:rank_values.append(row[1:4])
            if row[4] is not None:
                support.append(row[4])
                for i,axis in enumerate(row[5:8]):
                    if axis is not None:byaxis[i][axis].append(row[4])
        tail=rank_values[-5:];count=len(rank_values)
        if count<3:out=[math.nan]*4+[float(count)]
        else:out=[tail[-1][0],math.fsum(v[0] for v in tail)/len(tail),
                  math.fsum(v[1] for v in tail)/len(tail),math.fsum(v[2] for v in tail)/len(tail),float(count)]
        parent=math.fsum(support)/len(support) if support else math.nan
        values=[];counts=[]
        for i,axis in enumerate(axes):
            xs=byaxis[i].get(axis,[]) if axis is not None else []
            values.append((math.fsum(xs)+5*parent)/(len(xs)+5));counts.append(float(len(xs)))
        yield key,out+values+counts


def main():
    cfg,f=m.verify()
    frames=m.s.reuse.load(m.s.p.WORK/'source-frames.pkl')
    races={row.race_id:(pd.Timestamp(row.race_date).date(),(cell(row.track_type),dist(row.distance),cell(row.venue_code)))
           for row in frames.races.itertuples()}
    raw=frames.race_horses[['race_id','horse_id','entry_status','popularity','odds']]
    groups=defaultdict(list)
    for rid,hid,status,pop,odds in raw.itertuples(index=False,name=None):
        if status==EntryStatus.STARTED:groups[rid].append((hid,pop,odds))
    history=defaultdict(list);complete_pop=complete_odds=0
    # Keep original per-horse source-row order for ties on a historical date.
    primitives={}
    for rid,rows in groups.items():
        pp=[float(x[1]) for x in rows] if all(finite(x[1]) and float(x[1])>=1 for x in rows) else None
        oo=[float(x[2]) for x in rows] if all(finite(x[2]) and 1<=float(x[2])<999.9 for x in rows) else None
        if pp is not None:complete_pop+=1;ordered=sorted(pp)
        if oo is not None:complete_odds+=1;inverse_total=math.fsum(1/v for v in oo)
        for i,(hid,_,_) in enumerate(rows):
            rank=bisect_left(ordered,pp[i])+1 if pp is not None else None
            n=len(rows);u=(1-(rank-1)/(n-1) if n>1 else 1.) if rank is not None else None
            s=math.log((1/oo[i])/inverse_total*n) if oo is not None else None
            primitives[(rid,hid)]=(u,float(rank==1),float(rank<=3) if rank is not None else 0.,s)
    for rid,hid,status,pop,odds in raw.itertuples(index=False,name=None):
        if status==EntryStatus.STARTED:
            day,axes=races[rid];history[hid].append((day,*primitives[(rid,hid)],*axes))
    del raw,groups,primitives,frames;gc.collect()
    matrix,_=m.s.reuse.load(m.WORK/'matrix.pkl')
    target=matrix.frame[['race_id','horse_id',*m.ADDITIONS]]
    expected={(r[0],r[1]):list(r[2:]) for r in target.itertuples(index=False,name=None)}
    targets=defaultdict(list)
    for rid,hid in expected:
        day,axes=races[rid];targets[hid].append((day,axes,(rid,hid)))
    del matrix,target,races;gc.collect()
    compared=missing=0;errors={c:0. for c in m.ADDITIONS};counts=defaultdict(int)
    for hid,ts in targets.items():
        for key,values in scalar_history_features(history.get(hid,[]),ts):
            for col,x,y in zip(m.ADDITIONS,values,expected[key],strict=True):
                compared+=1
                if pd.isna(x) or pd.isna(y):assert pd.isna(x) and pd.isna(y),(key,col,x,y);missing+=1;continue
                assert math.isfinite(x) and math.isfinite(y),(key,col,x,y)
                error=abs(x-y);errors[col]=max(errors[col],error)
                assert error<=1e-12,(key,col,x,y,error)
            counts['horse_rows']+=1
    assert counts['horse_rows']==len(expected)==958011 and compared==len(expected)*11
    m.verify()
    result={'artifact_kind':'market_feature_scalar_reconstruction','status':'PASS','can_adopt':False,'eligible_for_verdict':False,
        'additional_fits':0,'method_sha256':m.s.p.digest(__file__),'run_freeze_sha256':m.s.p.digest(m.WORK/'run-freeze.json'),
        'matrix_sha256':f['matrix_sha256'],'source_frames_sha256':m.s.p.digest(m.s.p.WORK/'source-frames.pkl'),
        'horse_rows':len(expected),'feature_cells':compared,'missing_cells':missing,'max_error_by_column':errors,
        'complete_popularity_source_races':complete_pop,'complete_odds_source_races':complete_odds,
        'strict_prior_dates':True,'same_day_excluded':True,'target_market_unused':True,
        'notes':['Independent scalar complete-field competition rank and reciprocal-odds support; strict-prior same-ID histories, lambda5 cell/parent smoothing.',
                 'No originalbuilder/merge_asof/rolling/expanding helper was used for reconstruction. Historical same-day source ties keep source row order as registered.']}
    path=m.SPEC/'evidence/independent-feature-review.json'
    if path.exists():assert m.s.read_json(path)==result
    else:m.write_json(path,result)
    print('122 FEATURE RECONSTRUCTION PASS',compared,max(errors.values()),flush=True)


if __name__=='__main__':main()
