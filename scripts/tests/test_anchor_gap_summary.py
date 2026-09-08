from copy import deepcopy
import datetime as dt
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anchor_gap_summary as g


def report(diff=-.001):
    r = {'evaluation_contract_version': 'v4', 'can_adopt': False, 'eligible_for_verdict': False,
         'n_races': 23030, 'n_eligible': 22990, 'race_id_set_hash': 'same',
         'periods': {p: {'candidate': 2., 'active': 2. - diff, 'diff': diff} for p in g.PERIODS},
         'total_ci': {'point': diff, 'ci_low': -.1, 'ci_high': .1, 'n_days': 715},
         'gate': {'recent_guard': True, 'top_noninferior': True, 'calibration': True,
                  'reasons': {'top2_diff': -.0001, 'top3_diff': .0001, 'cand_ece': .002, 'act_ece': .0018}},
         'subgroups': {'critical': ['canonical', 'nk', 'recent_year_only'],
             'subgroup_decisions': {'canonical': 'PASS', 'nk': 'NO_DECISION', 'recent_year_only': 'INCONCLUSIVE_LOW_PRECISION'},
             'subgroup_guard_status': 'NOT_PROVEN', 'critical_residual_risk': {'nk': .0013}}}
    refresh(r)
    return r


def refresh(r):
    r['research_disposition'] = g.research.assess_research(r)


def reports(anchor=(-.001,) * 3, retained=(-.001,) * 3):
    return {s: {'anchor': report(a), 'retained': report(b)} for s, a, b in zip(g.SEEDS, anchor, retained, strict=True)}


def test_both_means_improve_promotes_research_preference_without_ci_requirement():
    r = g.summarize_reports(reports())
    assert r['research_decision'] == 'ANCHOR_GAP_PREFERRED'
    assert r['candidate_retention'] == 'RETAIN'
    assert r['preferred_research_configuration'] == 'ANCHOR_GAP'
    assert r['deployment_seed'] == 42
    assert r['can_adopt'] is r['eligible_for_verdict'] is False
    assert r['upstream116_verdict_modified'] is False


@pytest.mark.parametrize('retained', [0., .002])
def test_positive_anchor_effect_retains_alternative_even_if_not_preferred(retained):
    r = g.summarize_reports(reports(retained=(retained,) * 3))
    assert r['research_decision'] == 'ANCHOR_GAP_ALTERNATIVE_RETAINED'
    assert r['candidate_retention'] == 'RETAIN'
    assert r['preferred_research_configuration'] == 'PRUNING_GAP'


def test_cannot_prefer_without_improvement_over_anchor():
    r = g.summarize_reports(reports(anchor=(0.,) * 3, retained=(-.001,) * 3))
    assert r['research_decision'] == 'ANCHOR_GAP_DEFERRED'
    assert r['candidate_retention'] == 'DEFER'


def test_no_majority_or_all_seeds_condition():
    r = g.summarize_reports(reports(anchor=(-.01, .001, .001), retained=(-.01, .001, .001)))
    assert r['research_decision'] == 'ANCHOR_GAP_PREFERRED'
    assert r['contrasts']['anchor']['negative_seed_count_descriptive_only'] == 1


@pytest.mark.parametrize('contrast', ['anchor', 'retained'])
def test_single_seed_failed_quality_not_hidden_by_mean(contrast):
    r = reports()
    x = r[43][contrast]
    x['gate']['reasons']['top3_diff'] = .0006
    x['gate']['top_noninferior'] = False
    refresh(x)
    result = g.summarize_reports(r)
    assert result['research_decision'] == 'REVIEW_REQUIRED'
    assert result['candidate_retention'] == 'REVIEW_REQUIRED'
    assert result['contrasts'][contrast]['mean_quality_pass'] is True
    assert result['contrasts'][contrast]['blocked_seeds'] == [43]


def test_numeric_violation_detected_even_if_report_flag_wrong():
    r = reports()
    r[44]['retained']['gate']['reasons']['top3_diff'] = .0006
    refresh(r[44]['retained'])
    assert g.summarize_reports(r)['research_decision'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('value', [None, 'false', 0])
def test_missing_or_nonboolean_quality_flag_cannot_be_averaged(value):
    r = reports()
    r[42]['anchor']['gate']['recent_guard'] = value
    refresh(r[42]['anchor'])
    with pytest.raises(ValueError, match='booleans'):
        g.summarize_reports(r)


def test_fixed_critical_groups_cannot_be_removed():
    r = reports()
    r[42]['anchor']['subgroups']['critical'] = []
    refresh(r[42]['anchor'])
    with pytest.raises(ValueError, match='critical subgroups'):
        g.summarize_reports(r)


@pytest.mark.parametrize('bad', ['missing_seed', 'missing_contrast', 'nonfinite', 'missing_ci', 'source_disposition', 'promotion', 'missing_subgroup'])
def test_invalid_evidence_stops_aggregation(bad):
    r = reports()
    x = r[42]['anchor']
    if bad == 'missing_seed':
        del r[43]
    elif bad == 'missing_contrast':
        del r[43]['retained']
    elif bad == 'nonfinite':
        x['gate']['reasons']['top3_diff'] = float('nan')
        refresh(x)
    elif bad == 'missing_ci':
        del x['total_ci']['ci_high']
        refresh(x)
    elif bad == 'source_disposition':
        x['research_disposition']['state'] = 'DEFER'
    elif bad == 'promotion':
        x['can_adopt'] = True
    else:
        x['subgroups']['subgroup_decisions']['nk'] = 'MISSING'
        refresh(x)
    with pytest.raises(ValueError):
        g.summarize_reports(r)


@pytest.fixture(scope='module')
def payload():
    old, evidence, attrs = {}, {}, []
    for i in range(22990):
        day = (dt.date(2026, 1, 1) + dt.timedelta(days=i % (25 if i < 300 else 70)) if i < 2296 else
               dt.date(2020, 1, 1) + dt.timedelta(days=(i - 2296) % 645))
        attrs.append({'race_id': f'r{i}', 'race_day': str(day), 'year': day.year,
                      'venue': '06' if i < 300 else '05', 'relative_coverage': '(.5,1)' if i < 1538 else '1'})
    for s in g.SEEDS:
        old[s] = {'rows': [{'race_id': x['race_id'], 'race_day': x['race_day'],
                           'candidate_winner_nll': 2.002, 'active_winner_nll': 2.01} for x in attrs]}
        evidence[s] = {name: {'rows': [{'race_id': x['race_id'], 'race_day': x['race_day'], 'candidate_winner_nll': 2.,
            'active_winner_nll': baseline, 'diff': 2. - baseline} for x in attrs]}
            for name, baseline in [('anchor', 2.01), ('retained', 2.002)]}
    return reports(anchor=(-.01,) * 3, retained=(-.002,) * 3), evidence, old, attrs


def test_full_population_and_both_original_baselines(payload):
    r, e, old, _ = payload
    result = g.validate_population(r, e, old)
    assert result['n_eligible'] == 22990
    assert result['all_new_baseline_losses_equal_original116']


@pytest.mark.parametrize('bad', ['baseline', 'candidate', 'race_order', 'date', 'count'])
def test_population_and_baseline_substitution_rejected(payload, bad):
    r, e, old, _ = deepcopy(payload)
    row = e[43]['retained']['rows'][0]
    if bad == 'baseline':
        row['active_winner_nll'] += .01
        row['diff'] = row['candidate_winner_nll'] - row['active_winner_nll']
    elif bad == 'candidate':
        row['candidate_winner_nll'] += .01
        row['diff'] = row['candidate_winner_nll'] - row['active_winner_nll']
    elif bad == 'race_order':
        e[43]['retained']['rows'].reverse()
    elif bad == 'date':
        row['race_day'] = '2027-01-01'
    else:
        r[43]['retained']['n_eligible'] = 22989
    with pytest.raises(ValueError):
        g.validate_population(r, e, old)


def test_fixed_diagnostic_groups_are_not_additional_gates(payload):
    _, e, _, attrs = payload
    r = g.fixed_diagnostics(e, attrs)
    assert r['extra_quality_gate'] is False
    assert r['new_ci'] is False
    assert r['groups']['2026_nakayama']['n_races'] == 300
    assert r['groups']['2026_partial_relative']['n_days'] == 70
    assert r['groups']['2026_all']['contrasts']['anchor']['equal_seed_means']['diff']['mean'] == pytest.approx(-.01)


def test_diagnostic_attributes_cannot_be_substituted(payload):
    _, e, _, attrs = deepcopy(payload)
    attrs[0]['venue'] = '05'
    with pytest.raises(ValueError):
        g.fixed_diagnostics(e, attrs)
