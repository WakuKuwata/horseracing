"""118 anchor138 plus gap: two missing warmup fits, frozen old baselines, six reports."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
import datetime as dt
import gc
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np
import gap_seed_recheck as d
import gap_log_quality as g
import small_gain_stack as s
import pruning_2026_diagnostic as diagnostic
from horseracing_eval.dataset import population_masks
from horseracing_eval.paired import _clip_nll

ROOT = d.ROOT
SPEC = ROOT / 'specs/118-anchor-gap-recheck'
WORK = ROOT / 'artifacts/118-anchor-gap-recheck'
SEEDS = [42, 43, 44]
CONTRASTS = [{'id': 'anchor', 'baseline': 'anchor'}, {'id': 'retained', 'baseline': 'retained'}]
write_json = d.write_json


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg) != expected:
        raise ValueError('118 config hash changed')
    s.p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg, root=ROOT)
    old = d.load_config()
    for key in ('arms', 'eval_window', 'bootstrap', 'seed_noise', 'gamma_fit', 'min_effect_delta',
                'recent_guard', 'top_noninferior', 'calibration', 'subgroup_guard', 'correction', 'smoke'):
        if cfg[key] != old[key]:
            raise ValueError(f'118 fixed recipe/gate changed: {key}')
    if (cfg['seeds'] != SEEDS or cfg['fresh_seeds'] != [43, 44] or cfg['deployment_seed'] != 42
        or cfg['selected_arm'] != 'anchor' or cfg['selected_columns'] != 138
        or cfg['new_fit_jobs'] != 2 or cfg['fresh_fit_years'] != [2019] or cfg['warmup_year'] != 2019
        or cfg['contrasts'] != CONTRASTS or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('118 registered two-job/three-seed/contrast scope changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '116_source': d.source_hash(),
        '117_method': s.p.digest(diagnostic.__file__),
        'summarizer': s.p.digest(ROOT / 'scripts/anchor_gap_summary.py')})


def old_coefficient_path(seed):
    return (g.WORK if seed == 42 else d.seed_area(seed)) / 'coefficients.json'


def old_coefficients(seed):
    if seed == 42:
        return g.verified_coefficients(s.read_json(g.WORK / 'run-freeze.json'))
    return d.verified_coefficients(seed, s.read_json(d.WORK / 'run-freeze.json'))


def source_state():
    cfg116, frozen116 = d.verify()
    frozen117 = diagnostic.verify()
    if (d.WORK / 'running.lock').exists() or not all(d.completed(j) for j in frozen116['jobs']):
        raise ValueError('All116 training must be complete')
    paths = [d.WORK / 'run-freeze.json', d.SPEC / 'verdict.json',
        diagnostic.WORK / 'run-freeze.json', diagnostic.WORK / 'race-diagnostic.json',
        diagnostic.SPEC / 'diagnostic-config.json', diagnostic.SPEC / 'evidence/diagnostic.json',
        diagnostic.SPEC / 'pre-run-review.md', diagnostic.SPEC / 'result-review.md',
        diagnostic.SPEC / 'evidence/independent-review.py', diagnostic.SPEC / 'evidence/independent-review.json',
        diagnostic.SPEC / 'evidence/feature-integrity-review.py', diagnostic.SPEC / 'evidence/feature-integrity-review.json']
    diag = s.read_json(diagnostic.SPEC / 'evidence/diagnostic.json')
    attrs = s.read_json(diagnostic.WORK / 'race-diagnostic.json')
    audit = s.read_json(diagnostic.SPEC / 'evidence/feature-integrity-review.json')
    review = s.read_json(diagnostic.SPEC / 'evidence/independent-review.json')
    if (diag.get('method_sha256') != frozen117['method_sha256']
        or diag.get('run_freeze_sha256') != s.p.digest(diagnostic.WORK / 'run-freeze.json')
        or diag.get('race_rows_sha256') != s.p.digest(diagnostic.WORK / 'race-diagnostic.json')
        or attrs.get('run_freeze_sha256') != diag['run_freeze_sha256']
        or audit.get('state') != 'PASS' or audit.get('verification_before_and_after') is not True
        or audit.get('method_sha256') != s.p.digest(diagnostic.SPEC / 'evidence/feature-integrity-review.py')
        or audit.get('source116_freeze_sha256') != s.p.digest(d.WORK / 'run-freeze.json')):
        raise ValueError('117 diagnostic/audit/row provenance mismatch')
    if (review.get('status') != 'PASS' or review.get('can_adopt') is not False
        or review.get('eligible_for_verdict') is not False
        or review.get('method_sha256') != s.p.digest(diagnostic.SPEC / 'evidence/independent-review.py')
        or review.get('source_method_sha256') != frozen117['method_sha256']
        or review.get('run_freeze_sha256') != s.p.digest(diagnostic.WORK / 'run-freeze.json')
        or review.get('diagnostic_sha256') != s.p.digest(diagnostic.SPEC / 'evidence/diagnostic.json')
        or review.get('race_rows_sha256') != s.p.digest(diagnostic.WORK / 'race-diagnostic.json')):
        raise ValueError('117 independent numerical review provenance mismatch')
    summary = s.read_json(d.SPEC / 'verdict.json')
    if summary.get('research_decision') != 'SEED_MEAN_IMPROVEMENT_RETAINED':
        raise ValueError('118 requires retained116 configuration')
    source_evidence = {}
    for seed in SEEDS:
        old_coefficients(seed)
        paths.extend([old_coefficient_path(seed), old_coefficient_path(seed).with_name('coefficients-receipt.json')])
        for contrast in d.CONTRASTS:
            if not d.verified_result(seed, contrast, cfg116, frozen116):
                raise ValueError('All six old116 reports required')
            path = d.result_path(seed, contrast['id'])
            result = s.read_json(path)
            source_evidence[f"{seed}-{contrast['id']}"] = s.p.digest(result['evidence_path'])
            area = g.WORK if seed == 42 else d.seed_area(seed)
            paths.extend([path, Path(result['evidence_path']), area / f"{contrast['id']}-receipt.json"])
    if review.get('source_evidence_sha256') != source_evidence:
        raise ValueError('117 independent review source116 evidence mismatch')
    for job in frozen116['jobs']:
        paths.extend([d.receipt_path(job['key']), d.WORK / 'cache' / f"{job['key']}.pkl"])
    return {'files': {str(path): s.p.digest(path) for path in paths},
            'population': frozen116['sources']['population'],
            'snapshot_sha256': frozen116['sources']['snapshot_sha256'],
            'source116_freeze_sha256': s.p.digest(d.WORK / 'run-freeze.json'),
            'old_coefficient_sha256': {str(seed): s.p.digest(old_coefficient_path(seed)) for seed in SEEDS}}


def verify():
    cfg = load_config()
    frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != s.runtime() or frozen['sources'] != source_state()):
        raise ValueError('118 frozen source/config/runtime/upstream changed')
    return cfg, frozen


def result_path(seed, name):
    if seed not in SEEDS or name not in {'anchor', 'retained'}:
        raise ValueError('Unknown118 result scope')
    return SPEC / 'evidence' / f'seed-{seed}-{name}.json'


def seed_area(seed):
    if seed not in SEEDS:
        raise ValueError('Unknown118 seed')
    return WORK / f'seed-{seed}'


def seed_config(cfg, seed):
    if seed not in SEEDS:
        raise ValueError('Unknown118 training seed')
    out = deepcopy(cfg)
    out['arms']['seed'] = seed
    return out


def cache_identity(frozen, seed, smoke):
    return [frozen['sources']['snapshot_sha256'], frozen['source_hash'], frozen['config_hash'], seed,
            'smoke' if smoke else 'full']


@contextmanager
def isolated_cache():
    original = s.p.WORK
    s.p.WORK = WORK
    try:
        yield
    finally:
        s.p.WORK = original


class FreshFactory(s.p.CachedFactory):
    def fit(self, train_races, *, num_threads=None):
        with isolated_cache():
            return super().fit(train_races, num_threads=1)


def factory(cfg, frozen, matrix, races, seed, smoke=False):
    if seed not in (43, 44):
        raise ValueError('Only43/44 missing warmup may fit')
    result = FreshFactory(seed_config(cfg, seed), matrix, races, cache_identity(frozen, seed, smoke),
        drops=s.p.OBSERVATION_COLUMNS, smoke=smoke, label=f'{seed}-anchor2019')
    if len(result.expected_columns) != 138:
        raise ValueError('Anchor must preserve all138 columns')
    return result


def job_for(f, fold, seed):
    if seed not in (43, 44) or fold.valid_year != 2019:
        raise ValueError('Only anchor2019 seeds43/44 are new jobs')
    key, th = s.key_for(f, fold)
    return {'key': key, 'train_hash': th, 'year': 2019, 'seed': seed, 'arm': 'anchor'}


def receipt_path(key):
    return WORK / 'prefill' / f'{key}.json'


def completed(job):
    receipt = receipt_path(job['key'])
    if not receipt.exists():
        return False
    value = s.read_json(receipt)
    if (value.get('job') != job or value.get('cache_sha256') != s.p.digest(WORK / 'cache' / f"{job['key']}.pkl")
        or value.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or value.get('imported') is not False or value.get('model_threads') != 1):
        raise ValueError('118 fresh completion receipt changed')
    return True


class AnchorFactory(s.p.CachedFactory):
    def __init__(self, cfg, frozen, matrix, races, seed):
        super().__init__(seed_config(cfg, seed), matrix, races, cache_identity(frozen, seed, False),
                         drops=s.p.OBSERVATION_COLUMNS, label=f'{seed}-anchor')
        self.frozen, self.seed, self.matrix = frozen, seed, matrix

    def fit(self, train_races, *, num_threads=None):
        year = max(r.race_date.year for r in train_races) + 1
        if year not in range(2019, 2027):
            raise ValueError('Anchor replay year outside frozen study')
        if self.seed == 42:
            source = s.ReadOnlyFactory(s.load_config(), s.read_json(s.WORK / 'run-freeze.json'), self.matrix, self.races, 'anchor')
        elif year >= 2020:
            source = d.ReadOnlyFactory(d.load_config(), s.read_json(d.WORK / 'run-freeze.json'), self.matrix, self.races, self.seed, 'anchor')
        else:
            job = next(j for j in self.frozen['jobs'] if j['seed'] == self.seed)
            th = s.train_identity(train_races)
            key = s.p.stable_hash([self.identity, self.recipe_hash, th, year])
            if not completed(job) or job['year'] != year or job['train_hash'] != th or job['key'] != key:
                raise ValueError('118 warmup cache/train/recipe identity mismatch')
            cache = s.reuse.load(WORK / 'cache' / f'{key}.pkl')
            s.reuse.check_payload(cache, key, th, self, [r for r in self.races if r.context.race_date.year == year])
            return s.p.ReplayPredictor(cache['predictions'])
        if source.recipe_meta != self.recipe_meta or source.expected_columns != self.expected_columns:
            raise ValueError('Original anchor recipe/column order differs')
        return source.fit(train_races, num_threads=1)


def retained_factory(matrix, races, seed):
    """Unmodified old pruning base; correction is attached by old_tilt."""
    if seed not in SEEDS:
        raise ValueError('Unknown retained seed')
    base = (s.ReadOnlyFactory(s.load_config(), s.read_json(s.WORK / 'run-freeze.json'), matrix, races, 'pruning')
            if seed == 42 else d.ReadOnlyFactory(d.load_config(), s.read_json(d.WORK / 'run-freeze.json'), matrix, races, seed, 'pruning'))
    return base


def old_tilt(matrix, races, lookup, seed):
    return g.TiltFactory(retained_factory(matrix, races, seed), lookup, old_coefficients(seed))


def certify_baselines(cfg, frozen, matrix, races, folds, lookup):
    records = {}
    for seed in SEEDS:
        anchor = AnchorFactory(cfg, frozen, matrix, races, seed)
        retained = old_tilt(matrix, races, lookup, seed)
        source_report = s.read_json(d.result_path(seed, 'anchor'))
        if retained.recipe_meta != source_report['candidate_recipe_meta'] or retained.recipe_hash != source_report['candidate_recipe_hash']:
            # JSON changes tuples to lists; normalize both only for report serialization,
            # never modify native cache recipe metadata or its hash.
            import json
            if json.loads(json.dumps(retained.recipe_meta)) != source_report['candidate_recipe_meta'] or retained.recipe_hash != source_report['candidate_recipe_hash']:
                raise ValueError('Retained recipe differs from original116 candidate')
        expected = s.read_json(source_report['evidence_path'])['rows']
        actual, scored = [], []
        for year, fold in sorted(folds.items()):
            if year < 2020:
                continue
            train = [r.context for r in fold.train]
            ap, rp = anchor.fit(train, num_threads=1), retained.fit(train, num_threads=1)
            # Independent instance of the exact old entry point exercises all3
            # probabilities; historical horse-level probabilities were not archived.
            reference = old_tilt(matrix, races, lookup, seed).fit(train, num_threads=1)
            for race in fold.valid:
                ctx, pop = race.context, population_masks(race)
                a, r, old_r = ap.predict_race(ctx), rp.predict_race(ctx), reference.predict_race(ctx)
                if r != old_r:
                    raise ValueError('Retained win/top2/top3 replay differs from old entry point')
                scored.append((ctx.race_id, str(ctx.race_date), pop.eligible))
                if pop.eligible:
                    actual.append((ctx.race_id, str(ctx.race_date), _clip_nll(a[pop.winner_horse_id].win), _clip_nll(r[pop.winner_horse_id].win)))
        target = [(r['race_id'], r['race_day'], r['active_winner_nll'], r['candidate_winner_nll']) for r in expected]
        if actual != target:
            raise ValueError('118 baselines do not exactly reproduce old116 per-race losses')
        records[str(seed)] = {'rows': len(actual), 'all_races': len(scored),
            'eligible_order_sha256': s.p.stable_hash([(r[0], r[1]) for r in actual]),
            'scored_population_sha256': s.p.stable_hash(scored),
            'source_report_sha256': s.p.digest(d.result_path(seed, 'anchor')),
            'source_evidence_sha256': s.p.digest(source_report['evidence_path']),
            'retained_coefficient_sha256': s.p.digest(old_coefficient_path(seed)),
            'anchor_and_retained_losses_exact': True, 'retained_win_top2_top3_replay_exact': True,
            'probability_reference': 'Frozen old cache/coefficient/entry-point replay; not an archived horse-level probability file'}
    if len({r['eligible_order_sha256'] for r in records.values()}) != 1 or len({r['scored_population_sha256'] for r in records.values()}) != 1:
        raise ValueError('Baseline seed populations differ')
    return records


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify()
        return
    cfg = load_config()
    before, state = source_hash(), source_state()
    matrix, races, folds, lookup, audit = g.load_inputs(s.load_config())
    if audit != state['population']:
        raise ValueError('118 input population changed')
    frozen = {'source_hash': before, 'config_hash': s.p.gate_config_hash(cfg), 'runtime': s.runtime(), 'sources': state}
    jobs = [job_for(factory(cfg, frozen, matrix, races, seed), folds[2019], seed) for seed in (43, 44)]
    if len({j['key'] for j in jobs}) != 2:
        raise ValueError('Distinct seed warmup keys required')
    frozen['jobs'] = jobs
    parity = certify_baselines(cfg, frozen, matrix, races, folds, lookup)
    del matrix, races, folds, lookup
    gc.collect()
    if source_hash() != before or source_state() != state:
        raise ValueError('Sources changed during118 preparation')
    frozen.update(artifact_kind='anchor_gap_recheck_freeze', can_adopt=False, eligible_for_verdict=False,
        baseline_parity=parity, prepared_at=dt.datetime.now(dt.timezone.utc).isoformat())
    write_json(WORK / 'run-freeze.json', frozen)
    print('PREPARE PASS old baseline parity;2 warmup jobs frozen', flush=True)


def worker(key):
    cfg, frozen = verify()
    job = next(j for j in frozen['jobs'] if j['key'] == key)
    if completed(job):
        return
    path = WORK / 'cache' / f'{key}.pkl'
    if path.exists():
        raise ValueError('Unreceipted118 cache; preserve for diagnosis')
    matrix, races, folds = s.inputs(s.load_config())
    f = factory(cfg, frozen, matrix, races, job['seed'])
    fold = folds[2019]
    if job_for(f, fold, job['seed']) != job:
        raise ValueError('118 job input identity differs')
    try:
        f.fit([r.context for r in fold.train], num_threads=1)
        cache = s.reuse.load(path)
        s.reuse.check_payload(cache, key, job['train_hash'], f, list(fold.valid))
        verify()
        write_json(receipt_path(key), {'job': job, 'cache_sha256': s.p.digest(path),
            'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'imported': False, 'model_threads': 1,
            'current_fit_seconds': cache['elapsed_seconds'],
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)})
    except BaseException:
        if path.exists() and not receipt_path(key).exists():
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            target = WORK / 'prefill' / f'unverified-{key}-{stamp}.pkl'
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
        raise


def launch(job, run_id):
    log = WORK / 'prefill' / f"{run_id}-{job['key']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('x') as output:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), 'train', '--worker', job['key']], cwd=ROOT,
                       stdout=output, stderr=subprocess.STDOUT, check=True)
    if not completed(job):
        raise ValueError('118 worker exited without valid receipt')
    print(f"DONE seed={job['seed']} anchor2019", flush=True)


def train(workers):
    cfg, frozen = verify()
    smoke_result = s.read_json(SPEC / 'evidence/smoke.json')
    if (smoke_result.get('structure') != 'PASS' or smoke_result.get('seeds') != [43, 44]
        or smoke_result.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')):
        raise ValueError('118 two-seed structural smoke required')
    if workers not in (1, 2):
        raise ValueError('At most2 isolated workers')
    jobs = [j for j in frozen['jobs'] if not completed(j)]
    lock = WORK / 'running.lock'
    lock.mkdir()
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        write_json(WORK / 'prefill' / f'{run_id}-run.json', {'jobs': jobs, 'workers': workers,
            'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'smoke_sha256': s.p.digest(SPEC / 'evidence/smoke.json')})
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(launch, j, run_id) for j in jobs]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        verify()
    finally:
        lock.rmdir()


def smoke():
    cfg, frozen = verify()
    path = SPEC / 'evidence/smoke.json'
    if path.exists():
        raise FileExistsError('Preserve previous118 smoke')
    matrix, races, folds = s.inputs(s.load_config(), smoke=True)
    frame = matrix.frame[['race_id', 'horse_id', 'race_date', 'days_since_last', 'sex']]
    vals = g.screen.candidate_matrix(frame)[:, 0]
    lookup = {(rid, hid): (day, float(v)) for rid, hid, day, v in zip(frame.race_id, frame.horse_id, frame.race_date, vals, strict=True)}
    for seed in (43, 44):
        f = factory(cfg, frozen, matrix, races, seed, smoke=True)
        for fold in folds.values():
            predictor = f.fit([r.context for r in fold.train], num_threads=1)
            for race in fold.valid:
                base = predictor.predict_race(race.context)
                corrected, _ = g.tilt_predictions(base, race.context, lookup, 0.)
                if not np.allclose([v.win for v in base.values()], [corrected[k].win for k in base], rtol=0, atol=1e-12):
                    raise ValueError('Gamma-zero smoke changed win probabilities')
    verify()
    write_json(path, {'artifact_kind': 'anchor_gap_smoke', 'structure': 'PASS', 'can_adopt': False,
        'eligible_for_verdict': False, 'seeds': [43, 44], 'arm': 'anchor138', 'n_estimators': 5,
        'n_oof_blocks': 2, 'gamma': 0., 'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json')})


def verified_coefficients(seed, frozen):
    area = seed_area(seed)
    path = area / 'coefficients.json'
    receipt = s.read_json(area / 'coefficients-receipt.json')
    if receipt != {'sha256': s.p.digest(path), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed}:
        raise ValueError('118 anchor coefficient receipt mismatch')
    value = s.read_json(path)
    pop = frozen['sources']['population']
    recipe = s.p.CalibSplitFactory(None, s.p.make_recipe(seed_config(load_config(), seed), s.p.OBSERVATION_COLUMNS),
                                  n_oof_blocks=8, method='isotonic', require_sufficient=True)
    if (value.get('artifact_kind') != 'gap_log_coefficients'
        or value.get('training_seed') != seed or value.get('base_arm') != 'anchor138'
        or value.get('base_recipe_hash') != recipe.recipe_hash
        or value.get('can_adopt') is not False or value.get('eligible_for_verdict') is not False
        or set(value.get('gammas', {})) != {str(y) for y in range(2020, 2027)}
        or not all(np.isfinite(x) for x in value['gammas'].values())
        or value.get('warmup_eligible_races') != pop['2019']['eligible_races']
        or value.get('evaluated_eligible_races') != sum(pop[str(y)]['eligible_races'] for y in range(2020, 2027))):
        raise ValueError('118 anchor coefficient scope differs')
    return value


def coefficient_provenance(seed, contrast):
    candidate = s.read_json(seed_area(seed) / 'coefficients.json')
    retained_report = s.read_json(d.result_path(seed, 'anchor'))
    return {'candidate': {'base_arm': 'anchor138', 'path': str(seed_area(seed) / 'coefficients.json'),
                         'base_recipe_hash': candidate['base_recipe_hash'],
                         'sha256': s.p.digest(seed_area(seed) / 'coefficients.json')},
            'baseline': None if contrast['id'] == 'anchor' else
                {'base_arm': 'pruning125', 'path': str(old_coefficient_path(seed)),
                 'base_recipe_hash': s.p.stable_hash(s.p.ModelRecipe.strip_new_field_defaults(retained_report['candidate_recipe_meta']['base_recipe'])),
                 'sha256': s.p.digest(old_coefficient_path(seed))}}


def validate_rows(rows, seed, contrast):
    source = s.read_json(d.result_path(seed, 'anchor'))
    old_rows = s.read_json(source['evidence_path'])['rows']
    field = 'active_winner_nll' if contrast['id'] == 'anchor' else 'candidate_winner_nll'
    if (len(rows) != len(old_rows)
        or any((r['race_id'], r['race_day'], r['active_winner_nll']) !=
               (o['race_id'], o['race_day'], o[field]) for r, o in zip(rows, old_rows, strict=True))):
        raise ValueError('118 baseline per-race NLL differs from original116 evidence')
    other = result_path(seed, 'retained' if contrast['id'] == 'anchor' else 'anchor')
    if other.exists():
        other_rows = s.read_json(s.read_json(other)['evidence_path'])['rows']
        if (len(rows) != len(other_rows)
            or any((r['race_id'], r['race_day'], r['candidate_winner_nll']) !=
                   (o['race_id'], o['race_day'], o['candidate_winner_nll']) for r, o in zip(rows, other_rows, strict=True))):
            raise ValueError('118 candidate losses differ across paired contrasts')


def verified_result(seed, contrast, cfg, frozen):
    if isinstance(contrast, str):
        contrast = next(c for c in CONTRASTS if c['id'] == contrast)
    path = result_path(seed, contrast['id'])
    if not path.exists():
        return False
    value = s.read_json(path)
    area = seed_area(seed)
    evidence = area / f"{contrast['id']}-evidence.json"
    receipt = s.read_json(area / f"{contrast['id']}-receipt.json")
    if (receipt != {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(evidence),
                    'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed}
        or value.get('artifact_kind') != 'anchor_gap_research_report'
        or value.get('can_adopt') is not False or value.get('eligible_for_verdict') is not False
        or value.get('training_seed') != seed or value.get('contrast') != contrast
        or value.get('study_config_hash') != s.p.gate_config_hash(cfg)
        or value.get('seed_config_hash') != s.p.gate_config_hash(seed_config(cfg, seed))
        or value.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or value.get('coefficient_provenance') != coefficient_provenance(seed, contrast)
        or value.get('evidence_path') != str(evidence) or value.get('evidence_sha256') != s.p.digest(evidence)
        or value.get('research_disposition') != s.research.assess_research(value)):
        raise ValueError('118 existing report/receipt/both coefficient provenance changed')
    verified_coefficients(seed, frozen)
    old_coefficients(seed)
    validate_rows(s.read_json(evidence)['rows'], seed, contrast)
    return True


def evaluate():
    cfg, frozen = verify()
    if (WORK / 'running.lock').exists() or not all(completed(j) for j in frozen['jobs']):
        raise ValueError('Both118 warmup jobs must have valid stopped-worker receipts')
    matrix, races, folds, lookup, audit = g.load_inputs(s.load_config())
    if audit != frozen['sources']['population']:
        raise ValueError('118 evaluation population changed')
    for seed in SEEDS:
        area = seed_area(seed)
        path = area / 'coefficients.json'
        if path.exists():
            coefficients = verified_coefficients(seed, frozen)
        else:
            if (area / 'coefficients-receipt.json').exists():
                raise ValueError('Orphan118 coefficient receipt')
            anchor = AnchorFactory(cfg, frozen, matrix, races, seed)
            coefficients = g.fit_coefficients(anchor, folds, lookup)
            coefficients.update(training_seed=seed, base_arm='anchor138', base_recipe_hash=anchor.recipe_hash)
            verify()
            write_json(path, coefficients)
            write_json(area / 'coefficients-receipt.json', {'sha256': s.p.digest(path),
                'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed})
            del anchor
        for contrast in CONTRASTS:
            if verified_result(seed, contrast, cfg, frozen):
                continue
            evidence = area / f"{contrast['id']}-evidence.json"
            if evidence.exists():
                raise ValueError('Orphan118 evidence; preserve for diagnosis')
            candidate = g.TiltFactory(AnchorFactory(cfg, frozen, matrix, races, seed), lookup, coefficients)
            baseline = (AnchorFactory(cfg, frozen, matrix, races, seed) if contrast['id'] == 'anchor'
                        else old_tilt(matrix, races, lookup, seed))
            t0 = time.monotonic()
            report = s.p.paired_eval(candidate, baseline, races, gate_config=seed_config(cfg, seed),
                first_valid_year=2020, valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1,
                snapshot={'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed,
                          'contrast': contrast, 'coefficient_provenance': coefficient_provenance(seed, contrast),
                          'evidence_regime': 'historical_development_full_information'})
            if report.n_eligible != 22990 or report.n_races != 23030:
                raise ValueError('118 scored population count changed')
            validate_rows(report.evidence.to_dict()['rows'], seed, contrast)
            result = report.to_dict()
            result.pop('evidence', None)
            result.pop('diffs_by_day', None)
            result['gate_readout'] = result.pop('decision')
            result.update(artifact_kind='anchor_gap_research_report', can_adopt=False, eligible_for_verdict=False,
                training_seed=seed, deployment_seed=42, contrast=contrast, selected_arm='anchor138',
                study_config_hash=s.p.gate_config_hash(cfg), seed_config_hash=s.p.gate_config_hash(seed_config(cfg, seed)),
                run_freeze_sha256=s.p.digest(WORK / 'run-freeze.json'),
                coefficient_provenance=coefficient_provenance(seed, contrast),
                candidate_columns=candidate.expected_columns, baseline_columns=baseline.expected_columns,
                evidence_regime='historical_development_full_information', limitations=cfg['limitations'],
                assembly_audit={'races': len(candidate.audit), 'assembly_eps': 0.,
                    'below_legacy_clip_horses': sum(a['below_legacy_clip_horses'] for a in candidate.audit.values()),
                    'max_post_assembly_win_change': max(a['max_post_assembly_win_change'] for a in candidate.audit.values())},
                elapsed_seconds=time.monotonic() - t0)
            result['research_disposition'] = s.research.assess_research(result)
            verify()
            write_json(evidence, report.evidence.to_dict())
            result.update(evidence_path=str(evidence), evidence_sha256=s.p.digest(evidence))
            out = result_path(seed, contrast['id'])
            write_json(out, result)
            write_json(area / f"{contrast['id']}-receipt.json", {'report_sha256': s.p.digest(out),
                'evidence_sha256': s.p.digest(evidence), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed})
            print(f"RESULT seed={seed} {contrast['id']} {result['periods']['all']['diff']:+.8f} {result['research_disposition']['state']}", flush=True)
            del candidate, baseline, report
            gc.collect()
    print('EVALUATE COMPLETE six118 research reports; no production change', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'smoke', 'train', 'evaluate'])
    parser.add_argument('--workers', type=int, choices=[1, 2], default=2)
    parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        if args.action != 'train':
            parser.error('--worker only with train')
        worker(args.worker)
    elif args.action == 'train':
        train(args.workers)
    else:
        {'prepare': prepare, 'smoke': smoke, 'evaluate': evaluate}[args.action]()


if __name__ == '__main__':
    main()
