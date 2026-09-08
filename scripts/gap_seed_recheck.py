"""116 fixed gap-log bundle across paired training seeds; historical research only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import datetime as dt
import gc
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np
import gap_log_quality as g
import small_gain_stack as s
from horseracing_eval.dataset import population_masks
from horseracing_eval.evidence import build_rows, race_covariates
from horseracing_eval.paired import _clip_nll

ROOT = s.ROOT
SPEC = ROOT / 'specs/116-gap-seed-recheck'
WORK = ROOT / 'artifacts/116-gap-seed-recheck'
CONTRASTS = g.CONTRASTS
SEEDS = [42, 43, 44]
FRESH_SEEDS = [43, 44]
write_json = g.screen.write_json


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg) != expected:
        raise ValueError('116 config hash mismatch')
    s.p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg, root=ROOT)
    previous = g.load_config()
    for key in ('arms', 'eval_window', 'bootstrap', 'seed_noise', 'gamma_fit', 'contrasts',
                'min_effect_delta', 'recent_guard', 'top_noninferior', 'calibration', 'subgroup_guard', 'correction'):
        if cfg[key] != previous[key]:
            raise ValueError(f'Fixed 115 recipe/gate changed: {key}')
    if (cfg['seeds'] != SEEDS or cfg['fresh_seeds'] != FRESH_SEEDS or cfg['deployment_seed'] != 42
        or cfg['selected_arm'] != 'pruning' or cfg['selected_columns'] != 125
        or cfg['warmup_year'] != 2019 or cfg['new_fit_jobs'] != 30
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False
        or cfg['smoke'] != {'eval_window': {'from': '2008-01-01', 'to': '2008-01-31', 'min_eval_days': 1},
                           'n_estimators': 5, 'n_oof_blocks': 2, 'gamma': 0.0}):
        raise ValueError('116 registered seed/feature/smoke scope changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '115_source': g.source_hash(),
        'aggregate_method': s.p.digest(ROOT / 'scripts/gap_seed_summary.py')})


def result_path(seed, contrast_id):
    if seed not in SEEDS or contrast_id not in {'increment', 'anchor'}:
        raise ValueError('Unknown registered result scope')
    return (g.SPEC / 'evidence' / f'full-{contrast_id}.json' if seed == 42 else
            SPEC / 'evidence' / f'seed-{seed}-{contrast_id}.json')


def seed_area(seed):
    if seed not in FRESH_SEEDS:
        raise ValueError('Seed42 evidence is read-only upstream')
    return WORK / f'seed-{seed}'


def seed_config(cfg, seed):
    if seed not in SEEDS:
        raise ValueError('Unregistered training seed')
    out = deepcopy(cfg)
    out['arms']['seed'] = seed
    return out


def sources():
    cfg115, frozen115, cfg113, frozen113 = g.verify()
    reports = {}
    files = [g.WORK / 'run-freeze.json', g.SPEC / 'gate-config.json', g.SPEC / 'gate-config.hash.txt',
             g.SPEC / 'verdict.json', g.WORK / 'coefficients.json', g.WORK / 'coefficients-receipt.json']
    for contrast in CONTRASTS:
        path = result_path(42, contrast['id'])
        if not g.verified_output(path, contrast, frozen115):
            raise ValueError('Both original115 results are required')
        reports[contrast['id']] = s.read_json(path)
        files.extend([path, g.WORK / f"{contrast['id']}-evidence.json", g.WORK / f"{contrast['id']}-receipt.json"])
    expected = {'artifact_kind': 'gap_log_quality_research_summary', 'can_adopt': False,
        'eligible_for_verdict': False, 'selected_arm': 'pruning',
        'research_decision': 'CORRECTION_RETAINED',
        'report_sha256': {k: s.p.digest(result_path(42, k)) for k in reports},
        'run_freeze_sha256': s.p.digest(g.WORK / 'run-freeze.json')}
    if (s.read_json(g.SPEC / 'verdict.json') != expected
        or frozen115['sources']['selected_arm'] != 'pruning'
        or g.research_progression(reports) != 'CORRECTION_RETAINED'):
        raise ValueError('116 requires the frozen115 retained pruning+gap branch')
    g.verified_coefficients(frozen115)
    state = {'files': {str(path): s.p.digest(path) for path in files},
             'snapshot_sha256': frozen113['snapshot_sha256'], 'population': frozen115['population'],
             'source115_config_hash': s.p.gate_config_hash(cfg115),
             'source113_freeze_sha256': s.p.digest(s.WORK / 'run-freeze.json')}
    return state, cfg113, frozen113, frozen115


def verify():
    cfg = load_config()
    frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != s.runtime() or frozen['sources'] != sources()[0]):
        raise ValueError('116 frozen source/config/runtime/upstream changed')
    return cfg, frozen


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


def factory(cfg, frozen, matrix, races, seed, name, smoke=False):
    if name not in {'pruning', 'anchor'} or seed not in FRESH_SEEDS:
        raise ValueError('Only registered fresh arms may fit')
    drops = s.arm(s.load_config(), name)['drop_features']
    result = FreshFactory(seed_config(cfg, seed), matrix, races, cache_identity(frozen, seed, smoke),
                          drops=drops, smoke=smoke, label=f'{seed}-{name}')
    if len(result.expected_columns) != (125 if name == 'pruning' else 138):
        raise ValueError('Fresh effective feature scope differs')
    return result


def inputs(smoke=False):
    return g.load_inputs(s.load_config()) if not smoke else (*s.inputs(s.load_config(), smoke=True),)


def job_for(f, fold, seed, name):
    key, th = s.key_for(f, fold)
    return {'key': key, 'train_hash': th, 'year': fold.valid_year, 'seed': seed, 'arm': name}


def training_jobs(cfg, frozen, matrix, races, folds):
    if sorted(folds) != list(range(2019, 2027)):
        raise ValueError('Eight source folds including2019 warmup required')
    jobs = []
    for seed in FRESH_SEEDS:
        for name in ('pruning', 'anchor'):
            f = factory(cfg, frozen, matrix, races, seed, name)
            for year, fold in sorted(folds.items()):
                if name == 'anchor' and year == 2019:
                    continue
                jobs.append(job_for(f, fold, seed, name))
    if len(jobs) != 30 or len({j['key'] for j in jobs}) != 30:
        raise ValueError('Expected30 distinct fresh training jobs')
    return sorted(jobs, key=lambda j: (-j['year'], j['seed'], j['arm']))


def certify_seed42(cfg113, frozen113, frozen115, matrix, races, folds, lookup):
    coefficients = g.verified_coefficients(frozen115)
    selected = s.ReadOnlyFactory(cfg113, frozen113, matrix, races, 'pruning')
    anchor = s.ReadOnlyFactory(cfg113, frozen113, matrix, races, 'anchor')
    entries = {c['id']: [] for c in CONTRASTS}
    covariates = {}
    scored = []
    for year, fold in sorted(folds.items()):
        baseline = selected.fit([r.context for r in fold.train], num_threads=1)
        if year == 2019:
            # Warmup probabilities are verified by the original cache payload;
            # they are not scored or included in the 2020+ primary population.
            continue
        original = anchor.fit([r.context for r in fold.train], num_threads=1)
        for race in fold.valid:
            ctx = race.context
            corrected, _ = g.tilt_predictions(baseline.predict_race(ctx), ctx, lookup, coefficients['gammas'][str(year)])
            pred_base, pred_anchor = baseline.predict_race(ctx), original.predict_race(ctx)
            pop = population_masks(race)
            scored.append((ctx.race_id, str(ctx.race_date), pop.eligible))
            if not pop.eligible:
                continue
            c = _clip_nll(corrected[pop.winner_horse_id].win)
            for name, prediction in [('increment', pred_base), ('anchor', pred_anchor)]:
                entries[name].append((ctx.race_id, str(ctx.race_date), c, _clip_nll(prediction[pop.winner_horse_id].win)))
            covariates[ctx.race_id] = race_covariates(ctx.race_id, field_size=len(ctx.started_horses), race_day=str(ctx.race_date))
    for name, rows in entries.items():
        actual = [asdict(r) for r in build_rows(rows, covariates=covariates)]
        expected = s.read_json(g.WORK / f'{name}-evidence.json')['rows']
        if actual != expected:
            raise ValueError(f'Seed42 exact per-race115 evidence parity failed: {name}')
    return {'all_eligible_rows_exact': True, 'n_eligible': len(entries['increment']),
            'n_races': len(scored), 'n_days': len({r[1] for r in scored}),
            'scored_population_sha256': s.p.stable_hash(scored),
            'eligible_order_sha256': s.p.stable_hash([(r[0], r[1]) for r in entries['increment']]),
            'coefficient_sha256': s.p.digest(g.WORK / 'coefficients.json'),
            'note': 'Original115 per-race primary evidence reproduced exactly; no bootstrap rerun or reinterpretation as 3-seed evidence.'}


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify()
        print('PREPARE existing freeze verified', flush=True)
        return
    cfg = load_config()
    before = source_hash()
    state, cfg113, frozen113, frozen115 = sources()
    matrix, races, folds, lookup, audit = g.load_inputs(cfg113)
    if audit != state['population']:
        raise ValueError('116 input population differs from115')
    frozen = {'source_hash': before, 'config_hash': s.p.gate_config_hash(cfg), 'runtime': s.runtime(), 'sources': state}
    parity = certify_seed42(cfg113, frozen113, frozen115, matrix, races, folds, lookup)
    jobs = training_jobs(cfg, frozen, matrix, races, folds)
    del matrix, races, folds, lookup
    gc.collect()
    if source_hash() != before or sources()[0] != state:
        raise ValueError('Sources changed during preparation')
    frozen.update(artifact_kind='gap_seed_recheck_freeze', can_adopt=False, eligible_for_verdict=False,
        jobs=jobs, seed42_parity=parity,
        prepared_at=dt.datetime.now(dt.timezone.utc).isoformat())
    write_json(WORK / 'run-freeze.json', frozen)
    print('PREPARE PASS seed42 exact parity;30 fresh jobs frozen', flush=True)


def receipt_path(key):
    return WORK / 'prefill' / f'{key}.json'


def completed(job):
    path = receipt_path(job['key'])
    if not path.exists():
        return False
    receipt = s.read_json(path)
    if (receipt.get('job') != job or receipt.get('cache_sha256') != s.p.digest(WORK / 'cache' / f"{job['key']}.pkl")
        or receipt.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or receipt.get('imported') is not False or receipt.get('model_threads') != 1):
        raise ValueError('Fresh116 cache completion receipt differs')
    return True


def worker(key):
    cfg, frozen = verify()
    job = next(j for j in frozen['jobs'] if j['key'] == key)
    if completed(job):
        return
    path = WORK / 'cache' / f'{key}.pkl'
    if path.exists():
        raise ValueError('Unreceipted cache exists; preserve for independent diagnosis')
    # Worker fitting needs one matrix, not the million-entry correction lookup.
    matrix, races, folds = s.inputs(s.load_config())
    f = factory(cfg, frozen, matrix, races, job['seed'], job['arm'])
    fold = folds[job['year']]
    if job_for(f, fold, job['seed'], job['arm']) != job:
        raise ValueError('116 worker job inputs changed')
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
    print(f"START seed={job['seed']} {job['arm']} {job['year']}", flush=True)
    with log.open('x') as output:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), 'train', '--worker', job['key']],
                       cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)
    if not completed(job):
        raise ValueError('116 worker exited without valid receipt')
    print(f"DONE seed={job['seed']} {job['arm']} {job['year']}", flush=True)


def train(workers):
    cfg, frozen = verify()
    smoke = s.read_json(SPEC / 'evidence/smoke.json')
    if (smoke.get('structure') != 'PASS' or smoke.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or smoke.get('configurations') != [{'seed': seed, 'arm': name} for seed in FRESH_SEEDS for name in ('anchor', 'pruning')]):
        raise ValueError('Frozen four-configuration smoke required')
    if workers not in (1, 2):
        raise ValueError('At most two isolated single-thread workers')
    jobs = [j for j in frozen['jobs'] if not completed(j)]
    lock = WORK / 'running.lock'
    lock.mkdir()
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        write_json(WORK / 'prefill' / f'{run_id}-run.json', {'jobs': jobs, 'workers': workers,
            'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'smoke_sha256': s.p.digest(SPEC / 'evidence/smoke.json')})
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(launch, job, run_id) for job in jobs]
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
    print(f'TRAIN COMPLETE new jobs={len(jobs)}', flush=True)


class ReadOnlyFactory(FreshFactory):
    def __init__(self, cfg, frozen, matrix, races, seed, name):
        f = factory(cfg, frozen, matrix, races, seed, name)
        self.__dict__.update(f.__dict__)
        self.frozen, self.seed, self.name = frozen, seed, name

    def fit(self, train_races, *, num_threads=None):
        year = max(r.race_date.year for r in train_races) + 1
        th = s.train_identity(train_races)
        job = next(j for j in self.frozen['jobs'] if j['year'] == year and j['seed'] == self.seed and j['arm'] == self.name)
        key = s.p.stable_hash([self.identity, self.recipe_hash, th, year])
        if not completed(job) or job['train_hash'] != th or job['key'] != key:
            raise ValueError('116 replay cache/recipe/train identity mismatch')
        cache = s.reuse.load(WORK / 'cache' / f'{key}.pkl')
        s.reuse.check_payload(cache, key, th, self, [r for r in self.races if r.context.race_date.year == year])
        print(f'REPLAY seed={self.seed} {self.name} year={year}', flush=True)
        return s.p.ReplayPredictor(cache['predictions'])


def smoke():
    cfg, frozen = verify()
    path = SPEC / 'evidence/smoke.json'
    if path.exists():
        raise FileExistsError('Preserve previous smoke')
    matrix, races, folds = inputs(smoke=True)
    # The same transformation is used for gamma-zero wiring, without fitting a coefficient.
    frame = matrix.frame[['race_id', 'horse_id', 'race_date', 'days_since_last', 'sex']]
    vals = g.screen.candidate_matrix(frame)[:, 0]
    lookup = {(rid, hid): (date, float(v)) for rid, hid, date, v in zip(frame.race_id, frame.horse_id, frame.race_date, vals, strict=True)}
    records = []
    for seed in FRESH_SEEDS:
        for name in ('anchor', 'pruning'):
            f = factory(cfg, frozen, matrix, races, seed, name, smoke=True)
            for fold in folds.values():
                pred = f.fit([r.context for r in fold.train], num_threads=1)
                for race in fold.valid:
                    base = pred.predict_race(race.context)
                    tilted, _ = g.tilt_predictions(base, race.context, lookup, 0.0)
                    if not np.allclose([base[h.horse_id].win for h in race.context.started_horses],
                                       [tilted[h.horse_id].win for h in race.context.started_horses], atol=1e-12, rtol=0):
                        raise ValueError('Gamma-zero smoke changed win probabilities')
            records.append({'seed': seed, 'arm': name})
    verify()
    write_json(path, {'artifact_kind': 'gap_seed_recheck_smoke', 'structure': 'PASS', 'can_adopt': False,
        'eligible_for_verdict': False, 'configurations': records, 'n_estimators': 5, 'n_oof_blocks': 2,
        'gamma': 0.0, 'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json')})
    print('SMOKE PASS four seed/arm configurations', flush=True)


def verified_coefficients(seed, frozen):
    area = seed_area(seed)
    path = area / 'coefficients.json'
    receipt = s.read_json(area / 'coefficients-receipt.json')
    if receipt != {'sha256': s.p.digest(path), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed}:
        raise ValueError('116 coefficient receipt mismatch')
    result = s.read_json(path)
    pop = frozen['sources']['population']
    if (result.get('artifact_kind') != 'gap_log_coefficients' or result.get('training_seed') != seed
        or result.get('can_adopt') is not False or result.get('eligible_for_verdict') is not False
        or set(result.get('gammas', {})) != {str(y) for y in range(2020, 2027)}
        or not all(np.isfinite(v) for v in result['gammas'].values())
        or result.get('warmup_eligible_races') != pop['2019']['eligible_races']
        or result.get('evaluated_eligible_races') != sum(pop[str(y)]['eligible_races'] for y in range(2020, 2027))):
        raise ValueError('116 coefficient seed/population scope mismatch')
    return result


def verified_result(seed, contrast, cfg, frozen):
    if isinstance(contrast, str):
        contrast = next(c for c in CONTRASTS if c['id'] == contrast)
    path = result_path(seed, contrast['id'])
    if not path.exists():
        return False
    if seed == 42:
        _, _, _, frozen115 = sources()
        return g.verified_output(path, contrast, frozen115)
    area = seed_area(seed)
    result = s.read_json(path)
    evidence = area / f"{contrast['id']}-evidence.json"
    receipt = s.read_json(area / f"{contrast['id']}-receipt.json")
    if (receipt != {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(evidence),
                    'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed}
        or result.get('artifact_kind') != 'gap_seed_recheck_research_report'
        or result.get('can_adopt') is not False or result.get('eligible_for_verdict') is not False
        or result.get('training_seed') != seed or result.get('contrast') != contrast
        or result.get('study_config_hash') != s.p.gate_config_hash(cfg)
        or result.get('seed_config_hash') != s.p.gate_config_hash(seed_config(cfg, seed))
        or result.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or result.get('evidence_path') != str(evidence) or result.get('evidence_sha256') != s.p.digest(evidence)
        or result.get('coefficient_sha256') != s.p.digest(area / 'coefficients.json')
        or result.get('research_disposition') != s.research.assess_research(result)):
        raise ValueError('116 stored report/receipt differs')
    verified_coefficients(seed, frozen)
    return True


def evaluate():
    cfg, frozen = verify()
    if (WORK / 'running.lock').exists() or not all(completed(j) for j in frozen['jobs']):
        raise ValueError('All30 valid receipts and stopped workers required')
    matrix, races, folds, lookup, audit = inputs()
    if audit != frozen['sources']['population']:
        raise ValueError('116 evaluation population differs')
    scored = [(r.context.race_id, str(r.context.race_date), population_masks(r).eligible)
              for year, fold in sorted(folds.items()) if year >= 2020 for r in fold.valid]
    if s.p.stable_hash(scored) != frozen['seed42_parity']['scored_population_sha256']:
        raise ValueError('116 full scored population differs from seed42')
    for seed in FRESH_SEEDS:
        area = seed_area(seed)
        coefficient_path = area / 'coefficients.json'
        if coefficient_path.exists():
            coefficients = verified_coefficients(seed, frozen)
        else:
            if (area / 'coefficients-receipt.json').exists():
                raise ValueError('Orphan coefficient receipt')
            f = ReadOnlyFactory(cfg, frozen, matrix, races, seed, 'pruning')
            coefficients = g.fit_coefficients(f, folds, lookup)
            coefficients['training_seed'] = seed
            verify()
            write_json(coefficient_path, coefficients)
            write_json(area / 'coefficients-receipt.json', {'sha256': s.p.digest(coefficient_path),
                'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed})
            del f
        for contrast in CONTRASTS:
            if verified_result(seed, contrast, cfg, frozen):
                continue
            evidence = area / f"{contrast['id']}-evidence.json"
            if evidence.exists():
                raise ValueError('Orphan evidence; preserve for independent diagnosis')
            selected = ReadOnlyFactory(cfg, frozen, matrix, races, seed, 'pruning')
            candidate = g.TiltFactory(selected, lookup, coefficients)
            baseline = ReadOnlyFactory(cfg, frozen, matrix, races, seed,
                                       'pruning' if contrast['baseline'] == 'selected' else 'anchor')
            t0 = time.monotonic()
            report = s.p.paired_eval(candidate, baseline, races, gate_config=seed_config(cfg, seed),
                first_valid_year=2020, valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1,
                snapshot={'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'training_seed': seed,
                          'contrast': contrast, 'coefficient_sha256': s.p.digest(coefficient_path),
                          'evidence_regime': 'historical_development_full_information'})
            if (report.n_eligible != coefficients['evaluated_eligible_races']
                or report.n_races != frozen['seed42_parity']['n_races']):
                raise ValueError('116 coefficient/scored eligibility mismatch')
            rows = report.evidence.to_dict()['rows']
            if s.p.stable_hash([(r['race_id'], r['race_day']) for r in rows]) != frozen['seed42_parity']['eligible_order_sha256']:
                raise ValueError('116 seed42/43/44 eligible race order differs')
            result = report.to_dict()
            result.pop('evidence', None)
            result.pop('diffs_by_day', None)
            result['gate_readout'] = result.pop('decision')
            result.update(artifact_kind='gap_seed_recheck_research_report', can_adopt=False, eligible_for_verdict=False,
                training_seed=seed, deployment_seed=42, contrast=contrast, selected_arm='pruning',
                study_config_hash=s.p.gate_config_hash(cfg), seed_config_hash=s.p.gate_config_hash(seed_config(cfg, seed)),
                run_freeze_sha256=s.p.digest(WORK / 'run-freeze.json'), coefficient_sha256=s.p.digest(coefficient_path),
                candidate_columns=candidate.expected_columns, baseline_columns=baseline.expected_columns,
                scored_population_sha256=frozen['seed42_parity']['scored_population_sha256'],
                evidence_regime='historical_development_full_information', limitations=cfg['limitations'],
                assembly_audit={'races': len(candidate.audit), 'assembly_eps': 0.0,
                    'below_legacy_clip_horses': sum(a['below_legacy_clip_horses'] for a in candidate.audit.values()),
                    'max_post_assembly_win_change': max(a['max_post_assembly_win_change'] for a in candidate.audit.values())},
                elapsed_seconds=time.monotonic() - t0)
            result['research_disposition'] = s.research.assess_research(result)
            verify()
            write_json(evidence, report.evidence.to_dict())
            result.update(evidence_path=str(evidence), evidence_sha256=s.p.digest(evidence))
            output = result_path(seed, contrast['id'])
            write_json(output, result)
            write_json(area / f"{contrast['id']}-receipt.json", {'report_sha256': s.p.digest(output),
                'evidence_sha256': s.p.digest(evidence), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed})
            print(f"RESULT seed={seed} {contrast['id']} diff={result['periods']['all']['diff']:+.8f} {result['research_disposition']['state']}", flush=True)
            del candidate, baseline, selected, report
            gc.collect()
    print('EVALUATE COMPLETE four fresh reports; seed42 remains original115 evidence', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'smoke', 'train', 'evaluate'])
    parser.add_argument('--workers', type=int, choices=[1, 2], default=2)
    parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        if args.action != 'train':
            parser.error('--worker only valid for train')
        worker(args.worker)
    elif args.action == 'train':
        train(args.workers)
    else:
        {'prepare': prepare, 'smoke': smoke, 'evaluate': evaluate}[args.action]()


if __name__ == '__main__':
    main()
