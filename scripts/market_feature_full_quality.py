"""126: conditionally selected122 raw-model candidates, seven full-quality folds each."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
import datetime as dt
import gc
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import market_feature_screen as m
import gap_seed_recheck as d
from horseracing_eval.dataset import population_masks
from horseracing_eval.hashing import race_set_hash
from horseracing_eval.paired import _clip_nll, _score_arm, DEFAULT_BAND_EDGES
from horseracing_eval.gates import _window_start
from horseracing_eval.subgroups import three_way, residual_risk, subgroup_guard_status

s, ROOT = m.s, m.ROOT
SPEC = ROOT / 'specs/126-market-feature-full-quality'
WORK = ROOT / 'artifacts/126-market-feature-full-quality'
UNIVERSE = ['f03', 'f05', 'colsample_07']
YEARS = [2026, 2025, 2024, 2020, 2021, 2022, 2023]
SELECTION = 'All and only122 ADVANCE_TO_FULL_RESEARCH after all3 complete reports and independent audit PASS; preserve universe order'
write_json = m.write_json


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg) != expected:
        raise ValueError('126 config hash changed')
    s.p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg, root=ROOT)
    old = d.load_config()
    for key in ('arms', 'eval_window', 'bootstrap', 'seed_noise', 'min_effect_delta',
                'top_noninferior', 'calibration', 'recent_guard', 'subgroup_guard'):
        if cfg[key] != old[key]:
            raise ValueError(f'126 fixed full recipe/quality changed: {key}')
    if (cfg['candidate_universe'] != UNIVERSE or cfg['selection_rule'] != SELECTION
        or cfg['study_arms'] != m.registered_arms() or cfg['contrasts'] != m.CONTRASTS
        or cfg['year_order'] != YEARS or cfg['seed'] != 42
        or cfg['outer_jobs_per_candidate'] != 7 or cfg['max_new_outer_jobs'] != 21
        or cfg['booster_fits_per_outer'] != 8 or cfg['baseline_new_fits'] != 0
        or cfg['max_workers'] != 2 or cfg['model_threads'] != 1
        or cfg['candidate_completion_order'] != 'Complete seven folds and report per candidate before the next candidate'
        or cfg['performance_early_stop'] is not False or cfg['smoke'] != m.load_config()['smoke']
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False
        or cfg['research_policy'] != 'small_gain_research_v1'):
        raise ValueError('126 selection/job/scope protocol changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '122_source': m.source_hash(),
        '116_source': d.source_hash(), 'research_policy': s.p.digest(s.research.__file__)})


def select_candidates(reports, review):
    if set(reports) != set(UNIVERSE) or set(review.get('comparisons', {})) != set(UNIVERSE):
        raise ValueError('All three122 results required before selection')
    if (review.get('status') != 'PASS' or review.get('artifact_kind') != 'market_feature_screen_independent_review'
        or review.get('additional_fits') != 0 or review.get('can_adopt') is not False or review.get('eligible_for_verdict') is not False):
        raise ValueError('Independent122 audit PASS required')
    selected = []
    for name in UNIVERSE:
        state = m.progression(reports[name])
        if (reports[name].get('progression') != state or review['comparisons'][name].get('progression') != state
            or reports[name].get('can_adopt') is not False or reports[name].get('eligible_for_verdict') is not False):
            raise ValueError('122 independently audited progression mismatch')
        if state == 'ADVANCE_TO_FULL_RESEARCH': selected.append(name)
    return selected


def check_feature_review(review, hashes):
    expected = {'artifact_kind': 'market_feature_scalar_reconstruction', 'status': 'PASS',
        'can_adopt': False, 'eligible_for_verdict': False, 'additional_fits': 0,
        'horse_rows': 958011, 'feature_cells': 10538121, 'strict_prior_dates': True,
        'same_day_excluded': True, 'target_market_unused': True, **hashes}
    if any(review.get(k) != value for k, value in expected.items()):
        raise ValueError('122 independent scalar feature audit/hash binding changed')
    if review.get('can_adopt') is not False or review.get('eligible_for_verdict') is not False:
        raise ValueError('Scalar feature audit must remain research only')


def source_state():
    cfg122, frozen122 = m.verify()
    if (m.WORK / 'running.lock').exists() or not all(m.completed(j) for j in frozen122['jobs']):
        raise ValueError('All122 jobs must finish before126 preparation')
    reports, hashes = {}, {}
    paths = [m.WORK / 'run-freeze.json', m.WORK / 'matrix.pkl', m.WORK / 'feature-audit.json',
        m.SPEC / 'gate-config.json', m.SPEC / 'gate-config.hash.txt', m.SPEC / 'verdict.json',
        m.SPEC / 'evidence/independent-review.py', m.SPEC / 'evidence/independent-review.json',
        m.SPEC / 'evidence/independent-feature-review.py', m.SPEC / 'evidence/independent-feature-review.json']
    feature_review = s.read_json(m.SPEC / 'evidence/independent-feature-review.json')
    check_feature_review(feature_review, {
        'method_sha256': s.p.digest(m.SPEC / 'evidence/independent-feature-review.py'),
        'run_freeze_sha256': s.p.digest(m.WORK / 'run-freeze.json'), 'matrix_sha256': frozen122['matrix_sha256'],
        'source_frames_sha256': s.p.digest(s.p.WORK / 'source-frames.pkl')})
    caches = {}
    for name in m.NAMES:
        if name == 'baseline' and frozen122['baseline']['mode'] == 'native_cache':
            path = Path(frozen122['baseline']['path'])
        else:
            job = next(j for j in frozen122['jobs'] if j['arm'] == name)
            path = m.WORK / 'cache' / f"{job['key']}.pkl"
            paths.append(m.receipt_path(job['key']))
        caches[name] = s.p.digest(path)
        paths.append(path)
    for name in UNIVERSE:
        if not m.verified_result(name, cfg122, frozen122):
            raise ValueError('All three122 reports must be complete')
        path = m.result_path(name)
        reports[name] = report = s.read_json(path)
        evidence = Path(report['evidence_path'])
        hashes[name] = {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(evidence)}
        paths.extend([path, evidence, m.result_receipt(name)])
    summary = s.read_json(m.SPEC / 'verdict.json')
    if (summary.get('can_adopt') is not False or summary.get('eligible_for_verdict') is not False
        or summary.get('run_freeze_sha256') != s.p.digest(m.WORK / 'run-freeze.json')
        or summary.get('reports') != {n: {'sha256': hashes[n]['report_sha256'], 'progression': reports[n]['progression']} for n in UNIVERSE}):
        raise ValueError('122 completed summary/report binding changed')
    review = s.read_json(m.SPEC / 'evidence/independent-review.json')
    if (review.get('method_sha256') != s.p.digest(m.SPEC / 'evidence/independent-review.py')
        or review.get('run_freeze_sha256') != s.p.digest(m.WORK / 'run-freeze.json')
        or review.get('summary_sha256') != s.p.digest(m.SPEC / 'verdict.json')
        or review.get('report_hashes') != hashes or review.get('cache_hashes') != caches):
        raise ValueError('122 independent method/freeze/summary/report/cache SHA binding changed')
    selected = select_candidates(reports, review)
    cfg116, frozen116 = d.verify()
    if not d.verified_result(42, 'increment', cfg116, frozen116):
        raise ValueError('Original116 raw125 reference result required')
    original_path = d.result_path(42, 'increment')
    original = s.read_json(original_path)
    paths.extend([d.WORK / 'run-freeze.json', original_path, Path(original['evidence_path']),
                  d.g.WORK / 'increment-receipt.json', s.WORK / 'run-freeze.json'])
    return {'files': {str(path): s.p.digest(path) for path in paths}, 'selected_candidates': selected,
        'snapshot_sha256': frozen122['sources']['snapshot_sha256'], 'matrix_sha256': frozen122['matrix_sha256'],
        'source122_recipe_hashes': frozen122['arm_recipe_hashes'],
        'raw125_report_path': str(original_path), 'raw125_evidence_path': original['evidence_path']}


def verify():
    cfg = load_config()
    frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != s.runtime() or frozen['sources'] != source_state()
        or frozen['selected_candidates'] != frozen['sources']['selected_candidates']
        or frozen['matrix_sha256'] != frozen['sources']['matrix_sha256']):
        raise ValueError('126 frozen source/config/runtime/selection changed')
    expected = [(name, year) for name in frozen['selected_candidates'] for year in YEARS]
    if ([(j['arm'], j['year']) for j in frozen['jobs']] != expected
        or len({j['key'] for j in frozen['jobs']}) != len(expected)):
        raise ValueError('126 frozen candidate/year jobs changed')
    return cfg, frozen


def inputs(cfg, smoke=False):
    matrix, all_races = s.reuse.load(m.WORK / 'matrix.pkl')
    win = cfg['smoke']['eval_window'] if smoke else cfg['eval_window']
    start, end = [dt.date.fromisoformat(win[k]) for k in ('from', 'to')]
    races = [r for r in all_races if r.context.race_date <= end]
    folds = {f.valid_year: f for f in s.expanding_folds(races, start.year, valid_from=start)}
    if sorted(folds) != ([2008] if smoke else list(range(2020, 2027))):
        raise ValueError('126 required full annual folds missing')
    return matrix, races, folds


def source141(matrix):
    return m.TrainingMatrix(matrix.frame.drop(columns=m.ADDITIONS),
        s.p.columns_from_model() + s.p.OBSERVATION_COLUMNS, matrix.categorical_cols, matrix.build_audit)


@contextmanager
def isolated_cache():
    original = m.WORK
    m.WORK = WORK
    try:
        yield
    finally:
        m.WORK = original


class FreshFactory(m.Factory):
    def __init__(self, cfg, frozen, matrix, races, name, smoke=False):
        super().__init__(cfg, frozen, matrix, races, name, smoke)
        self.identity = [frozen['matrix_sha256'], frozen['source_hash'], frozen['config_hash'], 'smoke' if smoke else 'full']
        if not smoke and name not in frozen['selected_candidates'] + ['baseline']:
            raise ValueError('Unselected126 candidate prohibited')

    def fit(self, train_races, *, num_threads=None):
        if self.name == 'baseline' and not self.smoke:
            raise ValueError('No new full baseline fit permitted')
        with isolated_cache():
            return super().fit(train_races, num_threads=1)


class BaselineFactory(FreshFactory):
    def __init__(self, cfg, frozen, matrix, races):
        super().__init__(cfg, frozen, matrix, races, 'baseline')
        cfg113, frozen113 = s.verify()
        self.native = s.ReadOnlyFactory(cfg113, frozen113, source141(matrix), races, 'pruning')
        if (self.native.expected_columns != self.expected_columns
            or s.strip_drops(self.native.recipe_meta) != s.strip_drops(self.recipe_meta)):
            raise ValueError('126 native baseline effective recipe/columns differ')

    def fit(self, train_races, *, num_threads=None):
        # Original113 read-only factory validates actual train/key/native tuple
        # recipe/full race and horse populations plus all three probability heads.
        return self.native.fit(train_races, num_threads=1)


def training_jobs(cfg, frozen, matrix, races, folds):
    if set(folds) != set(YEARS):
        raise ValueError('All seven full folds required')
    jobs = []
    for name in frozen['selected_candidates']:
        if name not in UNIVERSE:
            raise ValueError('Unregistered selected candidate')
        f = FreshFactory(cfg, frozen, matrix, races, name)
        if f.recipe_hash != frozen['sources']['source122_recipe_hashes'][name]:
            raise ValueError('126 candidate recipe differs from122 screen')
        for year in YEARS:
            jobs.append(m.job_for(f, folds[year]))
    if len(jobs) != 7 * len(frozen['selected_candidates']) or len({j['key'] for j in jobs}) != len(jobs):
        raise ValueError('126 exact selected outer job count differs')
    return jobs


def certify_baseline(cfg, frozen, matrix, races, folds):
    source = BaselineFactory(cfg, frozen, matrix, races)
    predictions, valid = {}, []
    records = []
    for year, fold in sorted(folds.items()):
        pred = source.fit([r.context for r in fold.train], num_threads=1)
        valid.extend(fold.valid)
        for er in fold.valid:
            predictions[er.context.race_id] = pred.predict_race(er.context)
        row = next(r for r in source.native.frozen['source_caches'] if r['arm'] == 'pruning' and r['year'] == year)
        records.append({'year': year, 'cache_key': row['key'], 'cache_sha256': row['sha256'],
                        'receipt_sha256': row['receipt_sha256'], 'train_hash': row['train_hash']})
    pop = [(er.context.race_id, str(er.context.race_date), population_masks(er).eligible) for er in valid]
    primary = []
    for er in valid:
        mask = population_masks(er)
        if mask.eligible:
            primary.append([er.context.race_id, str(er.context.race_date), _clip_nll(predictions[er.context.race_id][mask.winner_horse_id].win)])
    old = s.read_json(frozen['sources']['raw125_evidence_path'])
    if primary != [[r['race_id'], r['race_day'], r['active_winner_nll']] for r in old['rows']]:
        raise ValueError('126 native raw125 losses differ from original116 source')
    scores = _score_arm(valid, predictions, band_edges=DEFAULT_BAND_EDGES)
    original = s.read_json(frozen['sources']['raw125_report_path'])
    if (scores.winner_nll != original['periods']['all']['active']
        or scores.ece_equal_width_like['ece'] != original['gate']['reasons']['act_ece']
        or race_set_hash(r[0] for r in pop) != original['race_id_set_hash']):
        raise ValueError('126 original full population/raw125 metric differs')
    population = {'n_races': len(pop), 'n_eligible': len(primary), 'n_days': len({r[1] for r in pop}),
        'race_id_set_hash': race_set_hash(r[0] for r in pop), 'ordered_population_hash': s.p.stable_hash(pop),
        'eligible_id_days': [r[:2] for r in primary]}
    if (population['n_races'], population['n_eligible'], population['n_days']) != (23030, 22990, 715):
        raise ValueError('126 registered full population differs')
    return population, {'records': records, 'native_three_head_population_and_probability_checks': True,
        'all_original_eligible_losses_exact': True, 'original_mean_nll_and_ece_exact': True,
        'scores': asdict(scores), 'primary_rows_hash': s.p.stable_hash(primary)}


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify(); return
    cfg = load_config()
    before, sources = source_hash(), source_state()
    frozen = {'source_hash': before, 'config_hash': s.p.gate_config_hash(cfg), 'runtime': s.runtime(),
        'sources': sources, 'matrix_sha256': sources['matrix_sha256'], 'selected_candidates': sources['selected_candidates'],
        'artifact_kind': 'market_feature_full_quality_freeze', 'can_adopt': False, 'eligible_for_verdict': False}
    matrix, races, folds = inputs(cfg)
    original, original_races = s.reuse.load(s.p.WORK / 'snapshot.pkl')
    projection = source141(matrix)
    pd.testing.assert_frame_equal(projection.frame, original.frame, check_exact=True)
    if (projection.feature_cols != original.feature_cols or projection.categorical_cols != original.categorical_cols
        or projection.build_audit != original.build_audit
        or races != [r for r in original_races if r.context.race_date <= dt.date(2026, 8, 23)]):
        raise ValueError('126 original111 all-values/categories/race parity failed')
    del original, original_races, projection
    gc.collect()
    frozen['population'], frozen['baseline_certificate'] = certify_baseline(cfg, frozen, matrix, races, folds)
    frozen['jobs'] = training_jobs(cfg, frozen, matrix, races, folds)
    factories = {n: FreshFactory(cfg, frozen, matrix, races, n) for n in ['baseline'] + frozen['selected_candidates']}
    frozen['arm_recipe_hashes'] = {n: f.recipe_hash for n, f in factories.items()}
    frozen['arm_columns'] = {n: f.expected_columns for n, f in factories.items()}
    if source_hash() != before or source_state() != sources:
        raise ValueError('126 sources changed during preparation')
    write_json(WORK / 'run-freeze.json', frozen)
    verify()
    print(f'PREPARE126 PASS candidates={frozen["selected_candidates"]} outer_jobs={len(frozen["jobs"])}', flush=True)


def receipt_path(key):
    return WORK / 'prefill' / f'{key}.json'


def completed(job):
    path = receipt_path(job['key'])
    if not path.exists(): return False
    row = s.read_json(path)
    if (row.get('job') != job or row.get('cache_sha256') != s.p.digest(WORK / 'cache' / f"{job['key']}.pkl")
        or row.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or row.get('model_threads') != 1 or row.get('imported') is not False):
        raise ValueError('126 completed cache receipt changed')
    return True


def worker(key):
    cfg, frozen = verify()
    job = next(j for j in frozen['jobs'] if j['key'] == key)
    if completed(job): return
    path = WORK / 'cache' / f'{key}.pkl'
    if path.exists():
        raise ValueError('Unreceipted126 cache exists; preserve for independent diagnosis')
    matrix, races, folds = inputs(cfg)
    f = FreshFactory(cfg, frozen, matrix, races, job['arm'])
    fold = folds[job['year']]
    if m.job_for(f, fold) != job:
        raise ValueError('126 actual train/recipe/scope job differs')
    try:
        f.fit([r.context for r in fold.train], num_threads=1)
        cache = s.reuse.load(path)
        m.check_payload(cache, key, job['train_hash'], f, list(fold.valid))
        verify()
        write_json(receipt_path(key), {'job': job, 'cache_sha256': s.p.digest(path),
            'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'model_threads': 1, 'imported': False,
            'actual_params': cache['actual_params'], 'current_outer_fit_seconds': cache['elapsed_seconds'],
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)})
    except BaseException:
        if path.exists() and not receipt_path(key).exists():
            dest = WORK / 'prefill' / f'unverified-{key}-{time.time_ns()}.pkl'
            dest.parent.mkdir(parents=True, exist_ok=True)
            path.rename(dest)
        raise


def launch(job, run_id):
    log = WORK / 'prefill' / f"{run_id}-{job['key']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('x') as output:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), 'train', '--worker', job['key']],
            cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)
    if not completed(job): raise ValueError('126 worker exited without receipt')


def smoke():
    cfg, frozen = verify()
    names = ['baseline'] + frozen['selected_candidates'] if frozen['selected_candidates'] else []
    records = []
    if names:
        matrix, races, folds = inputs(cfg, smoke=True)
        fold = folds[2008]
        for name in names:
            f = FreshFactory(cfg, frozen, matrix, races, name, smoke=True)
            f.fit([r.context for r in fold.train], num_threads=1)
            key, th = s.key_for(f, fold)
            cache = s.reuse.load(WORK / 'cache' / f'{key}.pkl')
            m.check_payload(cache, key, th, f, list(fold.valid))
            records.append({'arm': name, 'feature_columns': f.expected_columns, 'feature_dtypes': f.dtypes(),
                'recipe_hash': f.recipe_hash, 'actual_params': cache['actual_params'],
                'cache_sha256': s.p.digest(WORK / 'cache' / f'{key}.pkl'), 'oof_info': cache['oof_info']})
            del f, cache
            gc.collect()
    verify()
    write_json(SPEC / 'evidence/smoke.json', {'structure': 'PASS', 'arm_names': names,
        'n_estimators': 5, 'n_oof_blocks': 2, 'records': records,
        'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'can_adopt': False, 'eligible_for_verdict': False,
        'note': 'Selected scope, params and probability wiring only; zero candidates means zero smoke fits.'})


class ReadOnlyFactory(FreshFactory):
    def fit(self, train_races, *, num_threads=None):
        th = s.train_identity(train_races)
        year = max(r.race_date.year for r in train_races) + 1
        job = next(j for j in self.frozen['jobs'] if j['arm'] == self.name and j['year'] == year)
        key = s.p.stable_hash([self.identity, self.recipe_hash, th, year])
        if key != job['key'] or th != job['train_hash'] or not completed(job):
            raise ValueError('126 cached candidate job/train/receipt differs')
        cache = s.reuse.load(WORK / 'cache' / f'{key}.pkl')
        m.check_payload(cache, key, th, self, [r for r in self.races if r.context.race_date.year == year])
        return s.p.ReplayPredictor(cache['predictions'])


def result_path(name):
    if name not in UNIVERSE: raise ValueError('Unknown126 candidate')
    return SPEC / 'evidence' / f'full-{name}.json'


def result_receipt(name):
    return WORK / f'{name}-receipt.json'


def finite_number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and np.isfinite(x)


def validate_full_readouts(row, evidence, cfg):
    recent = row['gate']['reasons']['recent']
    if (recent.get('mode') != 'non_inferiority' or recent.get('margin') != .005
        or set(recent.get('windows', {})) != {'recent_3y', 'recent_5y'}):
        raise ValueError('126 full recent-window evidence missing')
    end = dt.date.fromisoformat(cfg['eval_window']['to'])
    outcomes = []
    for years in (3, 5):
        name = f'recent_{years}y'
        start = _window_start(end, years)
        rows = [r for r in evidence['rows'] if start <= dt.date.fromisoformat(r['race_day']) <= end]
        if not rows: raise ValueError('126 empty recent window')
        cand, base = [float(np.mean([r[k] for r in rows])) for k in ('candidate_winner_nll', 'active_winner_nll')]
        period = row['periods'].get(name, {})
        window = recent['windows'][name]
        checks = [(period.get('candidate'), cand), (period.get('active'), base), (period.get('diff'), cand - base), (window.get('diff'), cand - base)]
        if (any(not finite_number(x) or abs(x - y) > 1e-12 for x, y in checks)
            or not all(finite_number(window.get(k)) for k in ('ci_low', 'ci_high'))
            or window['ci_low'] > window['ci_high'] or window.get('n_races') != len(rows)
            or window.get('n_days') != len({r['race_day'] for r in rows})):
            raise ValueError('126 recent numerical/population evidence differs')
        state = three_way(window['ci_low'], window['ci_high'], .005, point=window['diff'])
        if (window.get('decision') != state or window.get('residual_risk') != residual_risk(window['ci_high'], state)
            or window.get('point_estimate_degraded') is not (window['diff'] > 0)):
            raise ValueError('126 recent decision/residual risk differs')
        outcomes.append(state)
    if recent.get('pass') is not all(state != 'FAIL' for state in outcomes):
        raise ValueError('126 recent combined flag differs')
    groups = row['subgroups']
    states = {}
    for name in cfg['subgroup_guard']['critical_subgroups']:
        field, margin = ('race_subgroups', .005) if name == 'recent_year_only' else ('horse_subgroups', .001)
        group = groups.get(field, {}).get(name, {})
        ci = group.get('bootstrap_ci', {})
        if (not all(finite_number(ci.get(k)) for k in ('point', 'ci_low', 'ci_high'))
            or ci['ci_low'] > ci['ci_high'] or type(ci.get('n_days')) is not int or ci['n_days'] <= 0
            or group.get('n_days') != ci['n_days'] or group.get('margin') != margin):
            raise ValueError('126 critical subgroup numerical evidence missing/invalid')
        state = three_way(ci['ci_low'], ci['ci_high'], margin, point=ci['point'])
        risk = residual_risk(ci['ci_high'], state)
        if (group.get('decision') != state or groups['subgroup_decisions'].get(name) != state
            or group.get('residual_risk') != risk or groups.get('critical_residual_risk', {}).get(name) != risk):
            raise ValueError('126 critical subgroup decision/residual differs')
        states[name] = state
    critical = cfg['subgroup_guard']['critical_subgroups']
    if (groups.get('subgroup_guard_status') != subgroup_guard_status(states, critical)
        or groups.get('subgroup_guard') is not all(v == 'PASS' for v in states.values())
        or groups.get('target_year') != 2026 or row.get('target_year') != 2026):
        raise ValueError('126 combined subgroup/target year differs')


def validate_result(row, evidence, name, cfg, frozen):
    if name not in frozen['selected_candidates']:
        raise ValueError('Result for unselected126 candidate')
    m.validate_result_population(row, evidence, frozen)
    m.validate_losses(row, evidence)
    if (row.get('candidate_recipe_hash') != frozen['arm_recipe_hashes'][name]
        or row.get('active_recipe_hash') != frozen['arm_recipe_hashes']['baseline']
        or evidence.get('candidate_recipe_hash') != row['candidate_recipe_hash']
        or evidence.get('active_recipe_hash') != row['active_recipe_hash']
        or row.get('candidate_columns') != frozen['arm_columns'][name]
        or row.get('baseline_columns') != frozen['arm_columns']['baseline']
        or row.get('gate_config_hash') != s.p.gate_config_hash(cfg)
        or evidence.get('gate_config_hash') != s.p.gate_config_hash(cfg)
        or row.get('subgroups', {}).get('critical') != cfg['subgroup_guard']['critical_subgroups']):
        raise ValueError('126 result recipe/scope/full quality gate changed')
    noise = row.get('seed_noise', {})
    if (any(noise.get(k) != cfg['seed_noise'][k] for k in ('sd_fold', 'k_seeds', 'source'))
        or noise.get('n_folds') != 7 or type(noise.get('applied')) is not bool
        or evidence.get('seed_noise') != noise):
        raise ValueError('126 seed noise must describe one seed and seven folds')
    old = s.read_json(frozen['sources']['raw125_evidence_path'])['rows']
    if [[r['race_id'], r['race_day'], r['active_winner_nll']] for r in evidence['rows']] != [[r['race_id'], r['race_day'], r['active_winner_nll']] for r in old]:
        raise ValueError('126 baseline losses differ from frozen original116')
    disposition = s.research.assess_research(row)
    if row.get('research_disposition') != disposition:
        raise ValueError('126 research disposition changed')
    invalid = [why for why in disposition['blocking_reasons'] if not why.startswith(
        ('quality_guard_not_passed:', 'critical_subgroup_fail:', 'subgroup_guard_fail'))]
    if invalid or any(type(row['gate'].get(k)) is not bool for k in ('recent_guard', 'top_noninferior', 'calibration')):
        raise ValueError(f'126 incomplete/invalid research quality evidence: {invalid}')
    q = row['gate']['reasons']
    if (row['gate']['top_noninferior'] != (q['top2_diff'] <= .0005 and q['top3_diff'] <= .0005)
        or row['gate']['calibration'] != (q['cand_ece'] - q['act_ece'] <= .001 and q['cand_ece'] < .05)
        or row['gate']['recent_guard'] != q.get('recent', {}).get('pass')):
        raise ValueError('126 quality flags contradict numerical evidence')
    validate_full_readouts(row, evidence, cfg)


def verified_result(name, cfg, frozen):
    path, evidence_path, receipt_path_ = result_path(name), WORK / f'{name}-evidence.json', result_receipt(name)
    exists = [p.exists() for p in (path, evidence_path, receipt_path_)]
    if not any(exists): return False
    if not all(exists): raise ValueError('Incomplete126 result; preserve for diagnosis')
    jobs = [j for j in frozen['jobs'] if j['arm'] == name]
    if len(jobs) != 7 or not all(completed(j) for j in jobs):
        raise ValueError('Completed126 report requires all seven valid cache receipts')
    row, evidence, receipt = s.read_json(path), s.read_json(evidence_path), s.read_json(receipt_path_)
    if (receipt != {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(evidence_path),
        'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'candidate': name}
        or row.get('artifact_kind') != 'market_feature_full_quality_report'
        or row.get('candidate') != name or row.get('can_adopt') is not False or row.get('eligible_for_verdict') is not False
        or row.get('study_config_hash') != s.p.gate_config_hash(cfg)
        or row.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or row.get('evidence_path') != str(evidence_path) or row.get('evidence_sha256') != s.p.digest(evidence_path)):
        raise ValueError('126 completed report provenance changed')
    validate_result(row, evidence, name, cfg, frozen)
    return True


def evaluate_one(name, cfg, frozen):
    jobs = [j for j in frozen['jobs'] if j['arm'] == name]
    if len(jobs) != 7 or not all(completed(j) for j in jobs):
        raise ValueError('126 candidate needs all seven completed folds before scoring')
    if verified_result(name, cfg, frozen): return
    matrix, races, folds = inputs(cfg)
    candidate = ReadOnlyFactory(cfg, frozen, matrix, races, name)
    baseline = BaselineFactory(cfg, frozen, matrix, races)
    report = s.p.paired_eval(candidate, baseline, races, gate_config=cfg, first_valid_year=2020,
        valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1,
        snapshot={'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'source_snapshot_sha256': frozen['sources']['snapshot_sha256'],
                  'candidate': name, 'evidence_regime': 'historical_development'})
    evidence = report.evidence.to_dict()
    row = report.to_dict()
    row.pop('evidence', None); row.pop('diffs_by_day', None)
    row['gate_readout'] = row.pop('decision')
    evidence_path = WORK / f'{name}-evidence.json'
    row.update(artifact_kind='market_feature_full_quality_report', stage='full', candidate=name,
        can_adopt=False, eligible_for_verdict=False, evidence_regime='historical_development',
        study_config_hash=s.p.gate_config_hash(cfg), run_freeze_sha256=s.p.digest(WORK / 'run-freeze.json'),
        candidate_columns=candidate.expected_columns, baseline_columns=baseline.expected_columns,
        evidence_path=str(evidence_path), primary_loss_changed=any(r.diff != 0 for r in report.evidence.rows))
    row['research_disposition'] = s.research.assess_research(row)
    validate_result(row, evidence, name, cfg, frozen)
    verify()
    write_json(evidence_path, evidence)
    row['evidence_sha256'] = s.p.digest(evidence_path)
    write_json(result_path(name), row)
    write_json(result_receipt(name), {'report_sha256': s.p.digest(result_path(name)), 'evidence_sha256': s.p.digest(evidence_path),
        'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'candidate': name})
    print(f'FULL126 {name} diff={report.periods["all"]["diff"]:+.8f} {row["research_disposition"]["state"]}', flush=True)


def train(workers):
    cfg, frozen = verify()
    smoke = s.read_json(SPEC / 'evidence/smoke.json')
    names = ['baseline'] + frozen['selected_candidates'] if frozen['selected_candidates'] else []
    if (smoke.get('structure') != 'PASS' or smoke.get('arm_names') != names
        or smoke.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json') or workers not in (1, 2)):
        raise ValueError('126 registered smoke/max2 workers required')
    lock = WORK / 'running.lock'
    lock.mkdir()
    try:
        for name in frozen['selected_candidates']:
            jobs = [j for j in frozen['jobs'] if j['arm'] == name and not completed(j)]
            run_id = f'{time.time_ns()}-{name}'
            write_json(WORK / 'prefill' / f'{run_id}-run.json', {'candidate': name, 'jobs': jobs, 'workers': workers,
                'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'smoke_sha256': s.p.digest(SPEC / 'evidence/smoke.json')})
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(launch, j, run_id) for j in jobs]
                try:
                    for future in as_completed(futures): future.result()
                except BaseException:
                    for future in futures: future.cancel()
                    raise
            # Valid negative/positive losses and quality BLOCKED never cancel a
            # later registered candidate. Only invalid/incomplete evidence stops.
            evaluate_one(name, cfg, frozen)
            gc.collect()
        verify()
    finally:
        lock.rmdir()


def evaluate():
    cfg, frozen = verify()
    if (WORK / 'running.lock').exists() or not all(completed(j) for j in frozen['jobs']):
        raise ValueError('All126 training must complete before final summary')
    for name in frozen['selected_candidates']:
        evaluate_one(name, cfg, frozen)
        gc.collect()
    summary = {'artifact_kind': 'market_feature_full_quality_summary', 'can_adopt': False, 'eligible_for_verdict': False,
        'research_decision': 'FULL_QUALITY_COMPLETED' if frozen['selected_candidates'] else 'NO_ADVANCING_CANDIDATES',
        'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'selected_candidates': frozen['selected_candidates'],
        'reports': {name: {'report_sha256': s.p.digest(result_path(name)),
            'evidence_sha256': s.read_json(result_path(name))['evidence_sha256'],
            'research_disposition': s.read_json(result_path(name))['research_disposition']} for name in frozen['selected_candidates']},
        'new_outer_jobs': len(frozen['jobs']), 'new_booster_fits': len(frozen['jobs']) * 8,
        'note': 'All2018-screen-selected candidates completed seven folds without performance early stopping. Raw-model seed42 historical research only; no gap/ensemble increment or production adoption.'}
    verify()
    out = SPEC / 'verdict.json'
    if out.exists():
        if s.read_json(out) != summary: raise ValueError('Existing126 summary changed')
    else: write_json(out, summary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['prepare', 'smoke', 'train', 'evaluate'])
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--worker')
    args = ap.parse_args()
    if args.worker and args.mode != 'train': raise ValueError('Worker is train-only')
    if args.mode == 'prepare': prepare()
    elif args.mode == 'smoke': smoke()
    elif args.mode == 'train': worker(args.worker) if args.worker else train(args.workers)
    else: evaluate()


if __name__ == '__main__': main()
