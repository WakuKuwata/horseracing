"""122 scope, temporal boundary, parameter forwarding and persistence; no study fit."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import market_feature_screen as m
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import RaceContext, HorseEntry, Prediction
from horseracing_eval.decision import ConfirmatoryContractError


@pytest.fixture
def cfg():
    return m.load_config()


@pytest.fixture
def frozen(cfg):
    return {'source_hash': 'source', 'config_hash': m.s.p.gate_config_hash(cfg), 'matrix_sha256': 'matrix',
            'baseline': {'mode': 'fresh_cache_absent'}}


def base_matrix():
    cols = m.s.p.columns_from_model() + m.s.p.OBSERVATION_COLUMNS
    frame = pd.DataFrame({c: [0., 1.] for c in cols})
    frame.insert(0, 'horse_id', ['H', 'X'])
    frame.insert(0, 'race_id', ['r', 'r'])
    return m.TrainingMatrix(frame, cols, [])


def additions():
    return pd.DataFrame({'race_id': ['r', 'r'], 'horse_id': ['H', 'X'], **{c: [0., 1.] for c in m.ADDITIONS}})


@pytest.fixture
def matrix():
    return m.augment_matrix(base_matrix(), additions())


def ctx(rid='201806010101', year=2018):
    return RaceContext(rid, dt.date(year, 1, 6), (HorseEntry('H'), HorseEntry('X')))


def race():
    return EvalRace(ctx(), (ScoringLabel('H', 1, 1, 1), ScoringLabel('X', 0, 1, 1)), 2)


def fold():
    return SimpleNamespace(valid_year=2018, train=[SimpleNamespace(context=ctx('train', 2017))], valid=[race()])


def test_exact_scope_difference_and_order(cfg, matrix):
    baseline = m.scope(matrix, cfg, 'baseline')
    f03, f05 = m.scope(matrix, cfg, 'f03'), m.scope(matrix, cfg, 'f05')
    assert len(baseline) == 125
    assert f03 == [c for c in baseline if c not in m.PAST_MARKET_COLUMNS] + m.F03
    assert f05 == baseline + m.F05
    assert m.scope(matrix, cfg, 'colsample_07') == baseline
    assert set(m.PM_CORE_STRENGTH_COLUMNS) <= set(f03) & set(f05)
    assert not set(m.PM_CONDITIONED_RESIDUAL_COLUMNS) & set(matrix.feature_cols)
    assert len(f03) == 126 and len(f05) == 131


@pytest.mark.parametrize('name', m.NAMES)
def test_actual_oof_base_receives_recipe_params_and_scope(cfg, frozen, matrix, name):
    f = m.Factory(cfg, frozen, matrix, [], name)
    oof = m.OofCalibratedPredictor(None, f.factory.recipe, shared_data=matrix, n_oof_blocks=8, method='isotonic')
    base = oof._make_base()
    assert base.params['colsample_bytree'] == (.7 if name == 'colsample_07' else 1.)
    assert base.params['n_estimators'] == 900
    assert base._data.feature_cols == f.expected_columns
    assert f.factory.recipe.weight_mask_seed == 20260810 and f.factory.recipe.seed == 42
    assert f.factory.recipe.weight_mask_rate == .5
    assert base._data.feature_cols == m.scope(matrix, cfg, name)


def test_colsample_changes_only_parameter(cfg, frozen, matrix):
    a = m.Factory(cfg, frozen, matrix, [], 'baseline')
    b = m.Factory(cfg, frozen, matrix, [], 'colsample_07')
    ma, mb = deepcopy(a.recipe_meta), deepcopy(b.recipe_meta)
    assert mb['params'] == ma['params'] + (('colsample_bytree', .7),)
    mb['params'] = ma['params']
    assert ma == mb and a.recipe_hash != b.recipe_hash


def test_effective_baseline_recipe_matches_native110(cfg, frozen, matrix):
    target = m.Factory(cfg, frozen, matrix, [], 'baseline')
    drops = next(c['drop_features'] for c in m.s.old.load_config()['candidates'] if c['id'] == 'relative_ability')
    native = m.s.old.CachedFactory(m.s.old.load_config(), m.native_matrix(matrix), [], [], drops=drops)
    assert native.expected_columns == target.expected_columns
    assert m.s.strip_drops(native.recipe_meta) == m.s.strip_drops(target.recipe_meta)
    assert native.recipe_hash != target.recipe_hash  # Explicit unused research columns differ.


def test_three_or_four_unique_outer_jobs(cfg, frozen, matrix):
    jobs = [m.job_for(m.Factory(cfg, frozen, matrix, [race()], n), fold()) for n in m.NAMES]
    assert len({j['key'] for j in jobs}) == 4
    assert len({j['train_hash'] for j in jobs}) == 1
    assert [len(j['feature_columns']) for j in jobs] == [125, 126, 131, 125]
    assert all(j['year'] == 2018 for j in jobs)


@pytest.mark.parametrize('name', m.NAMES)
def test_smoke_is_five_trees_two_oof(cfg, frozen, matrix, name):
    f = m.Factory(cfg, frozen, matrix, [], name, smoke=True)
    assert f.factory.recipe.resolved_params()['n_estimators'] == 5
    assert f.factory.n_oof_blocks == 2 and not f.factory.require_sufficient


@pytest.mark.parametrize('field,value', [('arms', {}), ('new_outer_jobs', {}), ('study_arms', []),
    ('contrasts', []), ('can_adopt', True), ('screen_rule', {}), ('eval_window', {}), ('fallback', 'always refit')])
def test_even_rehashed_unregistered_config_refused(cfg, tmp_path, monkeypatch, field, value):
    cfg[field] = value
    (tmp_path / 'gate-config.json').write_text(json.dumps(cfg))
    (tmp_path / 'gate-config.hash.txt').write_text(m.s.p.gate_config_hash(cfg))
    monkeypatch.setattr(m, 'SPEC', tmp_path)
    with pytest.raises((ValueError, ConfirmatoryContractError)):
        m.load_config()


def test_augmentation_preserves_all_source_values_and_input_objects():
    base, new = base_matrix(), additions()
    original = base.frame.copy(deep=True)
    got = m.augment_matrix(base, new.iloc[::-1])
    pd.testing.assert_frame_equal(base.frame, original, check_exact=True)
    pd.testing.assert_frame_equal(got.frame[original.columns], original, check_exact=True)
    assert got.frame[m.F03[0]].tolist() == [0., 1.]


@pytest.mark.parametrize('fault', ['duplicate', 'missing', 'extra_column', 'int_dtype', 'inf', 'rank_range', 'nan_count', 'fractional_count'])
def test_augmentation_rejects_invalid_keys_scope_and_values(fault):
    x = additions()
    if fault == 'duplicate': x = pd.concat([x, x.iloc[:1]])
    elif fault == 'missing': x = x.iloc[:1]
    elif fault == 'extra_column': x['residual'] = 0.
    elif fault == 'int_dtype': x[m.F03[0]] = x[m.F03[0]].astype(int)
    elif fault == 'inf': x.loc[0, m.F05[0]] = np.inf
    elif fault == 'rank_range': x.loc[0, m.F03[0]] = -1.
    elif fault == 'nan_count': x.loc[0, m.F03[-1]] = np.nan
    elif fault == 'fractional_count': x.loc[0, m.F05[-1]] = .5
    with pytest.raises(ValueError):
        m.augment_matrix(base_matrix(), x)


def frames():
    races, horses, results = [], [], []
    for i, day in enumerate(['2017-01-01', '2017-02-01', '2017-03-01', '2018-01-06', '2019-01-01']):
        rid = f'r{i}'
        races.append({'race_id': rid, 'race_date': pd.Timestamp(day), 'track_type': '芝', 'distance': 1600, 'venue_code': '06'})
        for j, hid in enumerate(['H', 'X']):
            horses.append({'race_id': rid, 'horse_id': hid, 'entry_status': 'started', 'popularity': j + 1, 'odds': 2. + j})
            results.append({'race_id': rid, 'horse_id': hid, 'finish_order': j + 1, 'result_status': 'finished'})
    return m.Frames(pd.DataFrame(races), pd.DataFrame(horses), pd.DataFrame(results))


def target_features(f):
    return m.feature_additions(f).set_index(['race_id', 'horse_id']).loc[['r3']].sort_index()


@pytest.mark.parametrize('mutation', ['target_odds_pop_result', 'future', 'same_day'])
def test_existing_builders_time_boundary_without_database(mutation):
    original = frames()
    expected = target_features(original)
    changed = deepcopy(original)
    if mutation == 'future':
        changed = m.subset_frames(changed, dt.date(2018, 12, 31))
    else:
        rid = 'r3' if mutation == 'target_odds_pop_result' else 'r4'
        if mutation == 'same_day':
            changed.races.loc[changed.races.race_id == rid, 'race_date'] = pd.Timestamp('2018-01-06')
        changed.race_horses.loc[changed.race_horses.race_id == rid, ['odds', 'popularity']] = [99., 18]
        changed.race_results.loc[changed.race_results.race_id == rid, 'finish_order'] = 7
    pd.testing.assert_frame_equal(expected, target_features(changed), check_exact=True)


def test_history_changes_reach_features_and_residuals_never_leave_builder():
    f = frames()
    base = target_features(f)
    f.race_horses.loc[(f.race_horses.race_id == 'r0') & (f.race_horses.horse_id == 'H'), ['odds', 'popularity']] = [20., 8]
    changed = target_features(f)
    assert not changed.equals(base)
    assert list(changed.columns) == m.ADDITIONS
    assert changed[m.F03[-1]].eq(3).all()
    assert all(v == np.dtype('float64') for v in changed.dtypes)


def cache_for(f, key='key', th='train'):
    er = race()
    return {'key': key, 'train_hash': th, 'recipe_meta': f.recipe_meta, 'feature_columns': f.expected_columns,
        'predictions': {er.context.race_id: {'H': Prediction(.6, 1., 1.), 'X': Prediction(.4, 1., 1.)}},
        'oof_info': {'sufficient': True}, 'actual_params': f.factory.recipe.resolved_params(), 'feature_dtypes': f.dtypes()}


@pytest.mark.parametrize('fault', ['none', 'train', 'recipe', 'columns', 'oof', 'horse', 'top3', 'race', 'params', 'dtype'])
def test_cache_strict_identity_population_and_three_heads(cfg, frozen, matrix, fault):
    f = m.Factory(cfg, frozen, matrix, [race()], 'f03')
    cache = cache_for(f)
    if fault == 'train': cache['train_hash'] = 'other'
    elif fault == 'recipe': cache['recipe_meta'] = {}
    elif fault == 'columns': cache['feature_columns'] = f.expected_columns[::-1]
    elif fault == 'oof': cache['oof_info']['sufficient'] = False
    elif fault == 'horse': cache['predictions'][ctx().race_id].pop('X')
    elif fault == 'top3': cache['predictions'][ctx().race_id]['H'] = Prediction(.6, 1., .7)
    elif fault == 'race': cache['predictions']['extra'] = cache['predictions'][ctx().race_id]
    elif fault == 'params': cache['actual_params'] = {}
    elif fault == 'dtype': cache['feature_dtypes'] = {}
    if fault == 'none':
        m.check_payload(cache, 'key', 'train', f, [race()])
    else:
        with pytest.raises(ValueError): m.check_payload(cache, 'key', 'train', f, [race()])


def report(diff=-1e-8, **reasons):
    return {'periods': {'all': {'diff': diff}}, 'gate': {'reasons': {
        'top2_diff': 0., 'top3_diff': 0., 'cand_ece': .004, 'act_ece': .004, **reasons}}}


@pytest.mark.parametrize('diff,expected', [(-1e-12, 'ADVANCE_TO_FULL_RESEARCH'), (0., 'DEFER'), (.001, 'DEFER')])
def test_small_improvement_without_ci_threshold(diff, expected):
    r = report(diff)
    r['gate'].update(stat_guard=False, recent_guard=False)
    assert m.progression(r) == expected


@pytest.mark.parametrize('reasons', [{'top2_diff': .00050001}, {'top3_diff': .00050001}, {'cand_ece': .05}, {'cand_ece': .005001}])
def test_quality_limits_remain_required(reasons):
    assert m.progression(report(**reasons)) == 'QUALITY_REVIEW_REQUIRED'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), None, True, '0'])
def test_missing_invalid_numeric_stops(value):
    with pytest.raises(ValueError): m.progression(report(cand_ece=value))


def test_exact_top_boundary_allowed():
    assert m.progression(report(top2_diff=.0005, top3_diff=.0005)) == 'ADVANCE_TO_FULL_RESEARCH'


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'WORK', tmp_path / 'work')
    monkeypatch.setattr(m, 'SPEC', tmp_path / 'spec')
    m.WORK.mkdir(); m.SPEC.mkdir()
    (m.WORK / 'run-freeze.json').write_text('{}')
    return m.WORK


def test_completed_receipt_requires_exact_job_cache_and_freeze(work):
    job = {'key': 'key', 'arm': 'f03'}
    path = work / 'cache/key.pkl'
    path.parent.mkdir(); path.write_bytes(b'cache')
    assert not m.completed(job)
    m.write_json(m.receipt_path('key'), {'job': job, 'cache_sha256': m.s.p.digest(path),
        'run_freeze_sha256': m.s.p.digest(work / 'run-freeze.json'), 'model_threads': 1, 'imported': False})
    assert m.completed(job)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError): m.completed(job)


def test_worker_rejects_orphan_without_loading_or_fitting(cfg, frozen, work, monkeypatch):
    frozen['jobs'] = [{'key': 'orphan'}]
    (work / 'cache').mkdir(); (work / 'cache/orphan.pkl').write_bytes(b'orphan')
    monkeypatch.setattr(m, 'verify', lambda: (cfg, frozen))
    monkeypatch.setattr(m, 'inputs', lambda *a: pytest.fail('No data load on orphan'))
    with pytest.raises(ValueError, match='Unreceipted'): m.worker('orphan')
    assert (work / 'cache/orphan.pkl').read_bytes() == b'orphan'


def test_failed_fit_cache_is_quarantined_before_receipt(cfg, frozen, matrix, work, monkeypatch):
    f = m.Factory(cfg, frozen, matrix, [race()], 'f03')
    job = m.job_for(f, fold())
    frozen['jobs'] = [job]
    monkeypatch.setattr(m, 'verify', lambda: (cfg, frozen))
    monkeypatch.setattr(m, 'inputs', lambda *_: (matrix, [race()], fold()))
    def failed(self, *a, **kw):
        path = work / 'cache' / f"{job['key']}.pkl"
        path.parent.mkdir(); path.write_bytes(b'failed')
        raise ValueError('injected failure')
    monkeypatch.setattr(m.Factory, 'fit', failed)
    with pytest.raises(ValueError, match='injected'): m.worker(job['key'])
    assert not (work / 'cache' / f"{job['key']}.pkl").exists()
    assert len(list((work / 'prefill').glob('unverified-*.pkl'))) == 1
    assert not m.receipt_path(job['key']).exists()


@pytest.mark.parametrize('which', ['report', 'evidence', 'receipt'])
def test_incomplete_result_resume_fails_closed(cfg, frozen, work, which):
    path = {'report': m.result_path('f03'), 'evidence': work / 'f03-evidence.json', 'receipt': m.result_receipt('f03')}[which]
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')
    with pytest.raises(ValueError, match='Incomplete'): m.verified_result('f03', cfg, frozen)


def test_noneligible_replacement_cannot_hide_behind_same_eligible_rows():
    frozen = {'population': {'race_id_set_hash': 'original full', 'n_races': 2, 'n_eligible': 1, 'eligible_id_days': [['r', '2018-01-06']]}}
    evidence = {'race_id_set_hash': 'other full', 'rows': [{'race_id': 'r', 'race_day': '2018-01-06'}]}
    r = {'race_id_set_hash': 'other full', 'n_races': 2, 'n_eligible': 1}
    with pytest.raises(ValueError, match='population'): m.validate_result_population(r, evidence, frozen)


def test_native_missing_cache_is_only_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'native_source', lambda *a: ('expected', 'train', None, tmp_path / 'absent.pkl'))
    assert m.certify_baseline(None, [], fold()) == {'mode': 'fresh_cache_absent', 'reason': 'Expected native cache absent', 'expected_key': 'expected', 'train_hash': 'train'}
    (tmp_path / 'absent.pkl').write_bytes(b'corrupt')
    with pytest.raises(Exception): m.certify_baseline(None, [], fold())


def test_fresh_replay_never_fits_missing_cache(cfg, frozen, matrix, work):
    f = m.ReadOnlyFactory(cfg, frozen, matrix, [race()], 'f03')
    frozen['jobs'] = [m.job_for(f, fold())]
    with pytest.raises(ValueError, match='receipt missing'):
        f.fit([r.context for r in fold().train])


def test_source_fields_are_not_redirected_by_fresh_factory(cfg, frozen, matrix):
    before = m.s.p.WORK, m.s.old.WORK
    m.Factory(cfg, frozen, matrix, [race()], 'baseline')
    assert (m.s.p.WORK, m.s.old.WORK) == before


@pytest.mark.parametrize('fault', ['none', 'negative', 'nan', 'diff', 'mean', 'changed', 'seq'])
def test_report_primary_and_rows_recomputed_on_resume(fault):
    r = report(-.1)
    r['periods']['all'].update(candidate=1., active=1.1)
    r['gate']['reasons']['winner_nll_diff'] = -.1
    r['primary_loss_changed'] = True
    row = {'seq': 0, 'candidate_winner_nll': 1., 'active_winner_nll': 1.1, 'diff': 1. - 1.1}
    if fault == 'negative': row['candidate_winner_nll'] = -1.
    elif fault == 'nan': row['active_winner_nll'] = np.nan
    elif fault == 'diff': row['diff'] = -.2
    elif fault == 'mean': r['periods']['all']['candidate'] = .9
    elif fault == 'changed': r['primary_loss_changed'] = False
    elif fault == 'seq': row['seq'] = 1
    if fault == 'none': m.validate_losses(r, {'rows': [row]})
    else:
        with pytest.raises(ValueError): m.validate_losses(r, {'rows': [row]})


def test_complete_result_resume_verifies_receipt_and_loss_provenance(cfg, frozen, work):
    name = 'f03'
    frozen.update(arm_recipe_hashes={'f03': 'candidate', 'baseline': 'base'}, arm_columns={'f03': ['c'], 'baseline': ['b']},
        population={'race_id_set_hash': 'pop', 'n_races': 1, 'n_eligible': 1, 'eligible_id_days': [['r', '2018-01-06']]})
    e = {'rows': [{'seq': 0, 'race_id': 'r', 'race_day': '2018-01-06', 'candidate_winner_nll': 1., 'active_winner_nll': 1.1, 'diff': 1. - 1.1}],
        'race_id_set_hash': 'pop', 'candidate_recipe_hash': 'candidate', 'active_recipe_hash': 'base', 'gate_config_hash': m.s.p.gate_config_hash(cfg)}
    m.write_json(work / 'f03-evidence.json', e)
    r = report(-.1)
    r['periods']['all'].update(candidate=1., active=1.1)
    r['gate']['reasons']['winner_nll_diff'] = -.1
    r.update(primary_loss_changed=True, candidate_recipe_hash='candidate', active_recipe_hash='base',
        gate_config_hash=m.s.p.gate_config_hash(cfg), candidate_columns=['c'], baseline_columns=['b'], race_id_set_hash='pop', n_races=1, n_eligible=1,
        study_config_hash=m.s.p.gate_config_hash(cfg), run_freeze_sha256=m.s.p.digest(work / 'run-freeze.json'),
        can_adopt=False, eligible_for_verdict=False, contrast=m.CONTRASTS[0], evidence_path=str(work / 'f03-evidence.json'),
        evidence_sha256=m.s.p.digest(work / 'f03-evidence.json'), progression='ADVANCE_TO_FULL_RESEARCH')
    m.write_json(m.result_path(name), r)
    receipt = {'report_sha256': m.s.p.digest(m.result_path(name)), 'evidence_sha256': m.s.p.digest(work / 'f03-evidence.json'),
        'run_freeze_sha256': m.s.p.digest(work / 'run-freeze.json'), 'contrast': name}
    m.write_json(m.result_receipt(name), receipt)
    assert m.verified_result(name, cfg, frozen)
    # Even reissuing a receipt cannot bless an inconsistent favorable report.
    r['periods']['all']['diff'] = -.2
    m.result_path(name).write_text(json.dumps(r))
    receipt['report_sha256'] = m.s.p.digest(m.result_path(name))
    m.result_receipt(name).write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='mean/primary'):
        m.verified_result(name, cfg, frozen)


@pytest.mark.parametrize('fault', ['none', 'anchor_cache', 'baseline_cache', 'matrix', 'runtime', 'source', 'config', 'audit'])
def test_verify_binds_both_old_caches_and_new_input(cfg, work, monkeypatch, fault):
    paths = {k: work / k for k in ['matrix.pkl', 'feature-audit.json', 'anchor.pkl', 'baseline.pkl']}
    for path in paths.values(): path.write_bytes(b'original')
    state = {'files': {}}
    f = {'source_hash': 'source', 'config_hash': m.s.p.gate_config_hash(cfg), 'runtime': m.s.runtime(), 'sources': state,
        'matrix_sha256': m.s.p.digest(paths['matrix.pkl']), 'feature_audit_sha256': m.s.p.digest(paths['feature-audit.json']),
        'baseline': {'mode': 'native_cache', 'path': str(paths['baseline.pkl']), 'sha256': m.s.p.digest(paths['baseline.pkl']),
                     'anchor_cache_path': str(paths['anchor.pkl']), 'anchor_cache_sha256': m.s.p.digest(paths['anchor.pkl'])}}
    monkeypatch.setattr(m, 'load_config', lambda: cfg)
    monkeypatch.setattr(m, 'source_state', lambda: state)
    monkeypatch.setattr(m, 'source_hash', lambda: 'source')
    if fault == 'anchor_cache': paths['anchor.pkl'].write_bytes(b'changed')
    elif fault == 'baseline_cache': paths['baseline.pkl'].write_bytes(b'changed')
    elif fault == 'matrix': paths['matrix.pkl'].write_bytes(b'changed')
    elif fault == 'audit': paths['feature-audit.json'].write_bytes(b'changed')
    elif fault == 'runtime': f['runtime'] = {}
    elif fault == 'source': f['source_hash'] = 'changed'
    elif fault == 'config': f['config_hash'] = 'changed'
    (work / 'run-freeze.json').write_text(json.dumps(f))
    if fault == 'none': assert m.verify() == (cfg, f)
    else:
        with pytest.raises(ValueError): m.verify()
