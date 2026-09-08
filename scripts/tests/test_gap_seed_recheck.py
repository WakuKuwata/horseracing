"""116 checks fixed seed schedule, frozen reuse, receipts, and seed42 evidence parity."""
from copy import deepcopy
from dataclasses import asdict
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
import gap_seed_recheck as d
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_training.dataset import TrainingMatrix


@pytest.fixture
def cfg():
    return d.load_config()


@pytest.fixture
def matrix():
    cols = d.s.p.columns_from_model() + d.s.p.OBSERVATION_COLUMNS
    return TrainingMatrix(pd.DataFrame(columns=cols), cols, [])


@pytest.fixture
def frozen(cfg):
    return {'sources': {'snapshot_sha256': 'snapshot'}, 'config_hash': d.s.p.gate_config_hash(cfg), 'source_hash': 'source'}


def context(rid, year):
    return RaceContext(rid, dt.date(year, 1, 6), (HorseEntry('h1'), HorseEntry('h2')))


@pytest.fixture
def folds():
    return {y: SimpleNamespace(valid_year=y, train=[SimpleNamespace(context=context(f'train{y}', y - 1))], valid=[])
            for y in range(2019, 2027)}


def test_fixed_thirty_fit_schedule(cfg, frozen, matrix, folds):
    jobs = d.training_jobs(cfg, frozen, matrix, [], folds)
    assert len(jobs) == len({j['key'] for j in jobs}) == 30
    assert jobs[0]['year'] == 2026 and jobs[-1]['year'] == 2019
    for seed in (43, 44):
        assert sorted(j['year'] for j in jobs if j['seed'] == seed and j['arm'] == 'pruning') == list(range(2019, 2027))
        assert sorted(j['year'] for j in jobs if j['seed'] == seed and j['arm'] == 'anchor') == list(range(2020, 2027))
    assert {j['seed'] for j in jobs} == {43, 44}


def test_schedule_requires_warmup(cfg, frozen, matrix, folds):
    folds.pop(2019)
    with pytest.raises(ValueError, match='warmup'):
        d.training_jobs(cfg, frozen, matrix, [], folds)


@pytest.mark.parametrize('seed', [43, 44])
@pytest.mark.parametrize('name,count', [('anchor', 138), ('pruning', 125)])
def test_factory_changes_training_seed_only(cfg, frozen, matrix, seed, name, count):
    f = d.factory(cfg, frozen, matrix, [], seed, name)
    assert len(f.expected_columns) == count
    assert f.factory.recipe.seed == seed
    assert f.factory.recipe.weight_mask_seed == 20260810
    assert f.factory.recipe.weight_mask_rate == .5
    assert dict(f.factory.recipe.params)['n_estimators'] == 900
    assert f.recipe_meta['n_oof_blocks'] == 8
    assert cfg['arms']['seed'] == 42


@pytest.mark.parametrize('seed,name', [(42, 'pruning'), (45, 'anchor'), (43, 'stack')])
def test_no_unregistered_fitting(cfg, frozen, matrix, seed, name):
    with pytest.raises(ValueError):
        d.factory(cfg, frozen, matrix, [], seed, name)


def test_smoke_recipe_is_structural(cfg, frozen, matrix):
    f = d.factory(cfg, frozen, matrix, [], 43, 'pruning', smoke=True)
    assert dict(f.factory.recipe.params)['n_estimators'] == 5
    assert f.recipe_meta['n_oof_blocks'] == 2
    assert f.smoke is True


def test_seed_config_keeps_noise_and_guards(cfg):
    for seed in (42, 43, 44):
        c = d.seed_config(cfg, seed)
        assert c['arms']['seed'] == seed
        c['arms']['seed'] = 42
        assert c == cfg
        assert c['seed_noise']['k_seeds'] == 1
    with pytest.raises(ValueError):
        d.seed_config(cfg, 99)


@pytest.mark.parametrize('field,value', [('seeds', [42, 43]), ('fresh_seeds', [42, 43, 44]), ('deployment_seed', 44), ('selected_columns', 127), ('new_fit_jobs', 32), ('warmup_year', 2020)])
def test_frozen_config_refuses_scope_mutation(tmp_path, monkeypatch, cfg, field, value):
    cfg[field] = value
    (tmp_path / 'gate-config.json').write_text(json.dumps(cfg))
    (tmp_path / 'gate-config.hash.txt').write_text(d.s.p.gate_config_hash(cfg))
    monkeypatch.setattr(d, 'SPEC', tmp_path)
    with pytest.raises(ValueError, match='scope'):
        d.load_config()


def test_frozen_config_refuses_same_hash_recipe_change(tmp_path, monkeypatch, cfg):
    cfg['arms']['weight_mask_seed'] += 1
    (tmp_path / 'gate-config.json').write_text(json.dumps(cfg))
    (tmp_path / 'gate-config.hash.txt').write_text(d.s.p.gate_config_hash(cfg))
    monkeypatch.setattr(d, 'SPEC', tmp_path)
    with pytest.raises(ValueError, match='recipe/gate'):
        d.load_config()


def test_output_scope_keeps_seed42_in_original115():
    assert d.result_path(42, 'increment') == d.g.SPEC / 'evidence/full-increment.json'
    assert d.result_path(43, 'anchor') == d.SPEC / 'evidence/seed-43-anchor.json'
    with pytest.raises(ValueError):
        d.seed_area(42)
    with pytest.raises(ValueError):
        d.result_path(99, 'anchor')


def test_isolated_output_root_is_restored_on_error(tmp_path, monkeypatch):
    original = d.s.p.WORK
    monkeypatch.setattr(d, 'WORK', tmp_path)
    with pytest.raises(RuntimeError):
        with d.isolated_cache():
            assert d.s.p.WORK == tmp_path
            raise RuntimeError('test')
    assert d.s.p.WORK == original


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'WORK', tmp_path)
    (tmp_path / 'run-freeze.json').write_text('{}')
    return tmp_path


def test_unreceipted_cache_never_counts_as_completed(work):
    (work / 'cache').mkdir()
    (work / 'cache/key.pkl').write_bytes(b'partial')
    assert d.completed({'key': 'key'}) is False


@pytest.mark.parametrize('change', ['none', 'cache', 'job', 'freeze', 'imported', 'threads'])
def test_completed_cache_requires_all_receipt_fields(work, change):
    job = {'key': 'key', 'seed': 43, 'arm': 'pruning', 'year': 2026, 'train_hash': 'train'}
    cache = work / 'cache/key.pkl'
    cache.parent.mkdir()
    cache.write_bytes(b'valid')
    receipt = {'job': deepcopy(job), 'cache_sha256': d.s.p.digest(cache),
               'run_freeze_sha256': d.s.p.digest(work / 'run-freeze.json'), 'imported': False, 'model_threads': 1}
    if change == 'cache':
        cache.write_bytes(b'changed')
    elif change == 'job':
        receipt['job']['seed'] = 44
    elif change == 'freeze':
        receipt['run_freeze_sha256'] = 'changed'
    elif change == 'imported':
        receipt['imported'] = True
    elif change == 'threads':
        receipt['model_threads'] = 2
    d.write_json(d.receipt_path('key'), receipt)
    if change == 'none':
        assert d.completed(job)
    else:
        with pytest.raises(ValueError):
            d.completed(job)


def test_verify_rejects_changed_upstream_without_unpickling(work, monkeypatch, cfg, frozen):
    frozen.update(runtime=d.s.runtime(), sources={'source': 'before'})
    (work / 'run-freeze.json').write_text(json.dumps(frozen))
    monkeypatch.setattr(d, 'load_config', lambda: cfg)
    monkeypatch.setattr(d, 'source_hash', lambda: 'source')
    monkeypatch.setattr(d, 'sources', lambda: ({'source': 'after'}, None, None, None))
    monkeypatch.setattr(d.s.reuse, 'load', lambda *_: pytest.fail('verify must never load another full matrix'))
    with pytest.raises(ValueError, match='upstream'):
        d.verify()


def test_seed42_certificate_reproduces_original_per_race_evidence(work, monkeypatch):
    ctx = context('valid', 2020)
    er = SimpleNamespace(context=ctx)
    fold = SimpleNamespace(train=[SimpleNamespace(context=context('train', 2019))], valid=[er])
    warmup = SimpleNamespace(train=[SimpleNamespace(context=context('train0', 2018))], valid=[])
    forecasts = {'h1': Prediction(.6, 1., 1.), 'h2': Prediction(.4, 1., 1.)}
    calls = []
    class Fake:
        def __init__(self, *args):
            self.name = args[-1]
        def fit(self, train, **kwargs):
            calls.append((self.name, train[0].race_date.year + 1))
            return SimpleNamespace(predict_race=lambda _: forecasts)
    monkeypatch.setattr(d.s, 'ReadOnlyFactory', Fake)
    monkeypatch.setattr(d.g, 'verified_coefficients', lambda _: {'gammas': {'2020': 0.}})
    monkeypatch.setattr(d.g, 'tilt_predictions', lambda base, *args: (base, {}))
    monkeypatch.setattr(d, 'population_masks', lambda _: SimpleNamespace(eligible=True, winner_horse_id='h1'))
    monkeypatch.setattr(d.g, 'WORK', work)
    (work / 'coefficients.json').write_text('{}')
    nll = d._clip_nll(.6)
    cov = {'valid': d.race_covariates('valid', field_size=2, race_day='2020-01-06')}
    expected = [asdict(r) for r in d.build_rows([('valid', '2020-01-06', nll, nll)], covariates=cov)]
    for name in ('increment', 'anchor'):
        d.write_json(work / f'{name}-evidence.json', {'rows': expected})
    cert = d.certify_seed42({}, {}, {}, None, [], {2019: warmup, 2020: fold}, {})
    assert cert['all_eligible_rows_exact'] and cert['n_eligible'] == 1
    assert ('anchor', 2019) not in calls
    expected[0]['candidate_winner_nll'] += .001
    (work / 'anchor-evidence.json').write_text(json.dumps({'rows': expected}))
    with pytest.raises(ValueError, match='parity failed'):
        d.certify_seed42({}, {}, {}, None, [], {2019: warmup, 2020: fold}, {})


def test_readonly_replay_rejects_changed_training_identity(cfg, frozen, matrix, work):
    f = d.ReadOnlyFactory(cfg, frozen, matrix, [], 43, 'pruning')
    f.frozen['jobs'] = [{'seed': 43, 'arm': 'pruning', 'year': 2020, 'key': 'key', 'train_hash': 'wrong'}]
    # A missing receipt rejects before loading any payload or fitting a model.
    with pytest.raises(ValueError, match='replay cache'):
        f.fit([context('train', 2019)])


def test_worker_preserves_unreceipted_output(work, monkeypatch):
    (work / 'cache').mkdir()
    path = work / 'cache/key.pkl'
    path.write_bytes(b'diagnostic')
    monkeypatch.setattr(d, 'verify', lambda: ({}, {'jobs': [{'key': 'key'}]}))
    with pytest.raises(ValueError, match='Unreceipted'):
        d.worker('key')
    assert path.read_bytes() == b'diagnostic'


def test_aggregate_preregistration_keeps_small_gain_policy(cfg):
    rule = cfg['seed_mean_rule']
    assert rule['require_all_seeds_negative'] is False
    assert rule['require_majority_negative'] is False
    assert rule['require_mean_ci'] is False
    assert rule['individual_BLOCKED'] == 'REVIEW_REQUIRED'
    assert rule['require_both_mean_primary_diffs_negative'] is True
    assert rule['deployment_seed42_residual_risk'] == 'not_resolved_by_seed_mean'


def test_aggregate_source_is_part_of_freeze(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    aggregate = scripts / 'gap_seed_summary.py'
    aggregate.write_text('original aggregate')
    monkeypatch.setattr(d, 'ROOT', tmp_path)
    monkeypatch.setattr(d.g, 'source_hash', lambda: 'fixed115')
    before = d.source_hash()
    aggregate.write_text('changed aggregate')
    assert d.source_hash() != before


def test_worker_quarantines_failed_partial_cache(work, monkeypatch):
    job = {'key': 'key', 'seed': 43, 'arm': 'pruning', 'year': 2020, 'train_hash': 'train'}
    monkeypatch.setattr(d, 'verify', lambda: ({}, {'jobs': [job]}))
    fold = SimpleNamespace(train=[], valid=[])
    monkeypatch.setattr(d.s, 'load_config', lambda: {})
    monkeypatch.setattr(d.s, 'inputs', lambda *_: (None, [], {2020: fold}))
    monkeypatch.setattr(d, 'job_for', lambda *_: job)
    path = work / 'cache/key.pkl'
    def fail(*args, **kwargs):
        path.parent.mkdir()
        path.write_bytes(b'failed output')
        raise RuntimeError('failed after writing')
    monkeypatch.setattr(d, 'factory', lambda *_: SimpleNamespace(fit=fail))
    with pytest.raises(RuntimeError):
        d.worker('key')
    assert not path.exists()
    assert not d.receipt_path('key').exists()
    saved = list((work / 'prefill').glob('unverified-key-*.pkl'))
    assert len(saved) == 1 and saved[0].read_bytes() == b'failed output'
