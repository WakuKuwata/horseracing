import datetime as dt
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pruning_2026_diagnostic as p


def frame(groups, values, days=None):
    f = pd.DataFrame({'group': groups, 'race_day': days or [f'2026-01-{i + 1:02d}' for i in range(len(values))]})
    for k in p.METRICS:
        f[k] = values
        for s in p.SEEDS:
            f[f'{k}_{s}'] = values
    return f


def evidence():
    def row(active):
        return {'race_id': 'r', 'race_day': '2026-01-01', 'candidate_winner_nll': 1.,
                'active_winner_nll': active, 'diff': 1. - active}
    return {s: {'anchor': {'rows': [row(1.2)]}, 'increment': {'rows': [row(1.1)]}} for s in p.SEEDS}


def test_direct_pruning_loss_and_sign():
    r = p.paired_rows(evidence()).iloc[0]
    assert r.pruning == pytest.approx(-.1)
    assert r.correction == pytest.approx(-.1)
    assert r.total == pytest.approx(-.2)


@pytest.mark.parametrize('change', ['seed', 'order', 'candidate', 'nonfinite', 'duplicate'])
def test_bad_paired_evidence_is_rejected(change):
    e = evidence()
    if change == 'seed':
        del e[44]
    elif change == 'order':
        e[43]['increment']['rows'][0]['race_id'] = 'wrong'
    elif change == 'candidate':
        e[43]['anchor']['rows'][0]['candidate_winner_nll'] = 2.
    elif change == 'nonfinite':
        e[42]['anchor']['rows'][0]['diff'] = np.nan
    else:
        e[42]['anchor']['rows'] *= 2
        e[42]['increment']['rows'] *= 2
    with pytest.raises(ValueError):
        p.paired_rows(e)


@pytest.mark.parametrize('value,expected', [(0, '0'), (.5, '(0,.5]'), (.6, '(.5,1)'), (1, '1'), (np.nan, 'unknown')])
def test_fraction_edges_and_unknown(value, expected):
    assert p.share_band(value) == expected


def test_fraction_out_of_range_rejected():
    with pytest.raises(ValueError):
        p.share_band(1.001)


def test_composition_change_only():
    ref = frame(['a', 'b'], [0., 1.])
    target = frame(['a', 'b', 'b'], [0., 1., 1.])
    r = p.composition(ref, target, 'group')
    assert r['composition'] == pytest.approx(1 / 6)
    assert r['within'] == 0
    assert r['unmatched'] == 0


def test_within_change_only_and_unmatched_cells():
    ref = frame(['a', 'b'], [0., 1.])
    target = frame(['a', 'b'], [1., 2.])
    r = p.composition(ref, target, 'group')
    assert r['composition'] == 0
    assert r['within'] == 1
    target = frame(['a', 'c'], [0., 3.])
    r = p.composition(ref, target, 'group')
    assert r['unmatched'] == 1
    assert r['target_unmatched_share'] == .5
    assert r['reference_unmatched_share'] == .5
    assert r['delta'] == sum(r[k] for k in ['composition', 'within', 'unmatched'])


def test_contributions_are_population_weighted_with_unknown_preserved():
    r = p.split_records(frame(['unknown', 'a', 'a'], [3., 0., 0.]), 'group')
    assert r['unknown']['metrics']['pruning']['contribution'] == 1
    assert sum(x['n_races'] for x in r.values()) == 3


def test_sensitivity_removes_whole_days_not_individual_races():
    f = frame(['a'] * 7, [10., 10., -2., -2., -2., -2., -2.],
              ['2026-01-01'] * 2 + [f'2026-01-{i:02d}' for i in range(2, 7)])
    result = p.sensitivity(f)
    r = result['remove_worst_days_posthoc']['1']
    assert r['excluded_days'] == ['2026-01-01']
    assert r['remaining']['n_races'] == 5
    assert r['remaining']['metrics']['pruning']['mean'] == -2
    assert len(f) == 7


def test_normalized_class_and_fixed_bins():
    assert p.class_name('１勝') == p.class_name('500万') == '1勝'
    assert p.class_name(None) == 'unknown'
    assert p.numeric_band(1400, [1400, 1800, 2200], ['a', 'b', 'c', 'd']) == 'b'


def test_missing_distributions_stay_missing():
    f = pd.DataFrame({'race_id': ['a', 'a'], 'feature': [np.nan, np.nan]})
    r = p.distribution(f, ['feature'])['feature']
    assert r['missing_rate'] == 1
    assert r['p10_p50_p90'] is None
    assert r['race_sd_p50_p90'] is None


def horse_frame():
    return pd.DataFrame({'race_id': ['r', 'r'], 'horse_id': ['canonical', 'nk:x'],
        'race_date': [dt.date(2026, 1, 1)] * 2, 'venue_code': ['01'] * 2,
        'track_type': ['芝'] * 2, 'distance': [1400] * 2, 'field_size': [2] * 2,
        'race_class': ['１勝'] * 2, 'going': [None] * 2, 'is_debut': [0, 1],
        'relative': [1., np.nan], 'rel_time_avg': [1., np.nan]})


def test_attributes_use_whole_started_field_and_keep_unknown():
    rows, horses = p.attach_attributes(p.paired_rows(evidence()), horse_frame(), ['relative'])
    assert rows.iloc[0].nk_share == '(0,.5]'
    assert rows.iloc[0].relative_coverage == '(0,.5]'
    assert rows.iloc[0].going == 'unknown'
    assert rows.iloc[0].race_class == '1勝'
    assert len(horses) == 2


@pytest.mark.parametrize('change', ['date', 'duplicate', 'field_size', 'nonconstant', 'inf', 'debut', 'bad_distance', 'infinite_distance'])
def test_attribute_join_fails_on_bad_source(change):
    h = horse_frame()
    if change == 'date':
        h['race_date'] = dt.date(2026, 1, 2)
    elif change == 'duplicate':
        h['horse_id'] = 'same'
    elif change == 'field_size':
        h['field_size'] = 3
    elif change == 'nonconstant':
        h.loc[1, 'distance'] = 1200
    elif change == 'inf':
        h.loc[0, 'relative'] = np.inf
    elif change == 'bad_distance':
        h['distance'] = -10
    elif change == 'infinite_distance':
        h['distance'] = np.inf
    else:
        h.loc[0, 'is_debut'] = np.nan
    with pytest.raises(ValueError):
        p.attach_attributes(p.paired_rows(evidence()), h, ['relative'])


def test_same_count_different_horses_rejected():
    with pytest.raises(ValueError, match='horse ID'):
        p.attach_attributes(p.paired_rows(evidence()), horse_frame(), ['relative'], {'r': {'wrong', 'nk:x'}})
