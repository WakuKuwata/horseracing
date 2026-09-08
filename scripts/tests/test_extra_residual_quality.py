from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import extra_residual_quality as q
from horseracing_eval.predictor import HorseEntry,RaceContext
from horseracing_eval.dataset import EvalRace,ScoringLabel
from horseracing_eval.decision import assert_verdict_eligible,VerdictSourceError


def context(year=2020,rid='r'):
    return RaceContext(rid,dt.date(year,1,1),tuple(HorseEntry(str(i)) for i in range(3)))


def lookup(ctx=None):
    ctx=ctx or context()
    return {(ctx.race_id,str(i)):(str(ctx.race_date),float(i),np.nan if i==0 else float(3-i)) for i in range(3)}


def coefficients(candidate='prior_gap',beta=(.02,.03)):
    return {'candidate_id':candidate,'state':'COMPLETE','folds':[{'year':y,'beta':list(beta),'context_edges':None,
        'fit_last_day':f'{y-1}-12-31','eval_first_day':f'{y}-01-01'} for y in range(2020,2027)]}


def predictions(p=(.2,.3,.5)):
    return q.g.assemble_predictions(['0','1','2'],np.array(p),eps=0.)


@pytest.mark.parametrize('candidate',q.CANDIDATES)
def test_saved_coefficient_assembly_matches121_win(candidate):
    ctx=context();base=predictions();fold=coefficients(candidate)['folds'][0]
    out,audit=q.assemble(base,ctx,lookup(),candidate,fold)
    gap,prior=q.h_values(ctx,lookup());row=q.probe.Row('r',str(ctx.race_date),np.array([.2,.3,.5]),gap,prior,0)
    expected=q.probe.corrected_p(row,q.probe.design(row,candidate),np.array(fold['beta']))
    np.testing.assert_allclose([v.win for v in out.values()],expected,atol=1e-12,rtol=0)
    values=np.array([[v.win,v.top2,v.top3] for v in out.values()])
    np.testing.assert_allclose(values.sum(0),[1,2,3],atol=1e-8,rtol=0)
    assert (np.diff(values,axis=1)>=0).all() and audit['max_post_assembly_win_change']<=1e-12


def test_zero_saved_correction_preserves_subclip_win_without_reclipping():
    p=np.array([1e-8,.2,.79999999]);base=predictions(p)
    out,audit=q.assemble(base,context(),lookup(),'global_temperature',coefficients(beta=(0.,0.))['folds'][0])
    np.testing.assert_allclose([v.win for v in out.values()],p,atol=1e-12,rtol=0)
    assert audit['below_legacy_clip_horses']==1


@pytest.mark.parametrize('damage',['date','missing','infinity'])
def test_feature_lookup_fails_closed(damage):
    values=lookup()
    if damage=='date':values[('r','0')]=('2021-01-01',0.,0.)
    elif damage=='missing':values.pop(('r','0'))
    else:values[('r','0')]=('2020-01-01',np.inf,0.)
    with pytest.raises(ValueError):q.h_values(context(),values)


@pytest.mark.parametrize('beta',[(0.,-1.),(0.,-2.),(0.,float('nan'))])
def test_temperature_domain_is_never_repaired(beta):
    with pytest.raises(ValueError):q.assemble(predictions(),context(),lookup(),'global_temperature',{'beta':beta})


def selection_report():
    return {'summary':{c:{'state':'ADVANCE_TO_FULL_QUALITY' if c in q.CANDIDATES else 'NO_OBSERVED_MEAN_IMPROVEMENT'} for c in q.probe.CANDIDATES},
            'seed_results':{str(seed):{c:{'state':'COMPLETE'} for c in q.probe.CANDIDATES} for seed in q.SEEDS}}


@pytest.mark.parametrize('damage',['C_added','A_removed','incomplete_seed'])
def test_selection_rule_cannot_be_manually_changed(damage):
    report=selection_report();assert q.selected_candidates(report)==q.CANDIDATES
    if damage=='C_added':report['summary']['context_temperature']['state']='ADVANCE_TO_FULL_QUALITY'
    elif damage=='A_removed':report['summary']['prior_gap']['state']='NO_OBSERVED_MEAN_IMPROVEMENT'
    else:report['seed_results']['44']['prior_gap']['state']='BLOCKED_NUMERICAL'
    with pytest.raises(ValueError):q.selected_candidates(report)


def test_saved_coefficient_scope_and_chronology(monkeypatch):
    c=coefficients();report={'seed_results':{'42':{'prior_gap':c}}}
    monkeypatch.setattr(q,'report121',lambda:report)
    assert q.coefficients(42,'prior_gap')==c
    c['folds'][0]['fit_last_day']='2020-01-02'
    with pytest.raises(ValueError,match='chronology'):q.coefficients(42,'prior_gap')


def test_wrong_year_rejected_before_replaying():
    predictor=q.Predictor(None,{},'prior_gap',{'year':2020},{})
    with pytest.raises(ValueError,match='wrong year'):predictor.predict_race(context(2021))


@pytest.mark.parametrize('candidate',q.CANDIDATES)
def test_actual_paired_eval_factory_protocol_and_recipe(candidate):
    races,values=[],{}
    for year in range(2019,2027):
        for day in range(1,4):
            ctx=RaceContext(f'{year}{day:04}',dt.date(year,1,day),tuple(HorseEntry(str(i)) for i in range(3)))
            labs=tuple(ScoringLabel(str(i),int(i==day-1),int(i<2),1) for i in range(3))
            races.append(EvalRace(ctx,labs,3));values.update(lookup(ctx))
    class Base:
        expected_columns=['fixture'];recipe_meta={'kind':'fixture'};recipe_hash=q.s.p.stable_hash(recipe_meta)
        def fit(self,train,**kwargs):return SimpleNamespace(is_leaky_reference=False,predict_race=lambda ctx:predictions())
    base=Base();cand=q.Factory(base,values,candidate,coefficients(candidate))
    cfg=deepcopy(q.load_config());cfg['bootstrap']['b']=20;cfg['eval_window']['min_eval_days']=1
    report=q.s.p.paired_eval(cand,base,races,gate_config=cfg,first_valid_year=2020,valid_from=dt.date(2020,1,1),subgroups=True,num_threads=1)
    assert report.n_races==report.n_eligible==21 and len(cand.audit)==21
    assert report.candidate_recipe_meta==cand.recipe_meta and report.candidate_recipe_hash!=base.recipe_hash
    assert report.active_recipe_hash==base.recipe_hash


def report(diff=-.001):
    r={'evaluation_contract_version':'v4','can_adopt':False,'eligible_for_verdict':False,'n_races':23030,'n_eligible':22990,'race_id_set_hash':'same',
       'periods':{p:{'candidate':2.,'active':2.-diff,'diff':diff} for p in q.aggregation.PERIODS},
       'total_ci':{'point':diff,'ci_low':-.1,'ci_high':.1,'n_days':715},
       'gate':{'recent_guard':True,'top_noninferior':True,'calibration':True,'reasons':{'top2_diff':-.0001,'top3_diff':.0001,'cand_ece':.002,'act_ece':.0018}},
       'subgroups':{'critical':['canonical','nk','recent_year_only'],'subgroup_decisions':{'canonical':'PASS','nk':'NO_DECISION','recent_year_only':'INCONCLUSIVE_LOW_PRECISION'},
       'subgroup_guard_status':'NOT_PROVEN','critical_residual_risk':{'nk':.0013}}}
    r['research_disposition']=q.s.research.assess_research(r);return r


def reports(a=(-.001,)*3,b=(-.001,)*3):return {s:{'anchor':report(x),'retained':report(y)} for s,x,y in zip(q.SEEDS,a,b,strict=True)}


def test_both_mean_contrasts_required_without_individual_sign_or_ci_veto():
    assert q.summarize_candidate(reports((-.01,.001,.001),(-.01,.001,.001)))['state']=='RETAIN'
    assert q.summarize_candidate(reports(b=(.001,)*3))['state']=='DEFER'
    with pytest.raises(VerdictSourceError):assert_verdict_eligible(q.summarize_candidate(reports()))


def test_hard_quality_failure_not_hidden_by_mean():
    values=reports();r=values[43]['anchor'];r['gate']['top_noninferior']=False;r['gate']['reasons']['top3_diff']=.0006
    r['research_disposition']=q.s.research.assess_research(r)
    result=q.summarize_candidate(values)
    assert result['state']=='REVIEW_REQUIRED' and result['contrasts']['anchor']['mean_quality_pass'] is True


def test_all_seeds_and_critical_evidence_required():
    values=reports();values.pop(44)
    with pytest.raises(ValueError):q.summarize_candidate(values)
    values=reports();values[42]['anchor']['subgroups']['critical']=[]
    with pytest.raises(ValueError):q.summarize_candidate(values)


def test_nll_parity_tolerance_scope_and_baseline(monkeypatch):
    original=[{'race_id':'r','race_day':'2020-01-01','active_winner_nll':2.,'candidate_winner_nll':1.9}]
    screen=[{'race_id':'r','day':'2020-01-01','candidate_nll':1.8}]
    monkeypatch.setattr(q.d,'result_path',lambda *args:'original')
    monkeypatch.setattr(q.s,'read_json',lambda p:{'evidence_path':'evidence'} if p=='original' else {'rows':original})
    monkeypatch.setattr(q,'report121',lambda:{'seed_results':{'42':{'prior_gap':{'rows':screen}}}})
    rows=[{'race_id':'r','race_day':'2020-01-01','active_winner_nll':2.,'candidate_winner_nll':1.8,'diff':-.2}]
    q.validate_rows(rows,42,'prior_gap','anchor')
    rows[0]['candidate_winner_nll']+=1e-6
    with pytest.raises(ValueError,match='differs from121'):q.validate_rows(rows,42,'prior_gap','anchor')
    rows[0]['candidate_winner_nll']=1.8;rows[0]['active_winner_nll']+=1e-12
    with pytest.raises(ValueError,match='baseline'):q.validate_rows(rows,42,'prior_gap','anchor')
    rows[0]['race_id']='other'
    with pytest.raises(ValueError,match='population'):q.validate_rows(rows,42,'prior_gap','anchor')


def test_full_population_includes_noneligible_hash(monkeypatch):
    expected={'n_races':23030,'n_eligible':22990,'race_id_set_hash':'all'}
    monkeypatch.setattr(q.d,'result_path',lambda *args:'original');monkeypatch.setattr(q.s,'read_json',lambda p:expected)
    q.validate_full(expected.copy(),42)
    altered={**expected,'race_id_set_hash':'eligibleonly'}
    with pytest.raises(ValueError,match='noneligible'):q.validate_full(altered,42)


def test_completed_resume_revalidates_coefficient_provenance(tmp_path,monkeypatch):
    monkeypatch.setattr(q,'WORK',tmp_path);monkeypatch.setattr(q,'SPEC',tmp_path/'spec')
    cfg={'arms':{'seed':42}}
    (tmp_path/'run-freeze.json').write_text('{}')
    directory=q.area(42,'prior_gap');directory.mkdir(parents=True);path=q.result_path(42,'prior_gap','anchor');path.parent.mkdir(parents=True)
    evidence=directory/'anchor-evidence.json';evidence.write_text('{"rows":[]}')
    prov={'coefficient_sha256':'original'}
    monkeypatch.setattr(q,'provenance',lambda *args:dict(prov));monkeypatch.setattr(q,'validate_full',lambda *args:None)
    monkeypatch.setattr(q,'validate_rows',lambda *args:None);monkeypatch.setattr(q.s.research,'assess_research',lambda r:{'state':'RETAIN_UNCERTAIN'})
    r={'artifact_kind':'extra_residual_quality_report','can_adopt':False,'eligible_for_verdict':False,'training_seed':42,'candidate_id':'prior_gap','contrast':'anchor',
       'study_config_hash':q.s.p.gate_config_hash(cfg),'seed_config_hash':q.s.p.gate_config_hash(cfg),'run_freeze_sha256':q.s.p.digest(tmp_path/'run-freeze.json'),
       'coefficient_provenance':dict(prov),'evidence_path':str(evidence),'evidence_sha256':q.s.p.digest(evidence),'research_disposition':{'state':'RETAIN_UNCERTAIN'}}
    path.write_text(json.dumps(r));receipt=directory/'anchor-receipt.json'
    receipt.write_text(json.dumps({'report_sha256':q.s.p.digest(path),'evidence_sha256':q.s.p.digest(evidence),'freeze_sha256':r['run_freeze_sha256']}))
    assert q.verified_result(42,'prior_gap','anchor',cfg,{})
    prov['coefficient_sha256']='changed'
    with pytest.raises(ValueError,match='coefficient changed'):q.verified_result(42,'prior_gap','anchor',cfg,{})


def test_config_fixed_no_coefficient_or_booster_fits():
    cfg=q.load_config()
    assert cfg['new_fit_jobs']==cfg['coefficient_refits']==0 and cfg['assembly_eps']==0.
    assert cfg['candidates']==q.CANDIDATES
