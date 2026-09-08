from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import datetime as dt
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import sparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extra_residual_probes as e
from horseracing_eval.decision import assert_verdict_eligible, VerdictSourceError


def history():
    return pd.DataFrame({'race_id':['a','b','c','c2','d','nk'], 'horse_id':['h']*5+['nk:h'],
        'race_date':[dt.date(2019,1,1),dt.date(2019,1,11),dt.date(2019,1,21),dt.date(2019,1,21),dt.date(2019,2,10),dt.date(2019,2,10)],
        'days_since_last':[np.nan,10.,10.,10.,20.,np.nan]})


def test_prior_gap_uses_distinct_two_prior_days_not_current_gap():
    out,audit=e.prior_gap_features(history())
    out=out.set_index('race_id')
    assert out.loc['d','prior_gap_days']==10
    assert out.loc['d','days_since_last']==20
    assert out.loc['d','D1']==pd.Timestamp('2019-01-21')
    assert out.loc['d','D2']==pd.Timestamp('2019-01-11')
    assert out.loc['c','prior_gap_days']==out.loc['c2','prior_gap_days']==10
    assert pd.isna(out.loc['a','prior_gap_days']) and pd.isna(out.loc['b','prior_gap_days'])
    assert pd.isna(out.loc['nk','prior_gap_days'])
    assert audit['current_gap_definition_differences']==0


def test_prior_gap_order_and_future_extension_invariant():
    f=history()
    a,_=e.prior_gap_features(f)
    b,_=e.prior_gap_features(f.sample(frac=1,random_state=42))
    pd.testing.assert_frame_equal(a.sort_values('race_id').reset_index(drop=True),b.sort_values('race_id').reset_index(drop=True))
    future=pd.DataFrame({'race_id':['future'],'horse_id':['h'],'race_date':[dt.date(2025,1,1)],'days_since_last':[2152.]})
    extended,_=e.prior_gap_features(pd.concat([f,future]))
    pd.testing.assert_series_equal(a.prior_gap_log,extended.loc[extended.race_id!='future','prior_gap_log'],check_names=True)


@pytest.mark.parametrize('bad',[0.,-1.,np.inf,-np.inf,.5])
def test_bad_current_gap_rejected(bad):
    f=history();f.loc[2,'days_since_last']=bad
    with pytest.raises(ValueError):e.prior_gap_features(f)


def test_duplicate_identity_rejected():
    f=history();f.loc[3,'race_id']='c'
    with pytest.raises(ValueError,match='duplicate'):e.prior_gap_features(f)


def row(day='2019-01-01', p=None,winner=0):
    p=np.array([.5,.25,.25]) if p is None else np.array(p)
    n=len(p)
    return e.Row(day,day,p,np.linspace(1,2,n),np.array([np.nan]+[1.]*(n-1)),winner)


def test_original_context_basis():
    r=row(p=[.6,.3,.1])
    x,en,gp=e.score_context(r)
    np.testing.assert_allclose(x,np.log(r.p)-np.log(r.p).mean())
    assert en==pytest.approx(-sum(r.p*np.log(r.p))/np.log(3))
    assert gp==pytest.approx(np.log(2))
    edges={'entropy':[.5,.9],'top_gap':[.1,.8]}
    h=e.design(r,'context_temperature',edges)
    assert h.shape==(3,11)
    for lo in [2,5,8]:np.testing.assert_allclose(h[:,lo:lo+3].sum(axis=1),x)
    np.testing.assert_allclose(e.design(r,'prior_gap')[:,1],[0.,1.,1.])


@pytest.mark.parametrize('n,index',[(9,0),(10,1),(13,1),(14,2)])
def test_field_size_boundaries(n,index):
    p=np.arange(1,n+1,dtype=float);p/=p.sum()
    r=row(p=p)
    h=e.design(r,'context_temperature',{'entropy':[], 'top_gap':[]})
    assert np.count_nonzero(h[:,2+index])>0
    assert np.count_nonzero(h[:,2:5])-np.count_nonzero(h[:,2+index])==0


def test_edges_are_only_from_passed_fit_races():
    prior=[row(p=[.8,.15,.05]),row('2019-01-02',p=[.5,.3,.2]),row('2019-01-03',p=[.7,.2,.1])]
    edges=e.context_edges(prior)
    vals=np.array([e.score_context(r)[1:] for r in prior])
    np.testing.assert_allclose(edges['entropy'],np.quantile(vals[:,0],[1/3,2/3]))
    assert e.context_edges(prior)==edges
    assert e.context_edges(prior+[row('2020-01-01',p=[1/3]*3)])!=edges


def test_single_horse_context_is_finite_and_neutral():
    r=row(p=[1.])
    x,en,gp=e.score_context(r)
    assert x[0]==0 and en==0 and np.isfinite(gp)
    np.testing.assert_equal(e.corrected_p(r,np.array([[20.]]),np.array([.3])),[1.])


@pytest.mark.parametrize('value',[-2.,-1.,np.nan,np.inf])
def test_nonpositive_temperature_domain_is_rejected(value):
    with pytest.raises(e.TemperatureDomainError):e.effective_temperature(row(),'global_temperature',np.array([0.,value]))


def test_context_effective_temperature_uses_selected_slopes():
    beta=np.zeros(11);beta[1]=.2;beta[2]=.1;beta[5]=.2;beta[8]=.3
    assert e.effective_temperature(row(),'context_temperature',beta,{'entropy':[],'top_gap':[]})==pytest.approx(1.8)
    assert e.effective_temperature(row(),'prior_gap',np.array([2.,3.]))==1.


def test_no_clipping_repairs_probability_endpoint():
    r=row()
    with pytest.raises(ValueError,match='endpoint'):
        e.corrected_p(r,np.array([[0.],[1.],[2.]]),np.array([1e6]))


def test_diagonal_ridge_objective_gradient():
    X=sparse.csr_matrix(np.array([[1.,2.],[0.,1.],[2.,0.],[1.,-1.]]))
    args=(X,np.array([0,0,1,1]),2,np.log([.6,.4,.4,.6]),np.array([True,False,False,True]),np.array([1e-6,.2]))
    beta=np.array([.12,-.04]);_,grad=e.objective(beta,*args)
    numeric=[]
    for i in range(2):
        step=np.eye(2)[i]*1e-6
        numeric.append((e.objective(beta+step,*args)[0]-e.objective(beta-step,*args)[0])/2e-6)
    np.testing.assert_allclose(grad,numeric,rtol=1e-5,atol=1e-7)


def synthetic_rows():
    return [row(f'{year}-01-0{day}',winner=0 if day<=2 else day-2) for year in e.YEARS for day in range(1,5)]


def cfg():
    out=deepcopy(e.load_config());out['bootstrap']['b']=20;return out


def test_seed_run_is_strict_prequential_and_2019_is_only_warmup():
    rows=synthetic_rows()
    retained={'gammas':{str(y):0. for y in e.YEARS[1:]}}
    out=e.run_seed(rows,retained,cfg(),42)
    for candidate,value in out.items():
        assert value['state']=='COMPLETE',value
        assert value['n_initial_fit_races']==4 and value['n_evaluated_races']==28
        assert len(value['rows'])==28 and all(r['day']>='2020-01-01' for r in value['rows'])
        assert all(f['fit_last_day']<f['eval_first_day'] for f in value['folds'])
        assert all(f['temperature_exponent_held_range'][0]>0 for f in value['folds'])
        with pytest.raises(VerdictSourceError):assert_verdict_eligible(value)


def test_future_labels_do_not_change_prior_fit():
    rows=synthetic_rows();retained={'gammas':{str(y):0. for y in e.YEARS[1:]}}
    out=e.run_seed(rows,retained,cfg(),42)
    changed=[replace(r,winner=2) if r.day.startswith('2021') else r for r in rows]
    other=e.run_seed(changed,retained,cfg(),42)
    for candidate in e.CANDIDATES:
        for i in [0,1]:
            np.testing.assert_equal(out[candidate]['folds'][i]['beta'],other[candidate]['folds'][i]['beta'])
            assert out[candidate]['folds'][i]['context_edges']==other[candidate]['folds'][i]['context_edges']


def summary_input():
    results={}
    for seed,delta in [(42,.001),(43,-.002),(44,-.002)]:
        results[seed]={}
        for candidate in e.CANDIDATES:
            records=[{'race_id':str(i),'day':f'2020-01-0{i}','candidate_nll':1.+delta,'retained_nll':1.,'global_temperature_nll':1.+delta-.01} for i in [1,2]]
            results[seed][candidate]={'state':'COMPLETE','rows':records,'n_evaluated_races':2}
    return results


def test_all_seed_mean_not_best_seed_or_probability_ensemble():
    summary=e.summarize(summary_input(),cfg())
    assert summary['prior_gap']['state']=='ADVANCE_TO_FULL_QUALITY'
    value=summary['prior_gap']['contrasts']['retained']['mean_seed_loss_difference']
    assert value['point']==pytest.approx(-.001)
    assert value['n_races']==2 and value['is_total_ci'] is False
    assert summary['context_temperature']['state']=='NO_OBSERVED_MEAN_IMPROVEMENT' # C worse than B.


def test_missing_seed_and_mismatched_population_fail_closed():
    results=summary_input();results.pop(44)
    with pytest.raises(ValueError,match='three seeds'):e.summarize(results,cfg())
    results=summary_input();results[44]['prior_gap']['rows'][0]['race_id']='other'
    with pytest.raises(ValueError,match='populations'):e.summarize(results,cfg())


def test_blocked_seed_never_averaged_away():
    results=summary_input();results[44]['prior_gap']['state']='BLOCKED_NUMERICAL'
    assert e.summarize(results,cfg())['prior_gap']['state']=='BLOCKED'


def test_registered_config():
    out=e.load_config()
    assert out['fit']['context_lambda']==1000.
    assert out['temperature_domain']['strict_positive'] is True
    assert out['eligible_for_verdict'] is False


def test_retained_nll_parity_and_scope():
    rows=[row('2019-01-01'),row('2020-01-01'),row('2020-01-02')]
    coefficients={'gammas':{'2020':-.03}}
    reference=[]
    for r in rows[1:]:
        q=e.corrected_p(r,r.gap[:,None],np.array([-.03]))
        reference.append({'race_id':r.race_id,'race_day':r.day,'candidate_winner_nll':e._clip_nll(float(q[r.winner]))})
    proof=e.retained_parity(rows,reference,coefficients)
    assert proof['eligible_races']==2 and proof['max_abs_nll_error']==0.
    altered=deepcopy(reference);altered[0]['candidate_winner_nll']+=1e-6
    with pytest.raises(ValueError,match='NLL differs'):e.retained_parity(rows,altered,coefficients)
    with pytest.raises(ValueError,match='population'):e.retained_parity(rows,list(reversed(reference)),coefficients)


def test_report_resume_hash_and_artifact_scope(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(e,'WORK',tmp_path)
    (tmp_path/'run-freeze.json').write_text('{}')
    config=cfg();out=tmp_path/'result.json';receipt=tmp_path/'receipt.json'
    report={'artifact_kind':'extra_residual_probe_report','can_adopt':False,'eligible_for_verdict':False,
            'study_config_hash':e.s.p.gate_config_hash(config),'run_freeze_sha256':e.s.p.digest(tmp_path/'run-freeze.json'),
            'seed_results':{str(seed):value for seed,value in summary_input().items()},'summary':e.summarize(summary_input(),config)}
    out.write_text(json.dumps(report));receipt.write_text(json.dumps({'report_sha256':e.s.p.digest(out),'freeze_sha256':report['run_freeze_sha256']}))
    assert e.verified_output(out,receipt,config)==report
    out.write_text('{}')
    with pytest.raises(ValueError,match='receipt'):e.verified_output(out,receipt,config)
    receipt.write_text(json.dumps({'report_sha256':e.s.p.digest(out),'freeze_sha256':report['run_freeze_sha256']}))
    with pytest.raises(ValueError,match='scope'):e.verified_output(out,receipt,config)


def test_incomplete_scored_rows_fail_even_with_complete_label():
    result=summary_input();result[42]['prior_gap']['n_evaluated_races']=3
    with pytest.raises(ValueError,match='count'):e.summarize(result,cfg())


def test_completed_summary_tamper_rejected_with_new_receipt(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(e,'WORK',tmp_path)
    (tmp_path/'run-freeze.json').write_text('{}')
    config=cfg();out=tmp_path/'result.json';receipt=tmp_path/'receipt.json'
    report={'artifact_kind':'extra_residual_probe_report','can_adopt':False,'eligible_for_verdict':False,
        'study_config_hash':e.s.p.gate_config_hash(config),'run_freeze_sha256':e.s.p.digest(tmp_path/'run-freeze.json'),
        'seed_results':{str(seed):value for seed,value in summary_input().items()},'summary':e.summarize(summary_input(),config)}
    report['summary']['prior_gap']['state']='NO_OBSERVED_MEAN_IMPROVEMENT'
    out.write_text(json.dumps(report));receipt.write_text(json.dumps({'report_sha256':e.s.p.digest(out),'freeze_sha256':report['run_freeze_sha256']}))
    with pytest.raises(ValueError,match='summary differs'):e.verified_output(out,receipt,config)


def test_different_candidate_population_rejected():
    results=summary_input()
    for seed in e.SEEDS:results[seed]['global_temperature']['rows'][0]['race_id']='changed'
    with pytest.raises(ValueError,match='Candidate evaluation populations'):e.summarize(results,cfg())
