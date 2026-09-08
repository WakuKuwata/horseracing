from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import season_mixture_stack as m
from horseracing_training.calib_split import assemble_predictions
from horseracing_eval.predictor import Prediction


IDS = ['a', 'b', 'c']


def members():
    return [assemble_predictions(IDS, np.array(p), eps=0.) for p in ([.8,.1,.1], [.1,.8,.1], [.3,.2,.5])]


def test_all_heads_are_arithmetic_means_and_not_harville_of_mean_win():
    ps = members(); result, excess = m.average_predictions(ps, IDS, 3)
    x = np.array([[[p[h].win,p[h].top2,p[h].top3] for h in IDS] for p in ps])
    assert np.array_equal(m.matrix_values(result, IDS), x.mean(0))
    harville = assemble_predictions(IDS, x.mean(0)[:,0], eps=0.)
    assert max(abs(result[h].top2-harville[h].top2) for h in IDS) > .01
    assert excess <= 1e-12


@pytest.mark.parametrize('count,actual', [(3,2),(6,3),(3,4),(2,2)])
def test_partial_extra_or_unregistered_member_count_rejected(count,actual):
    ps = (members()*2)[:actual]
    with pytest.raises(ValueError,match='members'):
        m.average_predictions(ps,IDS,count)


@pytest.mark.parametrize('mutation', ['missing','extra','nan','sum','order'])
def test_invalid_member_head_probabilities_stop(mutation):
    ps=members()
    if mutation=='missing': del ps[0]['a']
    elif mutation=='extra': ps[0]['d']=ps[0]['a']
    elif mutation=='nan': ps[0]['a']=Prediction(float('nan'),1.,1.)
    elif mutation=='sum': ps[0]['a']=Prediction(.7,ps[0]['a'].top2,1.)
    else: ps[0]['a']=Prediction(.8,.2,1.)
    with pytest.raises(ValueError): m.average_predictions(ps,IDS,3)


def test_duplicate_horse_rejected():
    with pytest.raises(ValueError): m.average_predictions(members(),['a','a','b'],3)


def test_duplicate_complete_bundle_is_same_mixture():
    p,_=m.average_predictions(members(),IDS,3)
    q,_=m.average_predictions(members()*2,IDS,6)
    assert np.allclose(m.matrix_values(p,IDS),m.matrix_values(q,IDS),rtol=0,atol=1e-15)


def test_recipe_binds_member_order_and_all_heads():
    ms=[SimpleNamespace(expected_columns=['x'],recipe_hash=str(i),recipe_meta={'i':i}) for i in range(3)]
    p=m.MixtureFactory('joint_pruning3',ms); q=m.MixtureFactory('joint_pruning3',ms[::-1])
    assert p.recipe_hash != q.recipe_hash
    assert p.recipe_meta['heads']==['win','top2','top3']
    assert p.recipe_meta['post_aggregation_processing'] is None


def report(diff=-.001,candidate=2.):
    r={'evaluation_contract_version':'v4','can_adopt':False,'eligible_for_verdict':False,'n_races':23030,'n_eligible':22990,
       'periods':{p:{'candidate':candidate,'active':candidate-diff,'diff':diff} for p in ('all','recent_3y','recent_5y')},
       'total_ci':{'point':diff,'ci_low':-.1,'ci_high':.1,'n_days':715},
       'gate':{'recent_guard':True,'top_noninferior':True,'calibration':True,
               'reasons':{'top2_diff':-.0001,'top3_diff':.0001,'cand_ece':.002,'act_ece':.0018}},
       'subgroups':{'critical':['canonical','nk','recent_year_only'],
                    'subgroup_decisions':{'canonical':'PASS','nk':'NO_DECISION','recent_year_only':'INCONCLUSIVE_LOW_PRECISION'},
                    'subgroup_guard_status':'NOT_PROVEN'}}
    refresh(r); return r


def refresh(r): r['research_disposition']=m.a.s.research.assess_research(r)


def reports():
    return {c['id']: report(candidate={'joint_pruning3': 2., 'joint_mixed6': 1.999}[c['candidate']]) for c in m.COMPARISONS}


def test_small_increment_kept_despite_zero_crossing_ci():
    r=m.summarize_reports(reports())
    assert r['preferred_research_configuration']=='joint_mixed6'
    assert r['retained_candidates']==list(m.MEMBERS)
    assert r['new_coefficient_fits']==0 and r['can_adopt'] is False


def test_branch_alternative_kept_when_does_not_beat_mixed6():
    rs=reports()
    for c in m.MEMBERS: rs[f'{c}_vs_mixed6']=report(diff=.000001)
    r=m.summarize_reports(rs)
    assert r['retained_candidates']==['joint_pruning3']
    assert r['preferred_research_configuration']=='mixed6'


def test_blocked_mixed6_quality_remains_visible_for_retained_alternative():
    rs=reports()
    for c in m.MEMBERS:
        rs[f'{c}_vs_mixed6']['gate']['calibration']=False
        refresh(rs[f'{c}_vs_mixed6'])
    r=m.summarize_reports(rs)
    assert r['retained_candidates']==['joint_pruning3']
    assert r['preferred_research_configuration']=='mixed6'
    assert set(r['blocked_comparisons'])=={f'{c}_vs_mixed6' for c in m.MEMBERS}


@pytest.mark.parametrize('fault',['quality','numeric','missing_flag','missing_comparison','critical'])
def test_quality_and_incomplete_evidence_prevent_promotion(fault):
    rs=reports(); r=rs['joint_mixed6_vs_pruning3']
    if fault=='quality': r['gate']['top_noninferior']=False
    elif fault=='numeric': r['gate']['reasons']['top3_diff']=.0006
    elif fault=='missing_flag': r['gate']['calibration']=None
    elif fault=='missing_comparison': del rs['joint_pruning3_vs_anchor42']
    else: r['subgroups']['critical']=[]
    refresh(r)
    if fault in ('quality','numeric'):
        assert 'joint_mixed6' not in m.summarize_reports(rs)['retained_candidates']
    else:
        with pytest.raises(ValueError): m.summarize_reports(rs)


def test_ties_use_fixed_registration_order():
    rs={c['id']:report(candidate=2.) for c in m.COMPARISONS}
    assert m.summarize_reports(rs)['preferred_research_configuration']=='joint_pruning3'


def test_gate_scope_no_extra_noise_shrinkage_or_fit():
    cfg=m.load_config()
    assert cfg['seed_noise']['k_seeds']==1 and cfg['additional_fits']==0
    assert cfg['comparisons']==m.COMPARISONS and len(cfg['comparisons'])==6
    assert cfg['selection_rule']==m.SELECTION_RULE


def test_factory_uses_saved_joint3_and_anchor_gap3_without_coefficient_fit(monkeypatch):
    factory=lambda tag: SimpleNamespace(expected_columns=['x'], recipe_meta={'tag':tag}, recipe_hash=tag)
    monkeypatch.setattr(m.a.s,'read_json',lambda _: {})
    monkeypatch.setattr(m.a,'retained_factory',lambda matrix,races,seed: factory(f'raw{seed}'))
    monkeypatch.setattr(m.n,'verified_coefficients',lambda seed,frozen: {'seed':seed})
    monkeypatch.setattr(m.n,'JointFactory',lambda base,lookup,coef: factory(f"joint{coef['seed']}"))
    monkeypatch.setattr(m.m,'build_factory',lambda *args: SimpleNamespace(members=[factory(f'anchor{s}') for s in m.SEEDS]))
    f=m.build_factory('joint_mixed6',None,None,{('r','h'):('2026-01-01',(1.,2.,3.))})
    assert [x.recipe_hash for x in f.members]==['joint42','joint43','joint44','anchor42','anchor43','anchor44']
    assert [x['kind'] for x in f.recipe_meta['ordered_members']]==['joint_pruning']*3+['anchor_gap']*3


@pytest.fixture
def population(monkeypatch):
    import datetime as dt
    old=[{'race_id':str(i),'race_day':str(dt.date(2020,1,1)+dt.timedelta(days=i%715)),
          'active_winner_nll':3.,'candidate_winner_nll':2.8} for i in range(22990)]
    values={'joint_pruning3':2.45,'joint_mixed6':2.3,'pruning3':2.5,'mixed6':2.4,'anchor42':3.}
    def read(path):
        path=str(path)
        if path=='old-evidence': return {'rows':old}
        for name in ('pruning3','mixed6'):
            if path==f'{name}-evidence':
                return {'rows':[dict(o,candidate_winner_nll=values[name]) for o in old]}
            if Path(path).name==f'{name}_vs_anchor42.json':
                return {'evidence_path':f'{name}-evidence'}
        return {'evidence_path':'old-evidence','race_id_set_hash':'fixed'}
    monkeypatch.setattr(m.a.s,'read_json',read)
    rs,es={},{}
    for c in m.COMPARISONS:
        cv,bv=values[c['candidate']],values[c['baseline']]
        rs[c['id']]={'n_races':23030,'n_eligible':22990,'race_id_set_hash':'fixed',
                     'periods':{'all':{'candidate':cv,'active':bv,'diff':cv-bv}}}
        es[c['id']]={'rows':[{'race_id':o['race_id'],'race_day':o['race_day'],
                              'candidate_winner_nll':cv,'active_winner_nll':bv,'diff':cv-bv} for o in old]}
    return rs,es


def test_exact_old_population_and_all_three_baselines(population):
    assert m.validate_population(*population)['old116_and120_baselines_exact']


@pytest.mark.parametrize('fault',['old_baseline','candidate','mixture_baseline','horse_population','day','hash','all_hashes'])
def test_any_row_or_original_full_hash_substitution_rejected(population,fault):
    rs,es=population
    row=es['joint_mixed6_vs_anchor42']['rows'][0]
    if fault=='old_baseline': row['active_winner_nll']+=.01
    elif fault=='candidate': row['candidate_winner_nll']+=.01
    elif fault=='mixture_baseline': es['joint_mixed6_vs_mixed6']['rows'][0]['active_winner_nll']+=.01
    elif fault=='horse_population': row['race_id']='unexpected'
    elif fault=='day': row['race_day']='2026-09-01'
    elif fault=='hash': rs['joint_mixed6_vs_anchor42']['race_id_set_hash']='changed'
    else:
        for r in rs.values(): r['race_id_set_hash']='same_but_wrong'
    with pytest.raises(ValueError): m.validate_population(rs,es)


@pytest.mark.parametrize('field',['status','method_sha256','summary_sha256','run_freeze_sha256','report_hashes','can_adopt','eligible_for_verdict'])
def test_upstream_independent_review_cannot_be_substituted(monkeypatch,field):
    hashes={'x':'report'}
    r={'status':'PASS','method_sha256':'digest','summary_sha256':'digest','run_freeze_sha256':'digest',
       'report_hashes':hashes,'can_adopt':False,'eligible_for_verdict':False}
    r[field]='wrong'
    monkeypatch.setattr(m.a.s,'read_json',lambda _:r)
    monkeypatch.setattr(m.a.s.p,'digest',lambda _:'digest')
    with pytest.raises(ValueError,match='review binding'): m.reviewed_study(m.n,hashes)
