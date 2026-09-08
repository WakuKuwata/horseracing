"""113 tests target source-cache safety and exact registered research scope."""
from __future__ import annotations
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import small_gain_stack as s
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_training.dataset import TrainingMatrix


@pytest.fixture
def cfg():
    return s.load_config()


def test_exact_feature_scope_has_138_125_127_columns(cfg):
    base = s.p.columns_from_model()
    matrix = TrainingMatrix(pd.DataFrame(columns=base + s.p.OBSERVATION_COLUMNS), base + s.p.OBSERVATION_COLUMNS, [])
    result = {}
    for arm in cfg['study_arms']:
        result[arm['id']] = s.p.validate_scope(matrix, s.p.make_recipe(cfg, arm['drop_features']))
    assert [len(result[n]) for n in ('anchor', 'pruning', 'stack')] == [138, 125, 127]
    assert set(result['stack']) - set(result['pruning']) == set(s.p.OBSERVATION_COLUMNS[:2])
    assert result['anchor'] == base


@pytest.mark.parametrize('change', ['seed', 'trees', 'oof', 'drop', 'arm_order', 'contrast', 'delta', 'alpha', 'reps', 'window', 'smoke'])
def test_config_refuses_unregistered_scope_or_recipe(cfg, change):
    if change in ('seed', 'trees', 'oof'):
        cfg['arms'][{'seed': 'seed', 'trees': 'n_estimators', 'oof': 'n_oof_blocks'}[change]] += 1
    elif change == 'drop':
        cfg['study_arms'][2]['drop_features'].pop()
    elif change == 'arm_order':
        cfg['study_arms'].reverse()
    elif change == 'contrast':
        cfg['contrasts'][0]['baseline'] = 'anchor'
    elif change == 'delta':
        cfg['min_effect_delta'] = .001
    elif change == 'alpha':
        cfg['bootstrap']['alpha'] = .05
    elif change == 'reps':
        cfg['bootstrap']['b'] = 50
    elif change == 'window':
        cfg['eval_window']['to'] = '2025-12-31'
    else:
        cfg['smoke']['eval_window']['to'] = '2008-12-31'
    with pytest.raises(ValueError):
        s.validate_config(cfg, s.old.load_config())


def test_cache_output_context_restores_old_input_root_after_failure(tmp_path, monkeypatch):
    original = s.p.WORK
    monkeypatch.setattr(s, 'WORK', tmp_path)
    with pytest.raises(RuntimeError):
        with s.isolated_cache():
            assert s.p.WORK == tmp_path
            raise RuntimeError('deliberate')
    assert s.p.WORK == original


def test_recipe_comparison_ignores_only_registered_drop_field():
    left = {'recipe': {'drop_features': ('a',), 'seed': 42, 'params': (('n_estimators', 900),)}, 'n_oof_blocks': 8}
    right = deepcopy(left)
    right['recipe']['drop_features'] = ('b',)
    assert s.strip_drops(left) == s.strip_drops(right)
    right['recipe']['seed'] = 43
    assert s.strip_drops(left) != s.strip_drops(right)


@pytest.fixture
def source_record(tmp_path):
    path, receipt = tmp_path / 'cache.pkl', tmp_path / 'receipt.json'
    path.write_bytes(b'original cache')
    job = {'key': 'k', 'year': 2020, 'arm': {'id': 'baseline', 'drop_features': []}}
    receipt.write_text(json.dumps({'job': job, 'cache_sha256': s.p.digest(path)}))
    return {'path': str(path), 'receipt_path': str(receipt), 'sha256': s.p.digest(path),
            'receipt_sha256': s.p.digest(receipt), 'source_job': job}


def test_source_receipt_exact_hash_and_job(source_record):
    s.validate_receipt(source_record)


@pytest.mark.parametrize('change', ['cache', 'receipt', 'job', 'receipted_hash'])
def test_source_receipt_rejects_changed_or_wrong_record(source_record, change):
    if change == 'cache':
        Path(source_record['path']).write_bytes(b'changed')
    elif change == 'receipt':
        Path(source_record['receipt_path']).write_text('{}')
    elif change == 'job':
        source_record['source_job']['year'] = 2021
    else:
        receipt = Path(source_record['receipt_path'])
        value = json.loads(receipt.read_text())
        value['cache_sha256'] = 'incorrect'
        receipt.write_text(json.dumps(value))
        source_record['receipt_sha256'] = s.p.digest(receipt)
    with pytest.raises(ValueError):
        s.validate_receipt(source_record)


@pytest.fixture
def frozen_run(tmp_path, monkeypatch, cfg):
    monkeypatch.setattr(s, 'WORK', tmp_path)
    monkeypatch.setattr(s, 'load_config', lambda: cfg)
    monkeypatch.setattr(s, 'source_hash', lambda: 'execution-source')
    source = tmp_path / 'source.pkl'
    source.write_bytes(b'immutable input')
    frozen = {'source_hash': 'execution-source', 'config_hash': s.p.gate_config_hash(cfg),
              'runtime': s.runtime(), 'source_files': {str(source): s.p.digest(source)},
              'source_caches': [], 'jobs': []}
    s.p.write_json(tmp_path / 'run-freeze.json', frozen)
    return tmp_path, frozen


def test_verification_hashes_inputs_without_unpickling(frozen_run, monkeypatch):
    monkeypatch.setattr(s.reuse, 'load', lambda *_: pytest.fail('Verification must not unpickle another training matrix'))
    s.verify()


@pytest.mark.parametrize('change', ['config', 'source', 'snapshot', 'runtime'])
def test_verify_fails_closed_on_changes(frozen_run, monkeypatch, cfg, change):
    work, _ = frozen_run
    if change == 'config':
        cfg['bootstrap']['seed'] += 1
    elif change == 'source':
        monkeypatch.setattr(s, 'source_hash', lambda: 'changed')
    elif change == 'snapshot':
        (work / 'source.pkl').write_bytes(b'changed')
    else:
        monkeypatch.setattr(s, 'runtime', lambda: {'python': 'different'})
    with pytest.raises(ValueError):
        s.verify()


def context(rid, year, horse='horse'):
    return RaceContext(rid, dt.date(year, 1, 6), (HorseEntry(horse),))


def test_train_identity_binds_dates_horses_and_order():
    original = s.train_identity([context('r', 2018)])
    assert original != s.train_identity([context('r', 2019)])
    assert original != s.train_identity([context('r', 2018, 'other')])
    assert original != s.train_identity([context('s', 2018)])
    assert s.train_identity([context('a', 2018), context('b', 2018)]) != s.train_identity([context('b', 2018), context('a', 2018)])


@pytest.fixture
def payload():
    ctx = context('valid', 2019)
    f = SimpleNamespace(recipe_meta={'recipe': {'drop_features': ('a',), 'seed': 42}}, expected_columns=['b'])
    cache = {'key': 'key', 'train_hash': 'train', 'recipe_meta': deepcopy(f.recipe_meta),
             'feature_columns': ['b'], 'oof_info': {'sufficient': True},
             'predictions': {'valid': {'horse': Prediction(1., 1., 1.)}}}
    return cache, f, [SimpleNamespace(context=ctx)]


def test_native_tuple_metadata_is_preserved(payload):
    cache, f, races = payload
    s.reuse.check_payload(cache, 'key', 'train', f, races)
    converted = json.loads(json.dumps(cache['recipe_meta']))
    cache['recipe_meta'] = converted
    with pytest.raises(ValueError, match='identity/scope'):
        s.reuse.check_payload(cache, 'key', 'train', f, races)


@pytest.mark.parametrize('change', ['key', 'train', 'seed', 'columns', 'oof', 'race', 'horse', 'probabilities'])
def test_source_payload_reuse_rejects_unsafe_cache(payload, change):
    cache, f, races = payload
    if change == 'key':
        cache['key'] = 'different'
    elif change == 'train':
        cache['train_hash'] = 'different'
    elif change == 'seed':
        cache['recipe_meta']['recipe']['seed'] = 43
    elif change == 'columns':
        cache['feature_columns'] = ['a']
    elif change == 'oof':
        cache['oof_info']['sufficient'] = False
    elif change == 'race':
        cache['predictions']['other'] = cache['predictions'].pop('valid')
    elif change == 'horse':
        cache['predictions']['valid']['other'] = cache['predictions']['valid'].pop('horse')
    else:
        cache['predictions']['valid']['horse'] = Prediction(.2, .1, 1.)
    with pytest.raises(ValueError):
        s.reuse.check_payload(cache, 'key', 'train', f, races)


def test_missing_receipt_never_marks_existing_cache_completed(frozen_run):
    work, _ = frozen_run
    path = work / 'cache' / 'key.pkl'
    path.parent.mkdir()
    path.write_bytes(b'unverified')
    assert s.completed({'key': 'key'}) is False


def test_fresh_completion_requires_frozen_receipt(frozen_run):
    work, _ = frozen_run
    job = {'key': 'key', 'year': 2020}
    path = work / 'cache' / 'key.pkl'
    path.parent.mkdir()
    path.write_bytes(b'verified')
    s.p.write_json(s.receipt_path('key'), {'job': job, 'cache_sha256': s.p.digest(path),
        'run_freeze_sha256': s.p.digest(work / 'run-freeze.json'), 'imported': False, 'model_threads': 1})
    assert s.completed(job)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError):
        s.completed(job)


def test_worker_refuses_unreceipted_prior_cache_before_any_fit(frozen_run, monkeypatch):
    work, frozen = frozen_run
    job = {'key': 'key', 'year': 2020}
    frozen['jobs'] = [job]
    monkeypatch.setattr(s, 'verify', lambda: ({}, frozen))
    monkeypatch.setattr(s, 'inputs', lambda *_: pytest.fail('Unsafe replay attempted'))
    path = work / 'cache' / 'key.pkl'
    path.parent.mkdir()
    path.write_bytes(b'preserve diagnostic cache')
    with pytest.raises(ValueError, match='Unreceipted'):
        s.worker('key')
    assert path.read_bytes() == b'preserve diagnostic cache'


def test_matrix_parity_refuses_changed_value_or_category(monkeypatch):
    monkeypatch.setattr(s.p, 'columns_from_model', lambda: ['base'])
    a = TrainingMatrix(pd.DataFrame({'base': [1.]}), ['base'], [])
    b = TrainingMatrix(pd.DataFrame({'base': [1.], **{c: [0.] for c in s.p.OBSERVATION_COLUMNS}}), ['base'] + s.p.OBSERVATION_COLUMNS, [])
    s.check_matrix_parity(a, [], b, [])
    b.frame.loc[0, 'base'] = 2.
    with pytest.raises(AssertionError):
        s.check_matrix_parity(a, [], b, [])
    b.frame.loc[0, 'base'] = 1.
    b.categorical_cols.append('base')
    with pytest.raises(ValueError, match='category'):
        s.check_matrix_parity(a, [], b, [])


def test_historical_certificate_is_bound_to_original_method_and_freezes():
    s.verify_historical_certificate()


@pytest.mark.parametrize('change', ['method_sha256', 'old_freeze_sha256', 'new_freeze_sha256', 'full_frame_projection_exact', 'all_eval_races_and_folds_exact', 'effective_recipe_exact', 'fresh_screen_parity', 'runtime'])
def test_historical_certificate_refuses_broken_links_or_missing_parity(monkeypatch, change):
    cert = s.read_json(s.p.WORK / 'baseline-equivalence.json')
    cert[change] = 'changed'
    if change == 'fresh_screen_parity':
        cert[change] = {}
    monkeypatch.setattr(s, 'read_json', lambda _: cert)
    with pytest.raises(ValueError):
        s.verify_historical_certificate()


def test_existing_research_result_resumes_only_after_provenance_check(frozen_run, cfg):
    work, frozen = frozen_run
    contrast = cfg['contrasts'][0]
    evidence = work / 'full-increment-evidence.json'
    evidence.write_text('{"rows": []}')
    result = {'artifact_kind': 'small_gain_stack_research_report', 'can_adopt': False,
        'eligible_for_verdict': False, 'study_config_hash': s.p.gate_config_hash(cfg),
        'run_freeze_sha256': s.p.digest(work / 'run-freeze.json'), 'contrast': contrast,
        'evidence_path': str(evidence), 'evidence_sha256': s.p.digest(evidence)}
    result['research_disposition'] = s.research.assess_research(result)
    output = work / 'result.json'
    assert s.verified_result(output, cfg, contrast) is False
    s.p.write_json(output, result)
    assert s.verified_result(output, cfg, contrast)
    evidence.write_text('{"rows": ["changed"]}')
    with pytest.raises(ValueError, match='provenance changed'):
        s.verified_result(output, cfg, contrast)


def test_worker_quarantines_failed_unreceipted_cache(frozen_run, monkeypatch):
    work, frozen = frozen_run
    job = {'key': 'key', 'year': 2020, 'train_hash': 'train'}
    frozen['jobs'] = [job]
    monkeypatch.setattr(s, 'verify', lambda: ({}, frozen))
    fold = SimpleNamespace(train=[], valid=[])
    monkeypatch.setattr(s, 'inputs', lambda *_: (None, [], {2020: fold}))
    monkeypatch.setattr(s, 'key_for', lambda *_: ('key', 'train'))
    path = work / 'cache' / 'key.pkl'
    def bad_fit(*args, **kwargs):
        path.parent.mkdir()
        path.write_bytes(b'failed partial cache')
        raise RuntimeError('deliberate failure after write')
    monkeypatch.setattr(s, 'factory', lambda *_: SimpleNamespace(fit=bad_fit))
    with pytest.raises(RuntimeError):
        s.worker('key')
    assert not path.exists()
    assert not s.receipt_path('key').exists()
    preserved = list((work / 'prefill').glob('unverified-key-*.pkl'))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == b'failed partial cache'
