"""Independent121 saved-coefficient audit; no coefficient/booster fit or source writes."""
from __future__ import annotations
from collections import defaultdict
import gc
import math
from pathlib import Path
import pickle
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import extra_residual_probes as driver

MAX_ERROR = 0.0


def close(actual, expected, tol=1e-12):
    global MAX_ERROR
    actual, expected = float(actual), float(expected)
    assert math.isfinite(actual) and math.isfinite(expected)
    error = abs(actual-expected)
    MAX_ERROR = max(MAX_ERROR,error)
    assert error <= tol, (actual, expected,error)


def mean(values):
    return math.fsum(values)/len(values)


def context(row):
    p = np.asarray(row['p'],dtype=float)
    assert np.isfinite(p).all() and (p>0).all()
    close(p.sum(),1.,1e-8)
    logp = np.log(p)
    x = logp-mean(logp)
    entropy = -math.fsum(p*logp)/math.log(len(p)) if len(p)>1 else 0.
    ordered = sorted(p,reverse=True)
    topgap = math.log(ordered[0]/max(ordered[1] if len(p)>1 else 0.,1e-12))
    return x,entropy,topgap


def edges_from_prior(prior):
    values = np.array([context(r)[1:] for r in prior])
    return {name:np.unique(np.quantile(values[:,i],[1/3,2/3])).tolist() for i,name in enumerate(('entropy','top_gap'))}


def basis(row,candidate,edges):
    gap = np.array([0. if math.isnan(v) else v for v in row['gap']])
    if candidate=='prior_gap':
        prior = np.array([0. if math.isnan(v) else v for v in row['prior_gap']])
        return np.column_stack((gap,prior)),1.
    x,en,tg = context(row)
    if candidate=='global_temperature':
        return np.column_stack((gap,x)),None
    groups = [0 if len(x)<=9 else 1 if len(x)<=13 else 2,
              sum(en>=cut for cut in edges['entropy']),sum(tg>=cut for cut in edges['top_gap'])]
    values = np.zeros((len(x),11));values[:,0]=gap;values[:,1]=x
    for axis,index in enumerate(groups):values[:,2+3*axis+index]=x
    return values,groups


def tilted(row,h,beta):
    assert np.isfinite(h).all() and np.isfinite(beta).all()
    offset = np.array([math.fsum(float(x)*float(b) for x,b in zip(v,beta,strict=True)) for v in h])
    logits = np.log(row['p'])+offset
    weights = np.exp(logits-max(logits));q=weights/math.fsum(weights)
    assert np.isfinite(q).all() and (q>0).all() and (len(q)==1 or (q<1).all())
    close(q.sum(),1.)
    return q


def loss(q,winner):
    return -math.log(min(max(float(q[winner]),1e-15),1-1e-15))


def check_fold(prior,held,candidate,saved):
    year = int(held[0]['day'][:4]);beta=np.array(saved['beta'])
    expected_edges=edges_from_prior(prior)
    assert saved['year']==year and saved['fit_races']==len(prior) and saved['eval_races']==len(held)
    assert saved['fit_last_day']==max(r['day'] for r in prior)<min(r['day'] for r in held)==saved['eval_first_day']
    assert all(int(r['day'][:4])<year for r in prior)
    if candidate=='context_temperature':
        for key,values in expected_edges.items():
            assert len(values)==len(saved['context_edges'][key])
            for actual,expected in zip(values,saved['context_edges'][key],strict=True):close(actual,expected)
    else:assert saved['context_edges'] is None
    edge = saved['context_edges'] if candidate=='context_temperature' else None
    ridge = np.array([1e-6]*len(beta))
    if candidate=='context_temperature':ridge[2:]=1000./len(prior)
    terms,grad=[],np.zeros(len(beta))
    ranges=[]
    for population in (prior,held):
        temperatures=[]
        for row in population:
            h,groups=basis(row,candidate,edge)
            effective = 1. if candidate=='prior_gap' else 1.+beta[1]
            if candidate=='context_temperature':effective+=math.fsum(beta[2+3*axis+index] for axis,index in enumerate(groups))
            assert effective>0 and math.isfinite(effective)
            temperatures.append(effective)
            if population is prior:
                q=tilted(row,h,beta);winner=row['winner']
                terms.append(-math.log(q[winner])+math.log(row['p'][winner]))
                grad+=q@h-h[winner]
        ranges.append([min(temperatures),max(temperatures)])
    for actual,expected in zip(ranges,[saved['temperature_exponent_fit_range'],saved['temperature_exponent_held_range']],strict=True):
        for x,y in zip(actual,expected,strict=True):close(x,y)
    objective=mean(terms)+.5*float(np.sum(ridge*beta**2))
    norm=float(np.max(np.abs(grad/len(prior)+ridge*beta)))
    diagnostic=saved['diagnostics']
    close(objective,diagnostic['regularized_fit_objective_delta' if candidate=='context_temperature' else 'regularized_fit_objective'])
    close(norm,diagnostic['gradient_inf'])
    assert objective<=1e-8 and norm<=1e-5
    if candidate=='context_temperature':close(diagnostic['context_ridge'],1000./len(prior))
    return {'year':year,'fit_races':len(prior),'objective_delta':objective,'gradient_inf':norm,'beta':beta.tolist()}


def ci(rows,contrast,cfg):
    key='retained_nll' if contrast=='retained' else 'global_temperature_nll'
    by_day=defaultdict(list)
    for row in rows:by_day[row['day']].append(row['candidate_nll']-row[key])
    days=sorted(by_day);sums=np.array([math.fsum(by_day[d]) for d in days]);counts=np.array([len(by_day[d]) for d in days])
    assert len(days)>1
    bs=cfg['bootstrap'];rng=np.random.default_rng(bs['seed']);samples=[]
    for _ in range(bs['b']):
        pick=rng.integers(0,len(days),size=len(days));samples.append(sums[pick].sum()/counts[pick].sum())
    lo,hi=np.percentile(samples,[100*bs['alpha']/2,100*(1-bs['alpha']/2)])
    return {'point':float(sums.sum()/counts.sum()),'ci_low':float(lo),'ci_high':float(hi),'n_races':len(rows),'n_days':len(days)}


def main():
    output=Path(__file__).with_suffix('.json')
    assert not output.exists(),'Preserve existing independent review'
    cfg,frozen=driver.verify()
    result_path=driver.SPEC/'evidence/residual-probes.json';receipt=driver.WORK/'result-receipt.json'
    report=driver.verified_output(result_path,receipt,cfg)
    records=[]
    for seed in driver.SEEDS:
        with driver.prepared_path(seed).open('rb') as f:rows=pickle.load(f)
        assert driver.s.p.stable_hash([(r['race_id'],r['day']) for r in rows])==frozen['population'][str(seed)]['ordered_eligible_hash']
        by_year={year:[r for r in rows if int(r['day'][:4])==year] for year in driver.YEARS}
        for year,pop in by_year.items():assert len(pop)==frozen['population'][str(seed)]['years'][str(year)]['eligible_races']
        held_all=[r for r in rows if r['day']>='2020-01-01'];seed_results=report['seed_results'][str(seed)];audit={}
        for candidate in driver.CANDIDATES:
            saved=seed_results[candidate]
            assert saved['can_adopt'] is False and saved['eligible_for_verdict'] is False
            assert saved['n_initial_fit_races']==len(by_year[2019]) and saved['n_evaluated_races']==len(held_all)
            if saved['state']!='COMPLETE':
                assert saved['state'] in ('BLOCKED_NUMERICAL','BLOCKED_TEMPERATURE_DOMAIN','BLOCKED_REFERENCE')
                audit[candidate]={'state':saved['state'],'scope':'Blocked candidates excluded; failed unsaved coefficients cannot be numerically revalidated.'}
                continue
            assert len(saved['folds'])==7
            assert [(r['race_id'],r['day']) for r in saved['rows']]==[(r['race_id'],r['day']) for r in held_all]
            checks=[];index=0;prior=list(by_year[2019]);maximum=0.
            for year,fold in zip(driver.YEARS[1:],saved['folds'],strict=True):
                held=by_year[year];checks.append(check_fold(prior,held,candidate,fold))
                for row in held:
                    target=saved['rows'][index];index+=1
                    h,_=basis(row,candidate,fold['context_edges']);q=tilted(row,h,np.array(fold['beta']))
                    current=loss(q,row['winner']);close(current,target['candidate_nll']);maximum=max(maximum,abs(current-target['candidate_nll']))
                    old=frozen['sources']['retained_coefficients'][str(seed)]['gammas'][str(year)]
                    gap=np.array([0. if math.isnan(v) else v for v in row['gap']])[:,None]
                    close(loss(tilted(row,gap,[old]),row['winner']),target['retained_nll'])
                    if candidate=='context_temperature':
                        global_result=seed_results['global_temperature'];assert global_result['state']=='COMPLETE'
                        close(target['global_temperature_nll'],global_result['rows'][index-1]['candidate_nll'])
                prior.extend(held)
            audit[candidate]={'state':'PASS','fold_checks':checks,'n_evaluated_races':index,'max_nll_error':maximum}
        records.append({'seed':seed,'candidates':audit})
        del rows,by_year,held_all;gc.collect()
        print(f'121 independent saved coefficient audit seed={seed}',flush=True)
    summaries={}
    for candidate in driver.CANDIDATES:
        saved=report['summary'][candidate]
        blocks=[report['seed_results'][str(seed)][candidate] for seed in driver.SEEDS]
        if any(block['state']!='COMPLETE' for block in blocks):
            assert saved['state']=='BLOCKED';summaries[candidate]={'state':'BLOCKED'};continue
        pooled=[]
        for records_per_seed in zip(*(b['rows'] for b in blocks),strict=True):
            first=records_per_seed[0];assert all((r['race_id'],r['day'])==(first['race_id'],first['day']) for r in records_per_seed)
            fields=['candidate_nll','retained_nll']+(['global_temperature_nll'] if candidate=='context_temperature' else [])
            pooled.append({'day':first['day'],**{field:mean([r[field] for r in records_per_seed]) for field in fields}})
        contrasts={}
        for contrast in driver.CONTRASTS[candidate]:
            calculated=ci(pooled,contrast,cfg);contrasts[contrast]=calculated
            target=saved['contrasts'][contrast]['mean_seed_loss_difference']
            assert target['kind']=='conditional_sample_ci' and target['is_total_ci'] is False
            for field,value in calculated.items():close(value,target[field])
            for seed,block in zip(driver.SEEDS,blocks,strict=True):
                for field,value in ci(block['rows'],contrast,cfg).items():close(value,saved['contrasts'][contrast]['per_seed'][str(seed)][field])
        state='ADVANCE_TO_FULL_QUALITY' if all(c['point']<0 for c in contrasts.values()) else 'NO_OBSERVED_MEAN_IMPROVEMENT'
        assert saved['state']==state;summaries[candidate]={'state':state,'contrasts':contrasts}
    driver.verify();driver.verified_output(result_path,receipt,cfg)
    driver.write_json(output,{'artifact_kind':'extra_residual_probe_independent_review','status':'PASS','can_adopt':False,'eligible_for_verdict':False,
        'method_sha256':driver.s.p.digest(__file__),'run_freeze_sha256':driver.s.p.digest(driver.WORK/'run-freeze.json'),
        'report_sha256':driver.s.p.digest(result_path),'receipt_sha256':driver.s.p.digest(receipt),
        'prepared_input_sha256':frozen['prepared_input_sha256'],'additional_fits':0,'seeds':records,'summary':summaries,
        'max_scalar_error':MAX_ERROR,'limitations':['Saved predictions and prepared feature rows are frozen upstream inputs; this audit does not independently refetch history.',
            'Only COMPLETE candidates receive coefficient-gradient/NLL validation; blocked candidates are excluded from progression.',
            'CI is conditional sample uncertainty of mean losses, not ensemble or coefficient-refit/training-noise uncertainty.']})


if __name__=='__main__':main()
