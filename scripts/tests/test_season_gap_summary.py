"""119 retention has a two-comparison rule; fixed diagnostics never alter it."""
from copy import deepcopy
import datetime as dt
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import season_gap_summary as s


def refresh(r):
    r['research_disposition'] = s.checks.research.assess_research(r)


def report(diff):
    r = {'evaluation_contract_version': 'v4', 'can_adopt': False, 'eligible_for_verdict': False,
         'n_races': 23030, 'n_eligible': 22990, 'race_id_set_hash': 'same',
         'periods': {p: {'candidate': 2., 'active': 2. - diff, 'diff': diff} for p in s.checks.PERIODS},
         'total_ci': {'point': diff, 'ci_low': -.1, 'ci_high': .1, 'n_days': 715},
         'gate': {'recent_guard': True, 'top_noninferior': True, 'calibration': True,
                  'reasons': {'top2_diff': -.0001, 'top3_diff': .0001, 'cand_ece': .002, 'act_ece': .0018}},
         'subgroups': {'critical': ['canonical', 'nk', 'recent_year_only'],
             'subgroup_decisions': {'canonical': 'PASS', 'nk': 'NO_DECISION', 'recent_year_only': 'INCONCLUSIVE_LOW_PRECISION'},
             'subgroup_guard_status': 'NOT_PROVEN', 'critical_residual_risk': {'nk': .0013}}}
    refresh(r)
    return r


def reports(anchor=(-.001,) * 3, retained=(-.001,) * 3):
    return {seed: {'anchor': report(x), 'retained': report(y)}
            for seed, x, y in zip(s.SEEDS, anchor, retained, strict=True)}


def test_both_means_improve_retains_increment():
    result = s.summarize_reports(reports())
    assert result['research_decision'] == 'SEASON_GAP_RETAINED'
    assert result['preferred_research_configuration'] == 'JOINT_SEASON_GAP'
    assert result['can_adopt'] is False and result['eligible_for_verdict'] is False


@pytest.mark.parametrize('which', ['anchor', 'retained'])
@pytest.mark.parametrize('value', [0., .001])
def test_either_nonimproving_mean_defers_season_increment(which, value):
    kw = {which: (value,) * 3}
    result = s.summarize_reports(reports(**kw))
    assert result['research_decision'] == 'SEASON_GAP_DEFERRED'
    assert result['candidate_retention'] == 'DEFER'
    assert result['preferred_research_configuration'] == 'PRUNING_GAP'


def test_no_all_seed_sign_majority_or_ci_requirement():
    result = s.summarize_reports(reports(anchor=(-.01, .001, .001), retained=(-.01, .001, .001)))
    assert result['research_decision'] == 'SEASON_GAP_RETAINED'
    assert result['contrasts']['anchor']['negative_seed_count_descriptive_only'] == 1


@pytest.mark.parametrize('contrast', ['anchor', 'retained'])
def test_real_quality_failure_requires_review_even_if_mean_improves(contrast):
    r = reports()
    r[43][contrast]['gate']['recent_guard'] = False
    refresh(r[43][contrast])
    result = s.summarize_reports(r)
    assert result['research_decision'] == 'QUALITY_REVIEW_REQUIRED'
    assert result['preferred_research_configuration'] == 'UNCHANGED_PENDING_REVIEW'


@pytest.mark.parametrize('bad', ['seed', 'flag', 'number', 'critical', 'disposition'])
def test_incomplete_or_inconsistent_evidence_cannot_be_averaged(bad):
    r = reports()
    if bad == 'seed':
        r.pop(44)
    else:
        one = r[43]['anchor']
        if bad == 'flag':
            one['gate']['recent_guard'] = None
        elif bad == 'number':
            one['gate']['reasons']['cand_ece'] = float('nan')
        elif bad == 'critical':
            one['subgroups']['critical'] = []
        else:
            one['research_disposition'] = {}
        if bad != 'disposition':
            refresh(one)
    with pytest.raises(ValueError):
        s.summarize_reports(r)


@pytest.fixture
def diagnostics():
    attrs, rows = [], []
    for i in range(23030):
        day = str(dt.date(2020, 1, 1) + dt.timedelta(days=i % 715))
        eligible = i < 22990
        attrs.append({'race_id': str(i), 'race_day': day, 'year': int(day[:4]),
                      'sex_group': s.GROUPS[i % 4], 'eligible': eligible})
        if eligible:
            rows.append({'race_id': str(i), 'race_day': day, 'candidate_winner_nll': 2., 'active_winner_nll': 2.001, 'diff': -.001})
    evidence = {seed: {name: {'rows': rows} for name in s.CONTRASTS} for seed in s.SEEDS}
    return evidence, {'race_attributes': attrs}


def test_fixed_sex_groups_partition_without_new_gate(diagnostics):
    evidence, audit = diagnostics
    out = s.sex_diagnostics(evidence, audit)
    assert out['extra_quality_gate'] is False and out['new_ci'] is False
    assert sum(group['n_eligible'] for group in out['groups']['all'].values()) == 22990
    assert all(group['n_eligible'] == 0 for group in out['groups']['2026'].values())
    assert all(group['contrasts']['anchor']['equal_seed_mean']['diff'] == -.001 for group in out['groups']['all'].values())


@pytest.mark.parametrize('bad', ['identity', 'group', 'eligible', 'year'])
def test_sex_diagnostics_refuses_changed_fixed_input(diagnostics, bad):
    evidence, audit = diagnostics
    if bad == 'identity':
        audit['race_attributes'][0]['race_id'] = 'changed'
    elif bad == 'group':
        audit['race_attributes'][0]['sex_group'] = 'unknown'
    elif bad == 'eligible':
        audit['race_attributes'][0]['eligible'] = 1
    else:
        audit['race_attributes'][0]['year'] = 2026
    with pytest.raises(ValueError):
        s.sex_diagnostics(evidence, audit)


def test_all_six_matching_wrong_full_hashes_still_fail_original116_binding(monkeypatch):
    r = reports()
    monkeypatch.setattr(s.checks, 'validate_population', lambda *_: {'counts_valid': True})
    original = {seed: 'same' for seed in s.SEEDS}
    result = s.validate_population(r, {}, {}, original)
    assert result['all_full_scored_hashes_equal_original116']
    for seed in s.SEEDS:
        for name in s.CONTRASTS:
            r[seed][name]['race_id_set_hash'] = 'same_wrong_full_population'
    with pytest.raises(ValueError, match='including noneligible'):
        s.validate_population(r, {}, {}, original)
