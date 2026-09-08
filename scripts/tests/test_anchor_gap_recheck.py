"""118 provenance/dispatch tests; no model fitting or evaluation is launched."""
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
import anchor_gap_recheck as a
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_training.dataset import TrainingMatrix


@pytest.fixture
def cfg():
    return a.load_config()


@pytest.fixture
def frozen(cfg):
    return {'source_hash': 'source', 'config_hash': a.s.p.gate_config_hash(cfg),
            'sources': {'snapshot_sha256': 'snapshot'}}


@pytest.fixture
def matrix():
    cols = a.s.p.columns_from_model() + a.s.p.OBSERVATION_COLUMNS
    return TrainingMatrix(pd.DataFrame(columns=cols), cols, [])


def context(rid, year):
    return RaceContext(rid, dt.date(year, 1, 6), (HorseEntry('h1'), HorseEntry('h2')))


def fold(year=2019):
    return SimpleNamespace(valid_year=year, train=[SimpleNamespace(context=context('train', year - 1))], valid=[])


def test_only_two_missing_warmup_jobs(cfg, frozen, matrix):
    jobs = [a.job_for(a.factory(cfg, frozen, matrix, [], seed), fold(), seed) for seed in (43, 44)]
    assert len({j['key'] for j in jobs}) == 2
    assert {(j['seed'], j['year'], j['arm']) for j in jobs} == {(43, 2019, 'anchor'), (44, 2019, 'anchor')}
    assert jobs[0]['train_hash'] == jobs[1]['train_hash']


@pytest.mark.parametrize('seed,year', [(42, 2019), (43, 2020), (44, 2018), (99, 2019)])
def test_unregistered_jobs_rejected(cfg, frozen, matrix, seed, year):
    f = a.factory(cfg, frozen, matrix, [], 43)
    with pytest.raises(ValueError):
        a.job_for(f, fold(year), seed)


@pytest.mark.parametrize('seed', [43, 44])
def test_fresh_recipe_equals_existing116_anchor_except_cache_identity(cfg, frozen, matrix, seed):
    f = a.factory(cfg, frozen, matrix, [], seed)
    old_cfg = a.d.load_config()
    old_frozen = {**frozen, 'source_hash': 'original116', 'config_hash': a.s.p.gate_config_hash(old_cfg)}
    original = a.d.factory(old_cfg, old_frozen, matrix, [], seed, 'anchor')
    assert f.recipe_meta == original.recipe_meta
    assert f.recipe_hash == original.recipe_hash
    assert f.expected_columns == a.s.p.columns_from_model()
    assert len(f.expected_columns) == 138
    assert f.factory.recipe.weight_mask_seed == 20260810
    assert f.factory.recipe.seed == seed
    assert f.identity != original.identity


def test_smoke_recipe_is_only_structural(cfg, frozen, matrix):
    f = a.factory(cfg, frozen, matrix, [], 43, smoke=True)
    assert f.recipe_meta['n_oof_blocks'] == 2
    assert dict(f.factory.recipe.params)['n_estimators'] == 5


@pytest.mark.parametrize('seed,year,expected', [(42, 2019, '113'), (42, 2026, '113'), (43, 2020, '116'), (44, 2026, '116')])
def test_anchor_dispatch_uses_original_factories(cfg, frozen, matrix, monkeypatch, seed, year, expected):
    target = a.AnchorFactory(cfg, frozen, matrix, [], seed)
    calls = []
    class Source:
        recipe_meta = target.recipe_meta
        expected_columns = target.expected_columns
        def __init__(self, kind):
            self.kind = kind
        def fit(self, train, **kwargs):
            calls.append((self.kind, train[0].race_date.year + 1))
            return self.kind
    monkeypatch.setattr(a.s, 'ReadOnlyFactory', lambda *_: Source('113'))
    monkeypatch.setattr(a.d, 'ReadOnlyFactory', lambda *_: Source('116'))
    assert target.fit([context('train', year - 1)]) == expected
    assert calls == [(expected, year)]


def test_anchor_dispatch_rejects_native_recipe_mismatch(cfg, frozen, matrix, monkeypatch):
    target = a.AnchorFactory(cfg, frozen, matrix, [], 43)
    monkeypatch.setattr(a.d, 'ReadOnlyFactory', lambda *_: SimpleNamespace(recipe_meta={}, expected_columns=target.expected_columns))
    with pytest.raises(ValueError, match='recipe/column'):
        target.fit([context('train', 2019)])


def test_missing118_warmup_does_not_fallback_to_refitting_or_oldseed(cfg, frozen, matrix, monkeypatch):
    target = a.AnchorFactory(cfg, frozen, matrix, [], 43)
    target.frozen['jobs'] = [{'key': 'key', 'seed': 43, 'year': 2019, 'train_hash': 'th'}]
    monkeypatch.setattr(a, 'completed', lambda _: False)
    monkeypatch.setattr(a.s, 'ReadOnlyFactory', lambda *_: pytest.fail('Must not fallback to seed42'))
    monkeypatch.setattr(a.d, 'ReadOnlyFactory', lambda *_: pytest.fail('116 has no anchor2019'))
    with pytest.raises(ValueError, match='warmup cache'):
        target.fit([context('train', 2018)])


@pytest.mark.parametrize('field,value', [('selected_columns', 125), ('selected_arm', 'pruning'), ('new_fit_jobs', 30), ('fresh_fit_years', [2019, 2020]), ('deployment_seed', 44)])
def test_fixed_scope_refuses_change(tmp_path, monkeypatch, cfg, field, value):
    cfg[field] = value
    (tmp_path / 'gate-config.json').write_text(json.dumps(cfg))
    (tmp_path / 'gate-config.hash.txt').write_text(a.s.p.gate_config_hash(cfg))
    monkeypatch.setattr(a, 'SPEC', tmp_path)
    with pytest.raises(ValueError, match='scope'):
        a.load_config()


def test_seed_config_does_not_change_ci_noise(cfg):
    for seed in (42, 43, 44):
        changed = a.seed_config(cfg, seed)
        assert changed['seed_noise']['k_seeds'] == 1
        assert changed['seed_noise']['sd_fold'] == .001816
        changed['arms']['seed'] = 42
        assert changed == cfg


def test_cache_redirect_restores_source_global_on_exception(tmp_path, monkeypatch):
    original = a.s.p.WORK
    monkeypatch.setattr(a, 'WORK', tmp_path)
    with pytest.raises(RuntimeError):
        with a.isolated_cache():
            assert a.s.p.WORK == tmp_path
            raise RuntimeError('failure')
    assert a.s.p.WORK == original


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(a, 'WORK', tmp_path)
    (tmp_path / 'run-freeze.json').write_text('{}')
    return tmp_path


@pytest.mark.parametrize('tamper', ['none', 'cache', 'seed', 'freeze', 'imported'])
def test_receipt_guard(work, tamper):
    path = work / 'cache/key.pkl'
    path.parent.mkdir()
    path.write_bytes(b'fresh')
    job = {'key': 'key', 'seed': 43, 'year': 2019, 'arm': 'anchor'}
    assert a.completed(job) is False
    receipt = {'job': deepcopy(job), 'cache_sha256': a.s.p.digest(path),
        'run_freeze_sha256': a.s.p.digest(work / 'run-freeze.json'), 'imported': False, 'model_threads': 1}
    if tamper == 'cache':
        path.write_bytes(b'changed')
    elif tamper == 'seed':
        receipt['job']['seed'] = 44
    elif tamper == 'freeze':
        receipt['run_freeze_sha256'] = 'changed'
    elif tamper == 'imported':
        receipt['imported'] = True
    a.write_json(a.receipt_path('key'), receipt)
    if tamper == 'none':
        assert a.completed(job)
    else:
        with pytest.raises(ValueError):
            a.completed(job)


def test_old_coefficients_stay_outside118(work):
    for seed in (42, 43, 44):
        assert not a.old_coefficient_path(seed).is_relative_to(work)
    assert a.old_coefficient_path(42) == a.g.WORK / 'coefficients.json'
    assert a.old_coefficient_path(43) == a.d.WORK / 'seed-43/coefficients.json'


@pytest.fixture
def rows_source(tmp_path, monkeypatch):
    root = tmp_path / 'old'
    root.mkdir()
    old = [{'race_id': 'r', 'race_day': '2020-01-01', 'active_winner_nll': 2., 'candidate_winner_nll': 1.9}]
    (root / 'evidence.json').write_text(json.dumps({'rows': old}))
    (root / 'report.json').write_text(json.dumps({'evidence_path': str(root / 'evidence.json')}))
    monkeypatch.setattr(a.d, 'result_path', lambda *_: root / 'report.json')
    monkeypatch.setattr(a, 'SPEC', tmp_path / 'new')
    return old


@pytest.mark.parametrize('contrast,baseline', [('anchor', 2.), ('retained', 1.9)])
def test_each_baseline_equals_corresponding_original116_nll(rows_source, contrast, baseline):
    rows = [{'race_id': 'r', 'race_day': '2020-01-01', 'active_winner_nll': baseline, 'candidate_winner_nll': 1.8}]
    a.validate_rows(rows, 43, {'id': contrast})
    rows[0]['active_winner_nll'] += 1e-15
    with pytest.raises(ValueError, match='baseline per-race'):
        a.validate_rows(rows, 43, {'id': contrast})


def test_candidate_losses_must_match_between_comparisons(rows_source):
    other = a.result_path(43, 'retained')
    other.parent.mkdir(parents=True)
    evidence = other.parent / 'rows.json'
    evidence.write_text(json.dumps({'rows': [{'race_id': 'r', 'race_day': '2020-01-01', 'candidate_winner_nll': 1.7}]}))
    other.write_text(json.dumps({'evidence_path': str(evidence)}))
    rows = [{'race_id': 'r', 'race_day': '2020-01-01', 'active_winner_nll': 2., 'candidate_winner_nll': 1.8}]
    with pytest.raises(ValueError, match='candidate losses'):
        a.validate_rows(rows, 43, {'id': 'anchor'})


def test_source_hash_binds_new_aggregator(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    path = scripts / 'anchor_gap_summary.py'
    path.write_text('original')
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    monkeypatch.setattr(a.d, 'source_hash', lambda: 'fixed116')
    before = a.source_hash()
    path.write_text('changed')
    assert before != a.source_hash()


def test_worker_preserves_unreceipted_cache(work, monkeypatch):
    path = work / 'cache/key.pkl'
    path.parent.mkdir()
    path.write_bytes(b'unverified')
    monkeypatch.setattr(a, 'verify', lambda: ({}, {'jobs': [{'key': 'key'}]}))
    with pytest.raises(ValueError, match='Unreceipted'):
        a.worker('key')
    assert path.read_bytes() == b'unverified'


def test_both_coefficient_provenance_uses_distinct_paths_and_native_base_hash(work, cfg, frozen, matrix, monkeypatch):
    candidate = a.AnchorFactory(cfg, frozen, matrix, [], 43)
    a.write_json(a.seed_area(43) / 'coefficients.json', {'base_recipe_hash': candidate.recipe_hash})
    original = a.d.factory(a.d.load_config(), frozen, matrix, [], 43, 'pruning')
    old = work / 'old-coefficients.json'
    old.write_text('{}')
    old_report = work / 'old-report.json'
    old_report.write_text(json.dumps({'candidate_recipe_meta': {'base_recipe': original.recipe_meta}}))
    monkeypatch.setattr(a, 'old_coefficient_path', lambda _: old)
    monkeypatch.setattr(a.d, 'result_path', lambda *_: old_report)
    provenance = a.coefficient_provenance(43, {'id': 'retained'})
    assert provenance['candidate']['base_recipe_hash'] == candidate.recipe_hash
    assert provenance['baseline']['base_recipe_hash'] == original.recipe_hash
    assert provenance['candidate']['path'] != provenance['baseline']['path']
    assert provenance['candidate']['base_arm'] == 'anchor138'
    assert provenance['baseline']['base_arm'] == 'pruning125'
    assert a.coefficient_provenance(43, {'id': 'anchor'})['baseline'] is None


def test_complete_result_resume_rechecks_both_coefficient_sources(work, cfg, frozen, monkeypatch):
    monkeypatch.setattr(a, 'SPEC', work / 'spec')
    contrast = {'id': 'retained', 'baseline': 'retained'}
    area = a.seed_area(43)
    evidence = area / 'retained-evidence.json'
    a.write_json(evidence, {'rows': []})
    provenance = {'candidate': {'sha256': 'candidate'}, 'baseline': {'sha256': 'old-retained'}}
    value = {'artifact_kind': 'anchor_gap_research_report', 'can_adopt': False, 'eligible_for_verdict': False,
        'training_seed': 43, 'contrast': contrast, 'study_config_hash': a.s.p.gate_config_hash(cfg),
        'seed_config_hash': a.s.p.gate_config_hash(a.seed_config(cfg, 43)),
        'run_freeze_sha256': a.s.p.digest(work / 'run-freeze.json'),
        'coefficient_provenance': deepcopy(provenance), 'evidence_path': str(evidence),
        'evidence_sha256': a.s.p.digest(evidence)}
    value['research_disposition'] = a.s.research.assess_research(value)
    output = a.result_path(43, 'retained')
    a.write_json(output, value)
    a.write_json(area / 'retained-receipt.json', {'report_sha256': a.s.p.digest(output),
        'evidence_sha256': a.s.p.digest(evidence), 'freeze_sha256': a.s.p.digest(work / 'run-freeze.json'), 'seed': 43})
    calls = []
    monkeypatch.setattr(a, 'coefficient_provenance', lambda *_: deepcopy(provenance))
    monkeypatch.setattr(a, 'verified_coefficients', lambda *_: calls.append('candidate'))
    monkeypatch.setattr(a, 'old_coefficients', lambda *_: calls.append('retained'))
    monkeypatch.setattr(a, 'validate_rows', lambda *_: None)
    assert a.verified_result(43, contrast, cfg, frozen)
    assert calls == ['candidate', 'retained']
    provenance['baseline']['sha256'] = 'changed'
    with pytest.raises(ValueError, match='both coefficient'):
        a.verified_result(43, contrast, cfg, frozen)


def test_worker_quarantines_output_when_fit_fails_after_write(work, monkeypatch):
    job = {'key': 'key', 'seed': 43, 'year': 2019, 'arm': 'anchor', 'train_hash': 'th'}
    monkeypatch.setattr(a, 'verify', lambda: ({}, {'jobs': [job]}))
    monkeypatch.setattr(a.s, 'inputs', lambda *_: (None, [], {2019: SimpleNamespace(train=[], valid=[])}))
    monkeypatch.setattr(a, 'job_for', lambda *_: job)
    cache = work / 'cache/key.pkl'
    def failure(*args, **kwargs):
        cache.parent.mkdir()
        cache.write_bytes(b'failed fit')
        raise RuntimeError('fit failure')
    monkeypatch.setattr(a, 'factory', lambda *_: SimpleNamespace(fit=failure))
    with pytest.raises(RuntimeError):
        a.worker('key')
    assert not cache.exists() and not a.receipt_path('key').exists()
    preserved = list((work / 'prefill').glob('unverified-key-*.pkl'))
    assert len(preserved) == 1 and preserved[0].read_bytes() == b'failed fit'
