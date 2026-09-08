from copy import deepcopy
import datetime as dt
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gap_seed_summary as g

def report(diff=-.001):
    r = {'evaluation_contract_version': 'v4', 'can_adopt': False, 'eligible_for_verdict': False,
         'n_races': 23030, 'n_eligible': 22990, 'race_id_set_hash': 'shared',
         'periods': {k: {'candidate': 2., 'active': 2. - diff, 'diff': diff}
                     for k in ('all', 'recent_3y', 'recent_5y')},
         'total_ci': {'point': diff, 'ci_low': -.1, 'ci_high': .1, 'n_days': 715},
         'gate': {'recent_guard': True, 'top_noninferior': True, 'calibration': True,
                  'reasons': {'top2_diff': -.0001, 'top3_diff': .0001, 'cand_ece': .002, 'act_ece': .0018}},
         'subgroups': {'critical': ['canonical', 'nk', 'recent_year_only'],
                       'subgroup_decisions': {'canonical': 'PASS', 'nk': 'NO_DECISION', 'recent_year_only': 'INCONCLUSIVE_LOW_PRECISION'},
                       'subgroup_guard_status': 'NOT_PROVEN', 'critical_residual_risk': {'nk': .0013}}}
    r['research_disposition'] = g.research.assess_research(r)
    return r

def reports(diffs=(-.001, -.002, -.003)):
    return {s: {c: report(d) for c in g.CONTRAST_IDS} for s, d in zip(g.SEEDS, diffs, strict=True)}

def refresh(r):
    r['research_disposition'] = g.research.assess_research(r)

def test_mean_gain_kept_without_all_seeds_or_majority_improving():
    result = g.summarize_reports(reports((-.01, .001, .001)))
    assert result['research_decision'] == 'SEED_MEAN_IMPROVEMENT_RETAINED'
    r = result['contrasts']['anchor']
    assert r['negative_seed_count_descriptive_only'] == 1
    assert r['equal_seed_mean_periods']['all']['diff']['mean'] == pytest.approx(-.008 / 3)
    assert r['seed_results']['44']['research_state'] == 'DEFER'
    assert result['deployment_seed'] == 42
    assert not result['can_adopt'] and not result['eligible_for_verdict']

def test_zero_mean_is_not_improvement():
    assert g.summarize_reports(reports((-.001, 0., .001)))['research_decision'] == 'MEAN_IMPROVEMENT_NOT_REPRODUCED'

def test_tiny_negative_mean_is_retained():
    assert g.summarize_reports(reports((-1e-10,) * 3))['research_decision'] == 'SEED_MEAN_IMPROVEMENT_RETAINED'

def test_one_contrast_mean_nonnegative_prevents_combined_retention():
    data = reports()
    for s in g.SEEDS: data[s]['increment'] = report(.001)
    assert g.summarize_reports(data)['research_decision'] == 'MEAN_IMPROVEMENT_NOT_REPRODUCED'

def test_single_seed_quality_failure_not_hidden_by_mean():
    data = reports()
    for s in g.SEEDS:
        data[s]['anchor']['gate']['reasons']['top3_diff'] = -.001
        refresh(data[s]['anchor'])
    data[44]['anchor']['gate']['reasons']['top3_diff'] = .001
    data[44]['anchor']['gate']['top_noninferior'] = False
    refresh(data[44]['anchor'])
    result = g.summarize_reports(data)
    assert result['research_decision'] == 'REVIEW_REQUIRED'
    assert result['contrasts']['anchor']['mean_quality_pass']
    assert result['contrasts']['anchor']['seeds_with_blocked_quality_or_evidence'] == [44]

def test_uncertain_subgroups_are_kept_visible_without_veto():
    result = g.summarize_reports(reports())
    assert result['research_decision'] == 'SEED_MEAN_IMPROVEMENT_RETAINED'
    row = result['contrasts']['anchor']['seed_results']['42']
    assert row['critical_subgroup_states']['nk'] == 'NO_DECISION'
    assert row['critical_residual_risk']['nk'] == .0013
    assert row['subgroup_status'] == 'NOT_PROVEN'

def test_original_recent_and_all_subgroup_intervals_preserved_without_alias():
    data = reports()
    recent = {'windows': {'recent_3y': {'ci_low': -.003, 'ci_high': .007}}}
    data[42]['anchor']['gate']['reasons']['recent'] = recent
    data[42]['anchor']['subgroups']['race_subgroups'] = {'canonical': {'bootstrap_ci': {'ci_low': -.01, 'ci_high': .01}}}
    refresh(data[42]['anchor'])
    row = g.summarize_reports(data)['contrasts']['anchor']['seed_results']['42']
    assert row['recent_guard_evidence'] == recent
    assert row['subgroup_evidence'] == data[42]['anchor']['subgroups']
    row['recent_guard_evidence']['windows'].clear()
    assert data[42]['anchor']['gate']['reasons']['recent']['windows']

def test_dispersion_is_sample_sd_not_standard_error_or_ci():
    s = g.stats([-.001, -.002, -.003])
    assert s['sample_sd_descriptive'] == pytest.approx(.001)
    assert set(s) == {'mean', 'sample_sd_descriptive', 'min', 'max'}

@pytest.mark.parametrize('value', [None, True, float('inf'), float('nan'), '-.1'])
def test_non_numeric_aggregation_fails_closed(value):
    with pytest.raises(ValueError): g.stats([-.1, -.2, value])

def test_missing_seed_or_contrast_never_averages_subset():
    data = reports(); data.pop(44)
    with pytest.raises(ValueError): g.summarize_reports(data)
    data = reports(); data[44].pop('increment')
    with pytest.raises(ValueError): g.summarize_reports(data)

def test_falsified_source_disposition_is_rejected():
    data = reports(); data[42]['anchor']['research_disposition']['state'] = 'RETAIN_SUPPORTED'
    with pytest.raises(ValueError, match='disposition'): g.summarize_reports(data)

def test_candidate_must_be_identical_between_contrasts():
    data = reports(); p = data[44]['increment']['periods']['recent_3y']
    p['candidate'] += .001; p['active'] += .001
    refresh(data[44]['increment'])
    with pytest.raises(ValueError, match='arithmetic'): g.summarize_reports(data)

@pytest.fixture
def aligned_data():
    rows = [{'race_id': str(i), 'race_day': str(dt.date(2020, 1, 1) + dt.timedelta(days=i % 715)),
             'candidate_winner_nll': 2., 'active_winner_nll': 2.001, 'diff': -.001} for i in range(22990)]
    data = reports((-.001,) * 3)
    evidence = {s: {c: {'rows': rows} for c in g.CONTRAST_IDS} for s in g.SEEDS}
    return data, evidence

def test_all_six_populations_and_actual_mean_losses_match(aligned_data):
    data, evidence = aligned_data
    assert g.validate_population(data, evidence)['eligible_races'] == 22990

@pytest.mark.parametrize('mode', ['day', 'duplicate', 'candidate', 'count', 'all_race_hash'])
def test_population_or_loss_change_rejected(aligned_data, mode):
    data, evidence = deepcopy(aligned_data)
    evidence[44]['anchor'] = deepcopy(evidence[44]['anchor'])
    rows = evidence[44]['anchor']['rows']
    if mode == 'day': rows[0]['race_day'] = '2019-12-31'
    elif mode == 'duplicate': rows[0]['race_id'] = rows[1]['race_id']
    elif mode == 'candidate': rows[0]['candidate_winner_nll'] += .01
    elif mode == 'count': rows.pop()
    else: data[44]['anchor']['race_id_set_hash'] = 'different'
    with pytest.raises(ValueError): g.validate_population(data, evidence)
