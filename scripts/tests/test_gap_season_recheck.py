from __future__ import annotations
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gap_season_recheck as g


def frame(gaps=(7., 14., 70., 100., np.nan), sexes=('牝', '牡', 'セ', '牝', None)):
    return pd.DataFrame({'race_id': ['r'] * len(gaps), 'horse_id': [str(i) for i in range(len(gaps))],
                         'race_date': [dt.date(2020, 1, 1)] * len(gaps),
                         'days_since_last': gaps, 'sex': sexes})


def test_gap_shapes_and_missing():
    h = g.candidate_matrix(frame())
    np.testing.assert_allclose(h[:4, 0], np.log1p([7, 14, 70, 100]))
    np.testing.assert_allclose(h[:4, 1], [7, 0, 0, 0])
    np.testing.assert_allclose(h[:4, 2], [0, 0, 0, 30])
    assert np.isnan(h[4]).all()
    np.testing.assert_allclose(h[:4, 3:], [[0, 1], [0, 0], [0, 0], [0, 1]])


@pytest.mark.parametrize('bad', [-1., 0., .5, np.inf, -np.inf])
def test_bad_gap_rejected(bad):
    with pytest.raises(ValueError):
        g.candidate_matrix(frame((bad,), ('牝',)))


def test_sex_encoding_fail_closed():
    with pytest.raises(ValueError, match='sex'):
        g.candidate_matrix(frame((10.,), ('female',)))


def test_duplicate_identity_rejected():
    f = frame()
    f.loc[1, 'horse_id'] = f.loc[0, 'horse_id']
    with pytest.raises(ValueError, match='duplicate'):
        g.candidate_matrix(f)


@pytest.mark.parametrize('date, days', [(dt.date(2020, 3, 1), 366), (dt.date(2021, 3, 1), 365)])
def test_season_leap_day(date, days):
    f = frame((10.,), ('牝',))
    f['race_date'] = [date]
    theta = 2 * np.pi * (date.timetuple().tm_yday - 1) / days
    np.testing.assert_allclose(g.candidate_matrix(f)[0, 3:], [np.sin(theta), np.cos(theta)])


@pytest.mark.parametrize('ps', [[0., 1.], [-.1, 1.1], [.2, .2], [np.nan, .5], [np.inf, .5]])
def test_invalid_probability_rejected(ps):
    with pytest.raises(ValueError):
        g.validate_probabilities({str(i): SimpleNamespace(win=p) for i, p in enumerate(ps)}, ['0', '1'])


def test_probability_alignment_and_population():
    pred = {'b': SimpleNamespace(win=.7), 'a': SimpleNamespace(win=.3)}
    np.testing.assert_equal(g.validate_probabilities(pred, ['a', 'b']), [.3, .7])
    with pytest.raises(ValueError):
        g.validate_probabilities(pred, ['a'])


def folds():
    return [[g.probe.RaceProbe(f'{year}-01-0{d}', np.array([.5, .5]),
                 np.array([[1., 0., 0., 0., 0.], [0., 0., 0., 0., 0.]]), 0 if d < 4 else 1)
             for d in range(1, 5)] for year in range(2019, 2027)]


def config():
    return {'bootstrap': {'b': 20, 'seed': 20260907, 'alpha': .0125}}


def test_prequential_counts_exclude_initial_fold_and_alpha(monkeypatch):
    original = g.bootstrap.race_day_cluster_bootstrap_ci_v1
    seen = []
    def capture(*args, **kw):
        seen.append(kw['alpha'])
        return original(*args, **kw)
    monkeypatch.setattr(g.bootstrap, 'race_day_cluster_bootstrap_ci_v1', capture)
    out = g.assess_candidate(folds(), 'gap_log', config())
    assert out['state'] == 'POINT_IMPROVEMENT'
    assert out['n_initial_fit_races'] == 4
    assert out['n_evaluated_races'] == 28
    assert out['warmup_inclusive_descriptive']['n_races'] == 32
    assert out['sample_ci']['n_days'] == 28 and out['sample_ci']['is_total_ci'] is False
    assert seen == [.0125]
    assert all(d.startswith(('2020', '2021', '2022', '2023', '2024', '2025', '2026')) for d in out['delta_nll_by_day'])
    assert all(d['fit_last_day'] < d['eval_first_day'] for d in out['fit_diagnostics'])
    assert out['eligible_for_verdict'] is False and out['can_adopt'] is False


def test_constant_feature_no_gain():
    out = g.assess_candidate(folds(), 'seasonal_sex', config())
    assert out['state'] == 'NO_OBSERVED_IMPROVEMENT'
    assert out['point_delta_winner_nll'] == 0.


def test_numerical_failure_is_blocked_not_candidate_rejection(monkeypatch):
    monkeypatch.setattr(g, 'fit_diagnostics', lambda *a: (_ for _ in ()).throw(ValueError('no convergence')))
    out = g.assess_candidate(folds(), 'gap_log', config())
    assert out['state'] == 'BLOCKED_NUMERICAL'
    assert 'point_delta_winner_nll' not in out
    assert out['can_adopt'] is False


def test_nonchronological_fold_rejected():
    fs = folds()
    fs[1], fs[2] = fs[2], fs[1]
    with pytest.raises(ValueError, match='chronological'):
        g.assess_candidate(fs, 'gap_log', config())


def test_missing_offset_is_zero_but_normalization_can_change_probability():
    r = g.probe.RaceProbe('2020-01-01', np.array([.5, .5]), np.array([[np.nan], [1.]]), 0)
    p, h = g.probe._clean(r)
    np.testing.assert_equal(h[:, 0], [0., 1.])
    assert g.probe._delta_nll_race(p, h, 0, np.array([1.])) > 0


def test_append_only_json(tmp_path):
    path = tmp_path / 'result.json'
    g.write_json(path, {'can_adopt': False})
    with pytest.raises(FileExistsError):
        g.write_json(path, {})


def synthetic_inputs(tmp_path, monkeypatch):
    import pickle
    rows, races, records = [], [], []
    cols = ['x' + str(i) for i in range(138)]
    for year in range(2019, 2027):
        rid = str(year)
        date = dt.date(year, 1, 2)
        context = SimpleNamespace(race_id=rid, race_date=date,
            started_horses=(SimpleNamespace(horse_id='a'), SimpleNamespace(horse_id='b')))
        labels = (g.dataset.ScoringLabel('a', 1, 1, 1),
                  g.dataset.ScoringLabel('b', int(year == 2020), 1, 1))
        races.append(g.dataset.EvalRace(context, labels, 1 if year == 2021 else 2))
        for hid in ['a', 'b']:
            rows.append({'race_id': rid, 'horse_id': hid, 'race_date': date,
                         'days_since_last': 20., 'sex': '牝' if hid == 'a' else '牡'})
        # Keep an eligible second race in the two years with ineligible first races.
        year_races = [races[-1]]
        if year in (2020, 2021):
            rid2 = rid + 'b'
            context2 = SimpleNamespace(race_id=rid2, race_date=date, started_horses=context.started_horses)
            extra = g.dataset.EvalRace(context2, (g.dataset.ScoringLabel('a', 1, 1, 1), g.dataset.ScoringLabel('b', 0, 1, 1)), 2)
            races.append(extra)
            year_races.append(extra)
            rows.extend([{**r, 'race_id': rid2} for r in rows[-2:]])
        path = tmp_path / (str(year) + '.pkl')
        payload = {'key': rid, 'train_hash': 'train' + rid, 'oof_info': {'sufficient': True},
                   'feature_columns': cols, 'recipe_meta': {'test': 'recipe'},
                   'predictions': {r.context.race_id: {'a': SimpleNamespace(win=.6), 'b': SimpleNamespace(win=.4)} for r in year_races}}
        path.write_bytes(pickle.dumps(payload))
        records.append({'year': year, 'arm': 'anchor', 'path': str(path), 'key': rid, 'train_hash': 'train' + rid})
    (tmp_path / 'snapshot.pkl').write_bytes(pickle.dumps((SimpleNamespace(frame=pd.DataFrame(rows)), races)))
    monkeypatch.setattr(g.stack.p, 'WORK', tmp_path)
    monkeypatch.setattr(g.stack, 'validate_receipt', lambda r: None)
    monkeypatch.setattr(g.stack.old, 'load_config', lambda: {'arms': {'n_oof_blocks': 8}})
    monkeypatch.setattr(g.stack.old, 'make_recipe', lambda *a: None)
    monkeypatch.setattr(g.stack.old, 'CalibSplitFactory', lambda *a, **kw: SimpleNamespace(recipe_meta={'test': 'recipe'}))
    monkeypatch.setattr(g.stack.p, 'columns_from_model', lambda: cols)
    return {'eval_window': {'to': '2026-08-23'}, 'gap_history_scope': 'test'}, {'source_caches': records}


def test_load_inputs_matches_canonical_eligibility(tmp_path, monkeypatch):
    cfg, frozen = synthetic_inputs(tmp_path, monkeypatch)
    fs, audit = g.load_inputs(cfg, frozen)
    assert len(fs) == 8 and list(map(len, fs)) == [1] * 8
    assert audit['initial_fit_races'] == 1 and audit['evaluated_races'] == 7
    assert audit['years']['2020']['excluded_races'] == 1  # Dead heat.
    assert audit['years']['2021']['excluded_races'] == 1  # Partial result coverage.
    assert all(len(r.p) == 2 for f in fs for r in f)


@pytest.mark.parametrize('field,bad', [('feature_columns', ['wrong'] * 138), ('recipe_meta', {'test': 'wrong'})])
def test_load_inputs_rejects_wrong_recipe_or_order(tmp_path, monkeypatch, field, bad):
    import pickle
    cfg, frozen = synthetic_inputs(tmp_path, monkeypatch)
    path = Path(frozen['source_caches'][0]['path'])
    cache = pickle.loads(path.read_bytes())
    cache[field] = bad
    path.write_bytes(pickle.dumps(cache))
    with pytest.raises(ValueError, match='identity/population'):
        g.load_inputs(cfg, frozen)


def test_registered_config():
    cfg = g.load_config()
    assert cfg['warmup_year'] == 2019
    assert cfg['bootstrap']['alpha'] == .0125
    assert cfg['can_adopt'] is False
