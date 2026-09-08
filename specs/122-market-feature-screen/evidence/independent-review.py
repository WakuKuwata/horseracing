"""Independent122 audit of saved native/fresh predictions; no model or calibrator fitting."""
from __future__ import annotations
from collections import defaultdict
import datetime as dt
import gc
import math
from pathlib import Path
import statistics
import sys

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
import market_feature_screen as m

CHECKS=0
MAX_ERROR=0.

def close(a,b):
    global CHECKS,MAX_ERROR
    CHECKS+=1
    assert math.isfinite(float(a)) and math.isfinite(float(b))
    error=abs(a-b);MAX_ERROR=max(MAX_ERROR,error)
    assert error<=1e-12,(a,b,error)


def loss(p,y=1):
    assert math.isfinite(p) and -1e-10<=p<=1+1e-10
    p=min(max(p,1e-15),1-1e-15)
    return -math.log(p if y else 1-p)


def ece(pairs):
    pairs=sorted(pairs,key=lambda v:v[0]);n=len(pairs)
    if not n:return 0.
    start=0;terms=[]
    for j in range(1,11):
        stop=int(n*j/10)
        if stop<=start:continue
        while stop<n and pairs[stop][0]==pairs[stop-1][0]:stop+=1
        terms.append(abs(math.fsum(p-y for p,y in pairs[start:stop]))/n)
        start=stop
        if start==n:break
    assert start==n
    return math.fsum(terms)


def inspect(valid,predictions):
    assert set(predictions)=={r.context.race_id for r in valid}
    winners,win_pairs,top2,top3={ },[],[],[]
    incomplete=0
    for race in valid:
        ctx=race.context;ids=[h.horse_id for h in ctx.started_horses]
        pred=predictions[ctx.race_id]
        assert set(pred)==set(ids) and len(ids)==len(set(ids)) and race.n_result_rows is not None
        heads=[[float(getattr(pred[h],k)) for h in ids] for k in ('win','top2','top3')]
        for k,values in enumerate(heads,1):
            assert all(math.isfinite(p) and -1e-10<=p<=1+1e-10 for p in values)
            assert abs(math.fsum(values)-min(k,len(ids)))<=1e-8
        assert all(-1e-10<=pred[h].win<=pred[h].top2+1e-10 and pred[h].top2<=pred[h].top3+1e-10 for h in ids)
        if race.n_result_rows<len(ids):
            incomplete+=1;continue
        labels={x.horse_id:(int(x.win),int(x.top2),int(x.top3)) for x in race.labels}
        ws=[h for h in ids if labels.get(h,(0,0,0))[0]==1]
        if len(ws)==1:winners[ctx.race_id]=loss(pred[ws[0]].win)
        for h in ids:
            y=labels.get(h,(0,0,0));v=pred[h]
            win_pairs.append((v.win,y[0]));top2.append(loss(v.top2,y[1]));top3.append(loss(v.top3,y[2]))
    return {'nll':math.fsum(winners.values())/len(winners),'top2':math.fsum(top2)/len(top2),
            'top3':math.fsum(top3)/len(top3),'ece':ece(win_pairs),'winner_rows':winners,
            'complete_started_horses':len(win_pairs),'incomplete_races':incomplete}


def bootstrap(rows):
    byday=defaultdict(list)
    for row in rows:byday[row['race_day']].append(row['diff'])
    days=sorted(byday);sums=np.array([sum(byday[d]) for d in days]);counts=np.array([len(byday[d]) for d in days])
    rng=np.random.default_rng(20260907);samples=[]
    for _ in range(4000):
        ix=rng.integers(0,len(days),size=len(days));samples.append(sums[ix].sum()/counts[ix].sum())
    p=float(sums.sum()/counts.sum());lo,hi=np.percentile(samples,[.625,99.375])
    #122 has exactly one annual outer fold and k=1; no seed averaging shrinkage.
    pad=statistics.NormalDist().inv_cdf(.99375)*.001816
    return {'point':p,'sample':[float(lo),float(hi)],'total':[p-math.hypot(p-lo,pad),p+math.hypot(hi-p,pad)],'n_days':len(days)}


def main():
    cfg,f=m.verify()
    assert not (m.WORK/'running.lock').exists() and all(m.completed(j) for j in f['jobs'])
    matrix,races,fold=m.inputs(cfg);valid=list(fold.valid);del matrix,races;gc.collect()
    assert len(valid)==3454
    preds={};cache_hashes={}
    for name in m.NAMES:
        if name=='baseline' and f['baseline']['mode']=='native_cache':
            path=Path(f['baseline']['path'])
        else:
            job=next(j for j in f['jobs'] if j['arm']==name);assert m.completed(job)
            path=m.WORK/'cache'/f"{job['key']}.pkl"
        cache=m.s.reuse.load(path);preds[name]=cache['predictions'];cache_hashes[name]=m.s.p.digest(path)
        assert len(cache['feature_columns'])=={'baseline':125,'f03':126,'f05':131,'colsample_07':125}[name]
        assert cache['oof_info']['sufficient'] is True
        if name=='colsample_07':assert cache['actual_params']['colsample_bytree']==.7
    scored={name:inspect(valid,p) for name,p in preds.items()};base=scored['baseline']
    assert len(base['winner_rows'])==3448
    records={};hashes={}
    for name in m.NAMES[1:]:
        assert m.verified_result(name,cfg,f)
        path=m.result_path(name);r=m.s.read_json(path);e=m.s.read_json(r['evidence_path']);c=scored[name]
        assert list(c['winner_rows'])==list(base['winner_rows'])==[x['race_id'] for x in e['rows']]
        for row in e['rows']:
            rid=row['race_id'];close(c['winner_rows'][rid],row['candidate_winner_nll']);close(base['winner_rows'][rid],row['active_winner_nll'])
            close(c['winner_rows'][rid]-base['winner_rows'][rid],row['diff'])
        q=r['gate']['reasons'];diff=c['nll']-base['nll']
        for x,y in [(c['nll'],r['periods']['all']['candidate']),(base['nll'],r['periods']['all']['active']),
                    (diff,r['periods']['all']['diff']),(c['top2']-base['top2'],q['top2_diff']),
                    (c['top3']-base['top3'],q['top3_diff']),(c['ece'],q['cand_ece']),(base['ece'],q['act_ece'])]:close(x,y)
        quality=c['top2']-base['top2']<=.0005 and c['top3']-base['top3']<=.0005 and c['ece']<.05 and c['ece']-base['ece']<=.001
        state='QUALITY_REVIEW_REQUIRED' if not quality else 'ADVANCE_TO_FULL_RESEARCH' if diff<0 else 'DEFER'
        assert r['progression']==state
        ci=bootstrap(e['rows']);assert ci['n_days']==r['total_ci']['n_days']==109
        for key,bounds in [('bootstrap_ci',ci['sample']),('total_ci',ci['total'])]:
            close(ci['point'],r[key]['point']);close(bounds[0],r[key]['ci_low']);close(bounds[1],r[key]['ci_high'])
        hashes[name]={'report_sha256':m.s.p.digest(path),'evidence_sha256':m.s.p.digest(r['evidence_path'])}
        records[name]={'nll':c['nll'],'diff':diff,'top2_diff':c['top2']-base['top2'],'top3_diff':c['top3']-base['top3'],
                       'candidate_ece':c['ece'],'baseline_ece':base['ece'],'progression':state,'ci':ci}
    summary=m.s.read_json(m.SPEC/'verdict.json')
    assert summary['reports']=={name:{'sha256':hashes[name]['report_sha256'],'progression':records[name]['progression']} for name in m.NAMES[1:]}
    m.verify()
    result={'artifact_kind':'market_feature_screen_independent_review','status':'PASS','can_adopt':False,'eligible_for_verdict':False,
            'additional_fits':0,'method_sha256':m.s.p.digest(__file__),'run_freeze_sha256':m.s.p.digest(m.WORK/'run-freeze.json'),
            'summary_sha256':m.s.p.digest(m.SPEC/'verdict.json'),'report_hashes':hashes,'cache_hashes':cache_hashes,
            'population':{'n_races':3454,'n_eligible':3448,'n_days':109,'complete_started_horses':base['complete_started_horses'],
                          'incomplete_races':base['incomplete_races']},'comparisons':records,'checks':CHECKS,'max_numeric_error':MAX_ERROR,
            'limitations':['2018 screen only; no recent/nk/full-period or stack increment assertion.','Cached forecasts audited; no independent model retraining. Feature temporal prefix and common matrix parity are certified in the frozen prepare audit.']}
    path=m.SPEC/'evidence/independent-review.json'
    if path.exists():assert m.s.read_json(path)==result
    else:m.write_json(path,result)
    print('122 INDEPENDENT AUDIT PASS',CHECKS,MAX_ERROR,flush=True)


if __name__=='__main__':main()
