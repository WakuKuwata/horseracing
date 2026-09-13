"""Independent recent audit math on synthetic data only."""
import datetime as dt
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_mixture_recent_inputs_135 as audit


def test_inverse_set_handles_collisions_and_prior_id_union():
    old = SimpleNamespace(prior=.1, mapping={'a': .2, 'b': .2, 'c': .1})
    new = SimpleNamespace(prior=.4, mapping={'a': .3, 'b': .3, 'c': .6, 'new': .7})
    assert audit.possible_new_values(.2, old, new) == {.3}
    assert audit.possible_new_values(.1, old, new) == {.4, .6, .7}
    for value in [float('nan'), float('inf'), True, None, '.2', .2+1e-15]:
        assert audit.possible_new_values(value, old, new) == set()


@pytest.mark.parametrize('n', [1, 2, 3, 7])
def test_ordered_head_enumeration_preserves_probability_mass(n):
    p = np.arange(1, n+1, dtype=float); p /= p.sum()
    heads = audit.ordered_heads(p, .75, 1.15)
    assert np.array_equal(heads[:, 0], p)
    assert np.allclose(heads.sum(axis=0), np.minimum([1, 2, 3], n), atol=2e-15, rtol=0)
    assert (np.diff(heads, axis=1) >= -1e-15).all()


def test_three_equal_horses_give_exact_topk():
    assert np.allclose(audit.ordered_heads(np.full(3, 1/3), .6, 1.7), [[1/3, 2/3, 1]]*3, atol=1e-15)


def test_correction_uses_ordered_identity_and_excludes_same_day_future():
    day = dt.date(2026, 9, 6)
    rows = pd.DataFrame({'horse_id':['b', 'a'], 'race_date':[day]*2,
                         'days_since_last':[4, 8], 'sex':['牝', '牡']})
    history = pd.DataFrame({'horse_id':['a','a','b','b','b','b'],
        'race_date':[dt.date(2026, 8, 1),dt.date(2026, 8, 3),dt.date(2026, 8, 1),
                     dt.date(2026, 8, 9),day,dt.date(2026, 9, 12)]})
    p = np.array([.6, .4])
    actual = audit.corrected_win(p, rows, history, ['prior_gap_log'], [1.])
    expected = p*np.array([9., 3.]); expected /= expected.sum()
    assert np.allclose(actual, expected, atol=1e-15, rtol=0)


def test_pickle_digest_checked_before_deserialization(tmp_path, monkeypatch):
    p = tmp_path/'fake.pkl'; p.write_bytes(b'not a pickle')
    monkeypatch.setattr(audit.pickle, 'load', lambda *_: pytest.fail('unpickle before hash gate'))
    with pytest.raises(AssertionError):
        audit.sealed(p, {str(p): 'different'})


def test_booster_entry_requests_raw_margin_then_softmax_calibration_clip():
    class Booster:
        def predict(self, x, *, raw_score, num_threads):
            assert raw_score is True and num_threads == 1
            assert isinstance(x.sex.dtype, pd.CategoricalDtype)
            return np.log([1., 2., 7.])
    class Calibrator:
        def transform(self, values):
            np.testing.assert_allclose(values, [.1, .2, .7], atol=1e-15, rtol=0)
            return np.array([0., .25, 1.])
    actual = audit.booster_win(Booster(), Calibrator(), pd.DataFrame({'sex':['牡','牝','セ']}), ['sex'], ['sex'])
    expected = np.array([1e-6, .25, 1-1e-6]); expected /= expected.sum()
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('extra', ['arm', 'race', 'date', 'lambda'])
def test_prediction_envelope_rejects_extra_population_or_arm(extra):
    saved = {'ids': {'r': ('a',)}, 'dates': {'r': dt.date(2026, 8, 29)},
             'predictions': {regime: {arm: {'r': [[1., 1., 1.]]} for arm in ('baseline', 'candidate')}
                             for regime in ('full', 'preweight')},
             'lambdas': {'full': {}, 'preweight': {}}}
    audit.prediction_envelope(saved, {'r'})
    if extra == 'arm':
        saved['predictions']['full']['extra'] = {'r': [[1., 1., 1.]]}
    elif extra == 'race':
        saved['predictions']['preweight']['candidate']['extra'] = [[1., 1., 1.]]
    elif extra == 'date':
        saved['dates']['extra'] = dt.date(2026, 8, 29)
    else:
        saved['lambdas']['extra'] = {}
    with pytest.raises(AssertionError):
        audit.prediction_envelope(saved, {'r'})
