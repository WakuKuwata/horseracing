from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import joint_residual_stack as j
from horseracing_training.calib_split import assemble_predictions
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_eval.dataset import EvalRace, ScoringLabel

SELECTED=['prior_gap','global_temperature']


def context(year=2020):
    return NS(race_id='r',race_date=dt.date(year,3,1),started_horses=[NS(horse_id=h) for h in ['a','b','c']])


def lookup(ctx=None):
    ctx=ctx or context()
    return {(ctx.race_id,h):(str(ctx.race_date),*v) for h,v in zip(['a','b','c'],[(2.,3.,.2,.5),(1.,2.,0.,0.),(np.nan,np.nan,np.nan,np.nan)])}


def coef(selected=SELECTED,beta=None):
    return {'base_recipe_hash':'raw','selected':selected,'coefficient_order':j.order_for(selected),
        'gammas':{str(y):list(beta or [0.]*len(j.order_for(selected))) for y in range(2020,2027)}}


@pytest.mark.parametrize('selected,length,count',[(['prior_gap'],4,9),(['global_temperature'],4,9),(SELECTED,5,12)])
def test_conditional_order_and_exact_comparison_count(selected,length,count):
    assert len(j.order_for(selected))==length
    assert len(j.comparisons(selected))==count
    assert j.order_for(selected)[-1]==('centered_logp' if 'global_temperature' in selected else 'female_cos')


@pytest.mark.parametrize('selected',[[],['unknown'],['prior_gap','prior_gap'],['global_temperature','prior_gap']])
def test_unregistered_or_empty_selection_stops(selected):
    with pytest.raises(ValueError):j.order_for(selected)


def test_design_matches_prior_season_and_centered_logp_with_neutral_missing():
    p=np.array([.1,.2,.7]);h=j.design(context(),lookup(),p,SELECTED)
    np.testing.assert_array_equal(h[:,:4],[[2.,3.,.2,.5],[1.,2.,0.,0.],[0.,0.,0.,0.]])
    np.testing.assert_array_equal(h[:,-1],np.log(p)-np.log(p).mean())


@pytest.mark.parametrize('mutation',['missing','date','inf'])
def test_input_join_or_infinite_feature_failure(mutation):
    values=lookup()
    if mutation=='missing':del values[('r','a')]
    elif mutation=='date':values[('r','a')]=('2021-01-01',2.,3.,.2,.5)
    else:values[('r','a')]=('2020-03-01',np.inf,3.,.2,.5)
    with pytest.raises(ValueError):j.design(context(),values,np.array([.1,.2,.7]),SELECTED)


@pytest.mark.parametrize('beta',[[0.,0.,0.,-1.],[0.,0.,0.,-2.]])
def test_global_nonpositive_temperature_blocked(beta):
    with pytest.raises(j.TemperatureDomainError):j.gamma_array(beta,['global_temperature'])


def test_last_season_coefficient_not_misclassified_as_temperature():
    np.testing.assert_array_equal(j.gamma_array([0.,0.,0.,-2.],['prior_gap']),[0.,0.,0.,-2.])


@pytest.mark.parametrize('beta',[[True]*5,[0.]*4,[0.,0.,0.,0.,np.nan],['0']*5])
def test_invalid_gamma_shape_type_or_finite_stops(beta):
    with pytest.raises(ValueError):j.gamma_array(beta,SELECTED)


def test_zero_gamma_preserves_below_legacy_clip_and_all_heads():
    base=assemble_predictions(['a','b','c'],[1e-8,.2,.79999999],eps=0.)
    out=j.assemble(base,context(),lookup(),coef())
    np.testing.assert_allclose(j.m.matrix_values(out,['a','b','c']),j.m.matrix_values(base,['a','b','c']),rtol=0,atol=1e-12)


def test_joint_scalar_formula_and_harville_heads():
    base=assemble_predictions(['a','b','c'],[.1,.2,.7],eps=0.)
    beta=np.array([.02,-.01,.03,-.02,.05]);p=np.array([base[h].win for h in ['a','b','c']])
    h=j.design(context(),lookup(),p,SELECTED);w=p*np.exp(h@beta);expected=assemble_predictions(['a','b','c'],w/w.sum(),eps=0.)
    out=j.assemble(base,context(),lookup(),coef(beta=beta.tolist()))
    np.testing.assert_allclose(j.m.matrix_values(out,['a','b','c']),j.m.matrix_values(expected,['a','b','c']),rtol=0,atol=1e-12)


def report(diff=-1e-8):
    r={'evaluation_contract_version':'v4','can_adopt':False,'eligible_for_verdict':False,'n_races':23030,'n_eligible':22990,
       'periods':{p:{'candidate':2.,'active':2.-diff,'diff':diff} for p in ('all','recent_3y','recent_5y')},
       'total_ci':{'point':diff,'ci_low':-.1,'ci_high':.1,'n_days':715},
       'gate':{'recent_guard':True,'top_noninferior':True,'calibration':True,
               'reasons':{'top2_diff':-.0001,'top3_diff':.0001,'cand_ece':.002,'act_ece':.0018}},
       'subgroups':{'critical':['canonical','nk','recent_year_only'],
                    'subgroup_decisions':{'canonical':'PASS','nk':'NO_DECISION','recent_year_only':'INCONCLUSIVE_LOW_PRECISION'},
                    'subgroup_guard_status':'NOT_PROVEN'}}
    refresh(r);return r


def refresh(r):r['research_disposition']=j.s.research.assess_research(r)
def reports():return {c['id']:report() for c in j.comparisons(SELECTED)}


def test_small_gain_with_unproven_subgroup_and_crossing_ci_retained():
    x=j.summarize_reports(reports(),SELECTED,'joint_mixed6')
    assert x['joint_retention']==x['mixture_retention']=='RETAIN'
    assert x['preferred_research_configuration']=='new_joint_mixed6'


def test_individual_nonimproving_seed_does_not_veto_negative_mean():
    rs=reports();rs['joint-seed-42-vs-season']=report(.0001)
    for seed in (43,44):rs[f'joint-seed-{seed}-vs-season']=report(-.0002)
    assert j.summarize_reports(rs,SELECTED,'joint_mixed6')['joint_retention']=='RETAIN'


def test_blocked_single_model_does_not_veto_valid_mixture_and_is_visible():
    rs=reports();key='joint-seed-42-vs-season';rs[key]['gate']['top_noninferior']=False;refresh(rs[key])
    result=j.summarize_reports(rs,SELECTED,'joint_mixed6')
    assert result['joint_retention']=='REVIEW_REQUIRED' and result['mixture_retention']=='RETAIN'
    assert result['blocked_comparisons']==[key]


def test_mixture_nonimprovement_keeps_old_preference_despite_joint_retained():
    rs=reports();rs['mixture-vs-mixed6']=report(0.)
    result=j.summarize_reports(rs,SELECTED,'joint_mixed6')
    assert result['joint_retention']=='RETAIN' and result['mixture_retention']=='DEFER'
    assert result['preferred_research_configuration']=='joint_mixed6'


@pytest.mark.parametrize('change',['missing','wrong_bool','missing_critical'])
def test_incomplete_quality_does_not_become_an_average(change):
    rs=reports();key=next(iter(rs))
    if change=='missing':del rs[key]
    elif change=='wrong_bool':rs[key]['gate']['recent_guard']=None;refresh(rs[key])
    else:rs[key]['subgroups']['critical']=[];refresh(rs[key])
    with pytest.raises(ValueError):j.summarize_reports(rs,SELECTED,'joint_mixed6')


def test_factory_rejects_wrong_base_recipe_and_binds_joint_coefficients():
    base=NS(recipe_hash='raw',recipe_meta={'seed':42},expected_columns=['x'])
    f=j.Factory(base,lookup(),coef());changed=coef(beta=[.1,0.,0.,0.,0.])
    assert f.recipe_hash!=j.Factory(base,lookup(),changed).recipe_hash
    base.recipe_hash='wrong'
    with pytest.raises(ValueError):j.Factory(base,lookup(),coef())


def test_predictor_refuses_wrong_year():
    p=j.Predictor(None,lookup(),coef(),2021)
    with pytest.raises(ValueError,match='year'):p.predict_race(context())


def test_mixture_requires_complete_bundle_and_keeps_all_heads():
    with pytest.raises(ValueError):j.MixtureFactory([])
    ms=[NS(recipe_meta={'i':i},expected_columns=['x']) for i in range(6)]
    f=j.MixtureFactory(ms)
    assert f.recipe_meta['member_order']==[('joint',s) for s in j.SEEDS]+[('anchor_gap',s) for s in j.SEEDS]
    assert f.recipe_meta['postprocess'] is None


def test_config_has_original_arms_and_no_booster_fit():
    cfg=j.load_config()
    assert cfg['arms']==j.n.load_config()['arms'] and cfg['additional_booster_fits']==0
    assert cfg['maximum_comparisons']==12


def synthetic_races():
    races,values=[],{}
    for year in range(2018,2027):
        for day in (1,2,3):
            ctx=RaceContext(f'{year}-{day}',dt.date(year,1,day),tuple(HorseEntry(h) for h in ['a','b','c']))
            labels=tuple(ScoringLabel(h,int(i==day-1),int(i<2),1) for i,h in enumerate(['a','b','c']))
            races.append(EvalRace(ctx,labels,3));values.update(lookup(ctx))
    return races,values


class FakeBase:
    expected_columns=['fixture'];recipe_meta={'kind':'fixture'};recipe_hash='raw'
    def fit(self,train,**kwargs):
        return NS(is_leaky_reference=False,predict_race=lambda ctx:assemble_predictions(['a','b','c'],[1/3]*3,eps=0.))


@pytest.mark.parametrize('selected',[['prior_gap'],['global_temperature'],SELECTED])
def test_real_joint_coefficient_fit_strict_prior_and_actual_paired_eval(selected):
    races,values=synthetic_races()
    folds={f.valid_year:f for f in j.s.expanding_folds(races,2019,valid_from=dt.date(2019,1,1))}
    saved=j.fit_coefficients(FakeBase(),folds,values,selected,42)
    assert saved['warmup_eligible_races']==3 and saved['evaluated_eligible_races']==21
    for index,diag in enumerate(saved['fit_diagnostics']):
        assert diag['fit_races']==3*(index+1) and diag['fit_last_day']<diag['eval_first_day']
        assert diag['gradient_inf']<=1e-5
    factory=j.Factory(FakeBase(),values,saved)
    cfg=deepcopy(j.load_config());cfg['bootstrap']['b']=20;cfg['eval_window']['min_eval_days']=1
    r=j.s.p.paired_eval(factory,FakeBase(),races,gate_config=cfg,first_valid_year=2020,
        valid_from=dt.date(2020,1,1),subgroups=True,num_threads=1)
    assert r.n_races==r.n_eligible==21
    assert r.candidate_recipe_hash==factory.recipe_hash and r.candidate_recipe_hash!=r.active_recipe_hash


def coefficient_fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(j,'WORK',tmp_path)
    (tmp_path/'run-freeze.json').write_text('{}')
    population={str(y):{'all_races':3,'eligible_races':3} for y in range(2019,2027)}
    frozen={'sources':{'selected':SELECTED,'population':population},'base_recipe_hashes':{'42':'raw'}}
    c=coef();c.update(artifact_kind='joint_residual_stack_coefficients',training_seed=42,base_arm='pruning125_raw',
        can_adopt=False,eligible_for_verdict=False,warmup_eligible_races=3,evaluated_eligible_races=21,
        fit_diagnostics=[{'eval_year':y,'fit_races':3*(y-2019),'fit_last_day':f'{y-1}-01-03','eval_first_day':f'{y}-01-01',
                          'regularized_fit_objective':0.,'gradient_inf':0.} for y in range(2020,2027)],
        temperature_domain_checks=[{'year':y,'fit_all_races':3*(y-2019),'held_all_races':3,'temperature_exponent':1.} for y in range(2020,2027)])
    path=j.seed_area(42)/'coefficients.json';j.write_json(path,c)
    receipt=path.with_name('coefficients-receipt.json')
    j.write_json(receipt,{'sha256':j.s.p.digest(path),'freeze_sha256':j.s.p.digest(tmp_path/'run-freeze.json'),'seed':42})
    return frozen,path,receipt,c


@pytest.mark.parametrize('mutation',['none','wrong_seed','order','population','year_missing','temp','fit_count','stale_receipt','failure'])
def test_saved_coefficient_scope_and_receipt_fail_closed(tmp_path,monkeypatch,mutation):
    frozen,path,receipt,c=coefficient_fixture(tmp_path,monkeypatch)
    if mutation=='none':assert j.verified_coefficients(42,frozen)==c;return
    if mutation=='wrong_seed':c['training_seed']=43
    elif mutation=='order':c['coefficient_order'].reverse()
    elif mutation=='population':c['evaluated_eligible_races']-=1
    elif mutation=='year_missing':del c['gammas']['2026']
    elif mutation=='temp':c['gammas']['2026'][-1]=-1.
    elif mutation=='fit_count':c['fit_diagnostics'][0]['fit_races']=999
    elif mutation=='failure':path.with_name('numerical-failure.json').write_text('{}')
    else:c['gammas']['2026'][0]=.02
    path.write_text(json.dumps(c))
    if mutation!='stale_receipt':
        receipt.write_text(json.dumps({'sha256':j.s.p.digest(path),'freeze_sha256':j.s.p.digest(j.WORK/'run-freeze.json'),'seed':42}))
    with pytest.raises(ValueError):j.verified_coefficients(42,frozen)


@pytest.mark.parametrize('mutation',['horse_day','baseline','negative','diff','mean','fullhash'])
def test_saved_result_population_and_loss_invariants(monkeypatch,mutation):
    c=j.comparisons(SELECTED)[0]
    old=[{'race_id':'r','race_day':'2020-01-01','candidate_winner_nll':2.}]
    original={'n_races':1,'n_eligible':1,'race_id_set_hash':'hash'}
    monkeypatch.setattr(j,'baseline_rows',lambda _: (original,old,'candidate_winner_nll'))
    r={**original,'periods':{'all':{'candidate':1.9,'active':2.,'diff':-.1}}}
    rows=[{'race_id':'r','race_day':'2020-01-01','candidate_winner_nll':1.9,'active_winner_nll':2.,'diff':-.1}]
    j.validate_rows(r,rows,c)
    if mutation=='horse_day':rows[0]['race_day']='2020-01-02'
    elif mutation=='baseline':rows[0]['active_winner_nll']=2.1
    elif mutation=='negative':rows[0]['candidate_winner_nll']=-1.
    elif mutation=='diff':rows[0]['diff']=0.
    elif mutation=='mean':r['periods']['all']['candidate']=1.8
    else:r['race_id_set_hash']='wrong'
    with pytest.raises(ValueError):j.validate_rows(r,rows,c)


def test_complete_result_resume_rechecks_all_coefficient_provenance(tmp_path,monkeypatch):
    monkeypatch.setattr(j,'WORK',tmp_path);monkeypatch.setattr(j,'SPEC',tmp_path/'spec')
    cfg={};frozen={};c=j.comparisons(SELECTED)[0]
    (tmp_path/'run-freeze.json').write_text('{}')
    path=j.result_path(c);ep=tmp_path/f"{c['id']}-evidence.json";rp=tmp_path/f"{c['id']}-receipt.json"
    j.write_json(ep,{'rows':[]});provenance={'42':'original','43':'original','44':'original'}
    monkeypatch.setattr(j,'provenance',lambda _:dict(provenance));monkeypatch.setattr(j,'validate_rows',lambda *args:None)
    r=report();r.update(artifact_kind='joint_residual_stack_report',comparison=c,study_config_hash=j.s.p.gate_config_hash(cfg),
        run_freeze_sha256=j.s.p.digest(tmp_path/'run-freeze.json'),coefficient_provenance=dict(provenance),evidence_path=str(ep),evidence_sha256=j.s.p.digest(ep))
    refresh(r)
    j.write_json(path,r);j.write_json(rp,{'report_sha256':j.s.p.digest(path),'evidence_sha256':j.s.p.digest(ep),'freeze_sha256':j.s.p.digest(tmp_path/'run-freeze.json')})
    assert j.verified_result(c,cfg,frozen)
    provenance['44']='changed'
    with pytest.raises(ValueError):j.verified_result(c,cfg,frozen)


def test_fixed117_diagnostics_preserve_population_and_add_no_gate():
    attrs=[{'race_id':'r1','race_day':'2026-01-01','year':2026,'venue':'06','relative_coverage':'(.5,1)'},
           {'race_id':'r2','race_day':'2026-01-02','year':2026,'venue':'05','relative_coverage':'1'}]
    rows=[{'race_id':v['race_id'],'race_day':v['race_day'],'diff':d} for v,d in zip(attrs,[.02,-.01])]
    result=j.fixed_diagnostics(attrs,{'comparison':rows})
    assert result['2026_all']['mean_diffs']['comparison']==pytest.approx(.005)
    assert result['2026_nakayama']['n_races']==1 and result['2026_nakayama']['mean_diffs']['comparison']==.02
    assert all(v['new_ci'] is False and v['extra_gate'] is False for v in result.values())
    with pytest.raises(ValueError):j.fixed_diagnostics(attrs,{'comparison':rows[::-1]})
