from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probability_mixture_recheck as m
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
    p=m.MixtureFactory('pruning3',ms); q=m.MixtureFactory('pruning3',ms[::-1])
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


def reports(): return {c['id']:report(candidate={'pruning3':2.,'anchor3':1.9999,'mixed6':1.999}[c['candidate']]) for c in m.COMPARISONS}


def test_small_increments_retained_without_ci_superiority():
    x=m.summarize_reports(reports())
    assert x['preferred_research_configuration']=='mixed6'
    assert x['retained_candidates']==list(m.MEMBERS)
    assert x['bundle_replicates']==1 and x['can_adopt'] is False


def test_nonimproving_increment_keeps_alternative_without_preference():
    rs=reports()
    for c in ('anchor3','mixed6'): rs[f'{c}_vs_pruning3']=report(diff=.00001)
    x=m.summarize_reports(rs)
    assert x['retained_candidates']==list(m.MEMBERS)
    assert x['preferred_research_configuration']=='pruning3'


@pytest.mark.parametrize('failure',['flag','numeric','incomplete','missing_comparison','critical'])
def test_quality_and_missing_evidence_never_hidden(failure):
    rs=reports(); r=rs['mixed6_vs_retained42']
    if failure=='flag': r['gate']['calibration']=False
    elif failure=='numeric': r['gate']['reasons']['top3_diff']=.0006
    elif failure=='incomplete': r['gate']['recent_guard']=None
    elif failure=='missing_comparison': del rs['anchor3_vs_pruning3']
    else: r['subgroups']['critical']=[]
    refresh(r)
    if failure in ('flag','numeric'):
        assert 'mixed6' not in m.summarize_reports(rs)['retained_candidates']
    else:
        with pytest.raises(ValueError): m.summarize_reports(rs)


def test_noise_not_divided_by_member_count():
    cfg=m.load_config()
    assert cfg['seed_noise']['k_seeds']==1
    assert len(cfg['comparisons'])==8
    assert cfg['additional_fits']==0


@pytest.fixture
def population(monkeypatch):
    import datetime as dt
    old=[{'race_id':str(i),'race_day':str(dt.date(2020,1,1)+dt.timedelta(days=i%715)),
          'active_winner_nll':3.,'candidate_winner_nll':2.8} for i in range(22990)]
    monkeypatch.setattr(m.a.s,'read_json',lambda p: {'rows':old} if str(p)=='old-evidence' else {'evidence_path':'old-evidence','race_id_set_hash':'fixed'})
    values={'pruning3':2.5,'anchor3':2.6,'mixed6':2.4,'retained42':2.8,'anchor42':3.}
    rs,es={},{}
    for c in m.COMPARISONS:
        cv,bv=values[c['candidate']],values[c['baseline']]
        rs[c['id']]={'n_races':23030,'n_eligible':22990,'race_id_set_hash':'fixed',
                     'periods':{'all':{'candidate':cv,'active':bv,'diff':cv-bv}}}
        es[c['id']]={'rows':[{'race_id':o['race_id'],'race_day':o['race_day'],
                              'candidate_winner_nll':cv,'active_winner_nll':bv,'diff':cv-bv} for o in old]}
    return rs,es


def test_full_population_and_all_baseline_roles_match(population):
    result=m.validate_population(*population)
    assert result['old_single_seed_baselines_exact'] and result['mixture_identity_across_comparisons_exact']


def test_changed_common_full_race_hash_is_rejected_against_original(population):
    rs,es=population
    for r in rs.values(): r['race_id_set_hash']='all_reports_same_but_wrong'
    with pytest.raises(ValueError,match='hashes'): m.validate_population(rs,es)


@pytest.mark.parametrize('failure',['old_baseline','candidate','mixture_baseline','horse_population','day','hash'])
def test_any_single_row_or_baseline_substitution_is_rejected(population,failure):
    rs,es=population
    row=es['mixed6_vs_retained42']['rows'][0]
    if failure=='old_baseline': row['active_winner_nll']+=.01
    elif failure=='candidate': row['candidate_winner_nll']+=.01
    elif failure=='mixture_baseline': es['anchor3_vs_pruning3']['rows'][0]['active_winner_nll']+=.01
    elif failure=='horse_population': row['race_id']='unexpected'
    elif failure=='day': row['race_day']='2026-09-01'
    else: rs['mixed6_vs_retained42']['race_id_set_hash']='changed'
    with pytest.raises(ValueError): m.validate_population(rs,es)
