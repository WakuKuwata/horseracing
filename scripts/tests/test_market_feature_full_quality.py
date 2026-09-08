"""126 conditional selection/full-scope/cache tests; no database or booster fitting."""
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
import market_feature_full_quality as q
from horseracing_eval.predictor import RaceContext, HorseEntry
from horseracing_eval.decision import ConfirmatoryContractError


@pytest.fixture
def cfg(): return q.load_config()


@pytest.fixture
def matrix():
    cols = q.s.p.columns_from_model() + q.s.p.OBSERVATION_COLUMNS + q.m.ADDITIONS
    frame = pd.DataFrame({c: [0., 1.] for c in cols})
    frame.insert(0, 'horse_id', ['H', 'X']); frame.insert(0, 'race_id', ['r', 'r'])
    return q.m.TrainingMatrix(frame, cols, [])


@pytest.fixture
def frozen(cfg, matrix):
    f = {'matrix_sha256': 'matrix', 'source_hash': 'source126', 'config_hash': q.s.p.gate_config_hash(cfg),
         'selected_candidates': q.UNIVERSE.copy(), 'sources': {'source122_recipe_hashes': {}}}
    f['sources']['source122_recipe_hashes'] = {n: q.m.Factory(q.m.load_config(), f, matrix, [], n).recipe_hash for n in q.m.NAMES}
    return f


def ctx(year): return RaceContext(str(year), dt.date(year, 1, 1), (HorseEntry('H'), HorseEntry('X')))


def folds():
    return {y: SimpleNamespace(valid_year=y, train=[SimpleNamespace(context=ctx(y - 1))], valid=[]) for y in range(2020, 2027)}


def screen(diff=-.001, **quality):
    r = {'periods': {'all': {'diff': diff}}, 'gate': {'reasons': {'top2_diff': 0., 'top3_diff': 0., 'cand_ece': .004, 'act_ece': .004, **quality}},
        'can_adopt': False, 'eligible_for_verdict': False}
    r['progression'] = q.m.progression(r)
    return r


def review(reports):
    return {'status': 'PASS', 'artifact_kind': 'market_feature_screen_independent_review', 'additional_fits': 0,
        'can_adopt': False, 'eligible_for_verdict': False,
        'comparisons': {n: {'progression': r['progression']} for n, r in reports.items()}}


def test_selection_all_passes_not_winner_ranking():
    reports = dict(zip(q.UNIVERSE, [screen(-1e-12), screen(-.1), screen(-.001)]))
    assert q.select_candidates(reports, review(reports)) == q.UNIVERSE


def test_selection_drops_only_registered_nonpasses_in_fixed_order():
    reports = dict(zip(q.UNIVERSE, [screen(-1e-12), screen(0.), screen(-.002)]))
    assert q.select_candidates(reports, review(reports)) == ['f03', 'colsample_07']
    reports['f03'] = screen(-.01, top2_diff=.001)
    assert q.select_candidates(reports, review(reports)) == ['colsample_07']


def test_zero_selected_is_valid_no_new_claim():
    reports = {n: screen(0.) for n in q.UNIVERSE}
    assert q.select_candidates(reports, review(reports)) == []


@pytest.mark.parametrize('fault', ['missing_report', 'missing_review', 'no_pass', 'changed_progression', 'can_adopt'])
def test_selection_requires_all_complete_and_independently_consistent(fault):
    reports = {n: screen() for n in q.UNIVERSE}
    audit = review(reports)
    if fault == 'missing_report': reports.pop('f05')
    elif fault == 'missing_review': audit['comparisons'].pop('f05')
    elif fault == 'no_pass': audit['status'] = 'FAIL'
    elif fault == 'changed_progression': audit['comparisons']['f03']['progression'] = 'DEFER'
    elif fault == 'can_adopt': reports['f03']['can_adopt'] = True
    with pytest.raises(ValueError): q.select_candidates(reports, audit)


@pytest.mark.parametrize('selected', [[], ['f03'], ['f03', 'colsample_07'], q.UNIVERSE])
def test_exact_seven_jobs_per_selected_with_fixed_schedule(cfg, frozen, matrix, selected):
    frozen['selected_candidates'] = selected.copy()
    jobs = q.training_jobs(cfg, frozen, matrix, [], folds())
    assert len(jobs) == 7 * len(selected)
    assert [(j['arm'], j['year']) for j in jobs] == [(n, y) for n in selected for y in q.YEARS]
    assert len({j['key'] for j in jobs}) == len(jobs)
    assert all(j['arm'] != 'baseline' for j in jobs)


def test_missing_fold_and_screen_recipe_mismatch_fail(cfg, frozen, matrix):
    f = folds(); f.pop(2020)
    with pytest.raises(ValueError): q.training_jobs(cfg, frozen, matrix, [], f)
    frozen['sources']['source122_recipe_hashes']['f03'] = 'changed'
    with pytest.raises(ValueError, match='recipe'): q.training_jobs(cfg, frozen, matrix, [], folds())


@pytest.mark.parametrize('name', q.m.NAMES)
def test_native122_factory_parameters_columns_types_are_identical(cfg, frozen, matrix, name):
    new = q.FreshFactory(cfg, frozen, matrix, [], name)
    old_frozen = {**frozen, 'source_hash': 'original122', 'config_hash': q.s.p.gate_config_hash(q.m.load_config())}
    old = q.m.Factory(q.m.load_config(), old_frozen, matrix, [], name)
    assert new.recipe_hash == old.recipe_hash and new.recipe_meta == old.recipe_meta
    assert new.expected_columns == old.expected_columns and new.dtypes() == old.dtypes()
    assert new.factory.recipe.resolved_params() == old.factory.recipe.resolved_params()
    assert new.identity != old.identity and new.identity[-1] == 'full'


def test_unselected_full_candidates_and_fresh_baseline_are_prohibited(cfg, frozen, matrix):
    frozen['selected_candidates'] = ['f03']
    with pytest.raises(ValueError, match='Unselected'):
        q.FreshFactory(cfg, frozen, matrix, [], 'f05')
    baseline = q.FreshFactory(cfg, frozen, matrix, [], 'baseline')
    with pytest.raises(ValueError, match='No new full baseline'):
        baseline.fit([ctx(2019)])


def test_original141_projection_preserves_values_and_metadata(matrix):
    projected = q.source141(matrix)
    pd.testing.assert_frame_equal(projected.frame, matrix.frame.drop(columns=q.m.ADDITIONS), check_exact=True)
    assert projected.feature_cols == q.s.p.columns_from_model() + q.s.p.OBSERVATION_COLUMNS
    assert projected.categorical_cols == matrix.categorical_cols
    assert projected.build_audit == matrix.build_audit


def test_baseline_keeps_native113_identity_and_never_calls_fresh_fit(cfg, frozen, matrix, monkeypatch):
    target = q.FreshFactory(cfg, frozen, matrix, [], 'baseline')
    calls = []
    native = SimpleNamespace(expected_columns=target.expected_columns, recipe_meta=target.recipe_meta,
        fit=lambda train, **kw: calls.append(train) or 'native predictions')
    monkeypatch.setattr(q.s, 'verify', lambda: ('original113cfg', 'original113freeze'))
    def make_old(c, f, data, races, name):
        assert c == 'original113cfg' and f == 'original113freeze' and name == 'pruning'
        assert len(data.feature_cols) == 141
        return native
    monkeypatch.setattr(q.s, 'ReadOnlyFactory', make_old)
    b = q.BaselineFactory(cfg, frozen, matrix, [])
    assert b.fit([ctx(2019)]) == 'native predictions'
    assert calls == [[ctx(2019)]]


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(q, 'WORK', tmp_path / 'work')
    monkeypatch.setattr(q, 'SPEC', tmp_path / 'spec')
    q.WORK.mkdir(); q.SPEC.mkdir()
    (q.WORK / 'run-freeze.json').write_text('{}')
    return q.WORK


def test_work_redirection_is_only_fitting_and_restored_after_failure(cfg, frozen, matrix, work, monkeypatch):
    original = q.m.WORK, q.s.p.WORK, q.s.old.WORK
    def failure(*args, **kwargs):
        assert q.m.WORK == work
        assert (q.s.p.WORK, q.s.old.WORK) == original[1:]
        raise RuntimeError('failure')
    monkeypatch.setattr(q.m.Factory, 'fit', failure)
    f = q.FreshFactory(cfg, frozen, matrix, [], 'f03')
    with pytest.raises(RuntimeError): f.fit([ctx(2019)])
    assert (q.m.WORK, q.s.p.WORK, q.s.old.WORK) == original


@pytest.mark.parametrize('key,value', [('selection_rule', 'best candidate'), ('performance_early_stop', True),
    ('year_order', list(range(2020, 2027))), ('baseline_new_fits', 1), ('max_workers', 3), ('seed', 43),
    ('candidate_universe', ['f03']), ('eval_window', {}), ('study_arms', [])])
def test_rehashed_protocol_change_is_rejected(cfg, tmp_path, monkeypatch, key, value):
    cfg[key] = value
    (tmp_path / 'gate-config.json').write_text(json.dumps(cfg))
    (tmp_path / 'gate-config.hash.txt').write_text(q.s.p.gate_config_hash(cfg))
    monkeypatch.setattr(q, 'SPEC', tmp_path)
    with pytest.raises((ValueError, ConfirmatoryContractError)): q.load_config()


@pytest.mark.parametrize('tamper', ['none', 'status', 'method_sha256', 'matrix_sha256', 'source_frames_sha256', 'horse_rows', 'same_day_excluded'])
def test_scalar_feature_review_binds_full_cells_and_sources(tamper):
    hashes = {'method_sha256': 'method', 'run_freeze_sha256': 'freeze', 'matrix_sha256': 'matrix', 'source_frames_sha256': 'frames'}
    audit = {'artifact_kind': 'market_feature_scalar_reconstruction', 'status': 'PASS', 'horse_rows': 958011,
        'can_adopt': False, 'eligible_for_verdict': False, 'additional_fits': 0,
        'feature_cells': 10538121, 'strict_prior_dates': True, 'same_day_excluded': True, 'target_market_unused': True, **hashes}
    if tamper != 'none': audit[tamper] = 'changed'
    if tamper == 'none': q.check_feature_review(audit, hashes)
    else:
        with pytest.raises(ValueError): q.check_feature_review(audit, hashes)


def test_train_completes_each_candidate_and_does_not_drop_later_after_quality_block(cfg, frozen, work, monkeypatch):
    frozen['selected_candidates'] = ['f03', 'f05']
    frozen['jobs'] = [{'key': f'{n}-{y}', 'arm': n, 'year': y} for n in frozen['selected_candidates'] for y in q.YEARS]
    done, events = set(), []
    monkeypatch.setattr(q, 'verify', lambda: (cfg, frozen))
    monkeypatch.setattr(q, 'completed', lambda j: j['key'] in done)
    def launch(j, run_id):
        assert not (j['arm'] == 'f05' and ('evaluated', 'f03') not in events)
        done.add(j['key']); events.append(('fit', j['arm']))
    monkeypatch.setattr(q, 'launch', launch)
    def evaluate(name, *args):
        assert all(j['key'] in done for j in frozen['jobs'] if j['arm'] == name)
        events.append(('evaluated', name))
        return {'state': 'BLOCKED'}
    monkeypatch.setattr(q, 'evaluate_one', evaluate)
    q.write_json(q.SPEC / 'evidence/smoke.json', {'structure': 'PASS', 'arm_names': ['baseline', 'f03', 'f05'],
        'run_freeze_sha256': q.s.p.digest(work / 'run-freeze.json')})
    q.train(2)
    assert len(done) == 14
    assert events[-1] == ('evaluated', 'f05')
    assert not (work / 'running.lock').exists()


def test_no_candidates_smoke_and_summary_require_no_fit(cfg, frozen, work, monkeypatch):
    frozen['selected_candidates'], frozen['jobs'] = [], []
    monkeypatch.setattr(q, 'verify', lambda: (cfg, frozen))
    monkeypatch.setattr(q, 'inputs', lambda *a, **kw: pytest.fail('No smoke/input fit for empty selection'))
    q.smoke(); q.train(2); q.evaluate()
    r = q.s.read_json(q.SPEC / 'verdict.json')
    assert r['research_decision'] == 'NO_ADVANCING_CANDIDATES'
    assert r['new_outer_jobs'] == r['new_booster_fits'] == 0
    assert r['reports'] == {} and r['can_adopt'] is False


def test_no_partial_year_performance_evaluation(cfg, frozen, work, monkeypatch):
    frozen['jobs'] = [{'key': str(y), 'arm': 'f03', 'year': y} for y in q.YEARS]
    monkeypatch.setattr(q, 'completed', lambda j: j['year'] >= 2024)
    monkeypatch.setattr(q, 'inputs', lambda *a, **kw: pytest.fail('No partial-year evaluation'))
    with pytest.raises(ValueError, match='all seven'): q.evaluate_one('f03', cfg, frozen)


def test_worker_orphan_is_preserved_without_loading(cfg, frozen, work, monkeypatch):
    frozen['jobs'] = [{'key': 'orphan'}]
    path = work / 'cache/orphan.pkl'; path.parent.mkdir(); path.write_bytes(b'old')
    monkeypatch.setattr(q, 'verify', lambda: (cfg, frozen))
    monkeypatch.setattr(q, 'inputs', lambda *a: pytest.fail('No reload before orphan diagnosis'))
    with pytest.raises(ValueError, match='Unreceipted'): q.worker('orphan')
    assert path.read_bytes() == b'old'


def test_fresh_cache_receipt_binds_job_and_run_freeze(work):
    job = {'key': 'new', 'arm': 'f03', 'year': 2026}
    path = work / 'cache/new.pkl'; path.parent.mkdir(); path.write_bytes(b'cache')
    q.write_json(q.receipt_path('new'), {'job': job, 'cache_sha256': q.s.p.digest(path),
        'run_freeze_sha256': q.s.p.digest(work / 'run-freeze.json'), 'model_threads': 1, 'imported': False})
    assert q.completed(job)
    (work / 'run-freeze.json').write_text('changed')
    with pytest.raises(ValueError): q.completed(job)


@pytest.mark.parametrize('artifact', ['report', 'evidence', 'receipt'])
def test_incomplete_report_resume_is_rejected(cfg, frozen, work, artifact):
    p = {'report': q.result_path('f03'), 'evidence': work / 'f03-evidence.json', 'receipt': q.result_receipt('f03')}[artifact]
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}')
    with pytest.raises(ValueError, match='Incomplete'): q.verified_result('f03', cfg, frozen)


@pytest.fixture
def original_report():
    # A completed, immutable seven-fold source is a useful regression fixture for
    # the official nested recent/subgroup schema; this does not run evaluation.
    r = q.s.read_json(q.d.result_path(42, 'increment'))
    e = q.s.read_json(r['evidence_path'])
    return r, e


def test_full_recent_and_subgroup_readouts_accept_official_source(cfg, original_report):
    r, e = original_report
    q.validate_full_readouts(r, e, cfg)


@pytest.mark.parametrize('fault', ['missing_recent', 'recent_diff', 'recent_risk', 'recent_falseflag',
    'missing_canonical', 'missing_nk_ci', 'nk_risk', 'target_year'])
def test_full_quality_readouts_cannot_hide_missing_or_inconsistent_ci(cfg, original_report, fault):
    r, e = original_report
    if fault == 'missing_recent': r['gate']['reasons']['recent']['windows'].pop('recent_3y')
    elif fault == 'recent_diff': r['periods']['recent_3y']['diff'] = -.1
    elif fault == 'recent_risk': r['gate']['reasons']['recent']['windows']['recent_3y']['residual_risk'] = 99.
    elif fault == 'recent_falseflag': r['gate']['reasons']['recent']['pass'] = False
    elif fault == 'missing_canonical': r['subgroups']['horse_subgroups'].pop('canonical')
    elif fault == 'missing_nk_ci': r['subgroups']['horse_subgroups']['nk']['bootstrap_ci'].pop('ci_high')
    elif fault == 'nk_risk': r['subgroups']['critical_residual_risk']['nk'] = 99.
    elif fault == 'target_year': r['target_year'] = 2025
    with pytest.raises(ValueError): q.validate_full_readouts(r, e, cfg)


def make_result(cfg, frozen, work, original_report):
    r, e = original_report
    frozen['selected_candidates'] = ['f03']
    frozen['sources']['raw125_evidence_path'] = r['evidence_path']
    frozen['population'] = {'n_races': r['n_races'], 'n_eligible': r['n_eligible'], 'n_days': 715,
        'race_id_set_hash': r['race_id_set_hash'], 'eligible_id_days': [[x['race_id'], x['race_day']] for x in e['rows']]}
    frozen['arm_recipe_hashes'] = {'f03': r['candidate_recipe_hash'], 'baseline': r['active_recipe_hash']}
    frozen['arm_columns'] = {'f03': ['f03_fixture'], 'baseline': ['baseline_fixture']}
    frozen['jobs'] = [{'key': f'f03-{y}', 'arm': 'f03', 'year': y} for y in q.YEARS]
    r.update(artifact_kind='market_feature_full_quality_report', candidate='f03',
        candidate_columns=['f03_fixture'], baseline_columns=['baseline_fixture'],
        gate_config_hash=q.s.p.gate_config_hash(cfg), study_config_hash=q.s.p.gate_config_hash(cfg),
        run_freeze_sha256=q.s.p.digest(work / 'run-freeze.json'), evidence_path=str(work / 'f03-evidence.json'),
        primary_loss_changed=any(x['diff'] != 0 for x in e['rows']))
    e['gate_config_hash'] = q.s.p.gate_config_hash(cfg)
    r['research_disposition'] = q.s.research.assess_research(r)
    return r, e


def persist_result(r, e, work):
    (work / 'f03-evidence.json').write_text(json.dumps(e))
    r['evidence_sha256'] = q.s.p.digest(work / 'f03-evidence.json')
    q.result_path('f03').parent.mkdir(parents=True, exist_ok=True)
    q.result_path('f03').write_text(json.dumps(r))
    q.result_receipt('f03').write_text(json.dumps({'report_sha256': q.s.p.digest(q.result_path('f03')),
        'evidence_sha256': q.s.p.digest(work / 'f03-evidence.json'),
        'run_freeze_sha256': q.s.p.digest(work / 'run-freeze.json'), 'candidate': 'f03'}))


def test_completed_result_resume_revalidates_full_mean_even_reissued_receipt(cfg, frozen, work, original_report, monkeypatch):
    r, e = make_result(cfg, frozen, work, original_report)
    persist_result(r, e, work)
    monkeypatch.setattr(q, 'completed', lambda job: True)
    assert q.verified_result('f03', cfg, frozen)
    r['periods']['all']['candidate'] -= .01
    persist_result(r, e, work)
    with pytest.raises(ValueError, match='mean/primary'):
        q.verified_result('f03', cfg, frozen)


def test_completed_result_requires_every_new_receipt(cfg, frozen, work, original_report, monkeypatch):
    r, e = make_result(cfg, frozen, work, original_report)
    persist_result(r, e, work)
    monkeypatch.setattr(q, 'completed', lambda job: job['year'] != 2020)
    with pytest.raises(ValueError, match='all seven'):
        q.verified_result('f03', cfg, frozen)


@pytest.mark.parametrize('fault', ['seed_count', 'fold_count', 'quality_flag', 'full_race_hash', 'column_order', 'baseline_loss'])
def test_full_result_contract_stops_identity_or_quality_tamper(cfg, frozen, work, original_report, fault):
    r, e = make_result(cfg, frozen, work, original_report)
    if fault == 'seed_count': r['seed_noise']['k_seeds'] = 3
    elif fault == 'fold_count': r['seed_noise']['n_folds'] = 3
    elif fault == 'quality_flag':
        r['gate']['top_noninferior'] = False
        r['research_disposition'] = q.s.research.assess_research(r)
    elif fault == 'full_race_hash': r['race_id_set_hash'] = e['race_id_set_hash'] = 'same wrong full set'
    elif fault == 'column_order': r['candidate_columns'] = ['wrong']
    elif fault == 'baseline_loss':
        e['rows'][0]['active_winner_nll'] += .01
        e['rows'][0]['candidate_winner_nll'] += .01
        e['rows'][0]['diff'] = e['rows'][0]['candidate_winner_nll'] - e['rows'][0]['active_winner_nll']
        import numpy as np
        r['periods']['all']['candidate'] = float(np.mean([x['candidate_winner_nll'] for x in e['rows']]))
        r['periods']['all']['active'] = float(np.mean([x['active_winner_nll'] for x in e['rows']]))
        r['periods']['all']['diff'] = r['periods']['all']['candidate'] - r['periods']['all']['active']
        r['gate']['reasons']['winner_nll_diff'] = r['periods']['all']['diff']
    with pytest.raises(ValueError): q.validate_result(r, e, 'f03', cfg, frozen)
