"""113 historical-development stack: exact old-cache replay, eight fresh bundle fits.

No action here creates a production verdict, reserves a confirmation slot, or writes
an older experiment. The 111 snapshot is historical input, not a fresh DB snapshot.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
import datetime as dt
import gc
import importlib.metadata
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

import pandas as pd
import ability_observation as p
import ability_observation_reuse as reuse
import feature_pruning as old
import feature_pruning_prefill as old_prefill
import small_gain_research as research
from horseracing_eval.splits import expanding_folds

ROOT = p.ROOT
SPEC = ROOT / 'specs/113-small-gain-stack'
WORK = ROOT / 'artifacts/113-small-gain-stack'
PACKAGES = ('numpy', 'pandas', 'lightgbm', 'scikit-learn')


def runtime():
    return {'python': sys.version, 'packages': {v: importlib.metadata.version(v) for v in PACKAGES}}


def read_json(path):
    return json.loads(Path(path).read_text())


def strip_drops(value):
    if isinstance(value, dict):
        return {k: strip_drops(v) for k, v in value.items() if k != 'drop_features'}
    if isinstance(value, (list, tuple)):
        return [strip_drops(v) for v in value]
    return value


def validate_config(cfg, old_cfg):
    relative = next(c['drop_features'] for c in old_cfg['candidates'] if c['id'] == 'relative_ability')
    obs = p.OBSERVATION_COLUMNS
    expected = [
        {'id': 'anchor', 'drop_features': obs},
        {'id': 'pruning', 'drop_features': obs + relative},
        {'id': 'stack', 'drop_features': ['asof_spdfig_sd'] + relative},
    ]
    if cfg.get('study_arms') != expected or len(relative) != 13:
        raise ValueError('Registered three-arm feature scope changed')
    if cfg.get('contrasts') != [
        {'id': 'increment', 'candidate': 'stack', 'baseline': 'pruning'},
        {'id': 'anchor', 'candidate': 'stack', 'baseline': 'anchor'},
    ]:
        raise ValueError('Registered two contrasts changed')
    if cfg['arms'] != old_cfg['arms'] or cfg['eval_window'] != old_cfg['eval_window']:
        raise ValueError('Historical effective recipe/window changed')
    if cfg['arms']['seed'] != 42 or cfg['arms']['n_estimators'] != 900 or cfg['arms']['n_oof_blocks'] != 8:
        raise ValueError('Study recipe must be 42/900/8')
    if cfg['min_effect_delta'] != 0 or cfg['bootstrap']['alpha'] != .0125 or cfg['bootstrap']['b'] != 4000:
        raise ValueError('Study research gate changed')
    if cfg['smoke']['eval_window'] != {'from': '2008-01-01', 'to': '2008-01-31', 'min_eval_days': 1}:
        raise ValueError('Smoke window changed')


def load_config():
    cfg = read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if p.gate_config_hash(cfg) != expected:
        raise ValueError('Frozen config changed')
    p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    p.assert_delta_provenance(cfg, root=ROOT)
    validate_config(cfg, old.load_config())
    return cfg


def source_hash():
    return p.stable_hash({
        'driver': p.digest(__file__), '111_source': p.source_hash(),
        '110_source': old.source_hash(), 'reuse': p.digest(reuse.__file__),
        'old_prefill': p.digest(old_prefill.__file__),
        'research_policy': p.digest(research.__file__),
    })


def arm(cfg, name):
    return next(a for a in cfg['study_arms'] if a['id'] == name)


def inputs(cfg, smoke=False):
    matrix, all_races = reuse.load(p.WORK / 'snapshot.pkl')
    window = cfg['smoke']['eval_window'] if smoke else cfg['eval_window']
    start, end = (dt.date.fromisoformat(window[k]) for k in ('from', 'to'))
    races = [r for r in all_races if r.context.race_date <= end]
    folds = {f.valid_year: f for f in expanding_folds(races, start.year, valid_from=start)}
    return matrix, races, folds


def train_identity(train):
    return p.stable_hash([(r.race_id, str(r.race_date), [h.horse_id for h in r.started_horses]) for r in train])


def identity(frozen, stage):
    return [frozen['snapshot_sha256'], frozen['source_hash'], frozen['config_hash'], stage]


def key_for(factory, fold):
    train = [r.context for r in fold.train]
    if max(r.race_date.year for r in train) + 1 != fold.valid_year:
        raise ValueError('Train/validation boundary mismatch')
    th = train_identity(train)
    return p.stable_hash([factory.identity, factory.recipe_hash, th, fold.valid_year]), th


@contextmanager
def isolated_cache():
    # CalibSplitFactory and the old verification modules keep their original
    # input roots. Only CachedFactory.fit resolves this global output directory.
    original = p.WORK
    p.WORK = WORK
    try:
        yield
    finally:
        p.WORK = original


class FreshFactory(p.CachedFactory):
    def fit(self, train_races, *, num_threads=None):
        with isolated_cache():
            return super().fit(train_races, num_threads=1)


def factory(cfg, frozen, matrix, races, name, smoke=False):
    return FreshFactory(cfg, matrix, races, identity(frozen, 'smoke' if smoke else 'full'),
                        drops=arm(cfg, name)['drop_features'], smoke=smoke, label=name)


def validate_receipt(record):
    path, receipt_path = Path(record['path']), Path(record['receipt_path'])
    if p.digest(path) != record['sha256'] or p.digest(receipt_path) != record['receipt_sha256']:
        raise ValueError('Source cache or receipt changed')
    receipt = read_json(receipt_path)
    if receipt['job'] != record['source_job'] or receipt['cache_sha256'] != record['sha256']:
        raise ValueError('Source cache receipt identity mismatch')


def verify():
    cfg = load_config()
    frozen = read_json(WORK / 'run-freeze.json')
    if frozen['source_hash'] != source_hash() or frozen['config_hash'] != p.gate_config_hash(cfg):
        raise ValueError('Study source/config changed since freeze')
    if frozen['runtime'] != runtime():
        raise ValueError('Study runtime changed')
    for path, sha in frozen['source_files'].items():
        if p.digest(Path(path)) != sha:
            raise ValueError(f'Frozen source artifact changed: {path}')
    for row in frozen['source_caches']:
        validate_receipt(row)
    return cfg, frozen


def check_matrix_parity(a, ar, b, br):
    if list(b.frame.columns) != list(a.frame.columns) + p.OBSERVATION_COLUMNS:
        raise ValueError('Snapshot frame scope changed')
    pd.testing.assert_frame_equal(a.frame, b.frame[a.frame.columns], check_exact=True)
    if (a.feature_cols != p.columns_from_model() or b.feature_cols != a.feature_cols + p.OBSERVATION_COLUMNS
        or a.categorical_cols != b.categorical_cols or a.build_audit != b.build_audit):
        raise ValueError('Snapshot feature/category/build parity failed')
    if len(ar) != len(br) or any(x != y for x, y in zip(ar, br)):
        raise ValueError('Snapshot evaluation population/outcomes changed')


def verify_historical_certificate():
    cert = read_json(p.WORK / 'baseline-equivalence.json')
    expected = {'method_sha256': p.digest(reuse.__file__),
                'old_freeze_sha256': p.digest(old.WORK / 'run-freeze.json'),
                'new_freeze_sha256': p.digest(p.WORK / 'run-freeze.json')}
    if any(cert.get(k) != value for k, value in expected.items()):
        raise ValueError('Historical equivalence certificate method/freeze changed')
    for key in ('full_frame_projection_exact', 'all_eval_races_and_folds_exact', 'effective_recipe_exact'):
        if cert.get(key) is not True:
            raise ValueError('Historical equivalence certificate incomplete')
    screen = cert.get('fresh_screen_parity', {})
    if screen.get('all_probabilities_exact') is not True or screen.get('oof_info_exact') is not True:
        raise ValueError('Historical fresh-fit parity evidence incomplete')
    if cert.get('runtime') != runtime():
        raise ValueError('Runtime differs from historical fresh 900-tree parity certificate')


def prepare(cfg):
    if (WORK / 'run-freeze.json').exists():
        raise FileExistsError('Study already frozen')
    before_source = source_hash()
    oc, om, of = reuse.verify_freeze(old)
    nc, nm, nf = reuse.verify_freeze(p)
    old_manifest = old_prefill.manifest()
    verify_historical_certificate()
    if cfg['arms'] != nc['arms'] or cfg['eval_window'] != nc['eval_window']:
        raise ValueError('111 effective recipe/window changed')
    a, ar = reuse.load(old.WORK / 'snapshot.pkl')
    b, br = reuse.load(p.WORK / 'snapshot.pkl')
    check_matrix_parity(a, ar, b, br)
    start, end = (dt.date.fromisoformat(cfg['eval_window'][k]) for k in ('from', 'to'))
    ra = [r for r in ar if r.context.race_date <= end]
    rb = [r for r in br if r.context.race_date <= end]
    fa = list(expanding_folds(ra, start.year, valid_from=start))
    fb = list(expanding_folds(rb, start.year, valid_from=start))
    if fa != fb or [f.valid_year for f in fa] != list(range(2019, 2027)):
        raise ValueError('Expected eight exactly shared validation folds')
    frozen = {'snapshot_sha256': nm['snapshot_sha256'], 'source_hash': before_source,
              'config_hash': p.gate_config_hash(cfg), 'runtime': runtime()}
    records, jobs = [], []
    for fold in fa:
        for name, old_name, drops in [('anchor', 'baseline', []),
                                     ('pruning', 'relative_ability', arm(cfg, 'stack')['drop_features'][1:])]:
            key, th, original_factory = reuse.key(old, om, of, oc, a, ra, fold, 'full', drops)
            target = factory(cfg, frozen, b, rb, name)
            if (original_factory.expected_columns != target.expected_columns
                or strip_drops(original_factory.recipe_meta) != strip_drops(target.recipe_meta)):
                raise ValueError('Source/target effective recipe or columns differ')
            source_job = next(j for j in old_manifest['jobs'] if j['key'] == key)
            if source_job != {'key': key, 'year': fold.valid_year, 'arm': {'id': old_name, 'drop_features': drops}}:
                raise ValueError('Unexpected historical source job')
            path = old.WORK / 'cache' / f'{key}.pkl'
            receipt = old.WORK / 'prefill' / f'{key}.json'
            row = {'arm': name, 'year': fold.valid_year, 'key': key, 'train_hash': th,
                   'path': str(path), 'sha256': p.digest(path), 'receipt_path': str(receipt),
                   'receipt_sha256': p.digest(receipt), 'source_job': source_job}
            validate_receipt(row)
            cache = reuse.load(path)
            reuse.check_payload(cache, key, th, original_factory, list(fold.valid))
            row['historical_fit_seconds'] = cache['elapsed_seconds']
            records.append(row)
        target = factory(cfg, frozen, b, rb, 'stack')
        key, th = key_for(target, fold)
        jobs.append({'key': key, 'year': fold.valid_year, 'train_hash': th, 'arm': arm(cfg, 'stack')})
        print(f'CERTIFIED shared inputs, source anchor/pruning {fold.valid_year}', flush=True)
    paths = []
    for driver in (old, p):
        paths.extend(driver.WORK / n for n in ('snapshot.pkl', 'snapshot.json', 'run-freeze.json'))
        paths.extend(driver.SPEC / n for n in ('gate-config.json', 'gate-config.hash.txt'))
    paths.extend([p.WORK / 'source-frames.pkl', p.WORK / 'baseline-equivalence.json',
                  old_prefill.MANIFEST, old_prefill.AREA / 'manifest-freeze.json', p.MODEL / 'model.txt',
                  ROOT / cfg['delta_derivation_ref']])
    paths.extend(old.SPEC / 'evidence' / f'screen-{label}.json' for label in old_manifest['screen_sha256'])
    source_files = {str(path): p.digest(path) for path in paths}
    reuse.verify_freeze(old)
    reuse.verify_freeze(p)
    old_prefill.manifest()
    if source_hash() != before_source:
        raise ValueError('Execution source changed during preparation')
    frozen.update(source_files=source_files, source_caches=records, jobs=list(reversed(jobs)),
        prepared_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        evidence_regime='historical_development_full_information', can_adopt=False,
        parity={'full_shared_matrix_exact': True, 'categorical_exact': True, 'eval_races_exact': True,
                'folds_exact': True, 'effective_recipes_exact_except_registered_scope': True},
        n_rows=len(b.frame), n_races=len(br),
        note='111 historical snapshot reused read-only; 110 original caches replayed without conversion. No fresh DB read or unused holdout claim.')
    p.write_json(WORK / 'run-freeze.json', frozen)
    verify()
    print('PREPARE OK: 16 certified source caches, 8 fresh stack jobs; no fitting yet', flush=True)


def receipt_path(key):
    return WORK / 'prefill' / f'{key}.json'


def completed(job):
    receipt = receipt_path(job['key'])
    path = WORK / 'cache' / f"{job['key']}.pkl"
    if not receipt.exists():
        return False
    result = read_json(receipt)
    if (result['job'] != job or result['cache_sha256'] != p.digest(path)
        or result['run_freeze_sha256'] != p.digest(WORK / 'run-freeze.json')
        or result.get('imported') is not False or result.get('model_threads') != 1):
        raise ValueError('Fresh cache completion receipt changed')
    return True


def worker(key):
    cfg, frozen = verify()
    job = next(j for j in frozen['jobs'] if j['key'] == key)
    if completed(job):
        return
    path = WORK / 'cache' / f'{key}.pkl'
    if path.exists():
        raise ValueError('Unreceipted cache exists; preserve it for independent diagnosis before any retry')
    matrix, races, folds = inputs(cfg)
    f = factory(cfg, frozen, matrix, races, 'stack')
    fold = folds[job['year']]
    if key_for(f, fold) != (key, job['train_hash']):
        raise ValueError('Fresh job inputs changed')
    try:
        f.fit([r.context for r in fold.train], num_threads=1)
        cache = reuse.load(path)
        reuse.check_payload(cache, key, job['train_hash'], f, list(fold.valid))
        verify()
        p.write_json(receipt_path(key), {'job': job, 'cache_sha256': p.digest(path),
            'run_freeze_sha256': p.digest(WORK / 'run-freeze.json'), 'imported': False,
            'model_threads': 1, 'current_fit_seconds': cache['elapsed_seconds'],
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)})
    except BaseException:
        # Keep failed work for diagnosis, but never let a retry bless it by replay.
        if path.exists() and not receipt_path(key).exists():
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            quarantine = WORK / 'prefill' / f'unverified-{key}-{stamp}.pkl'
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            path.rename(quarantine)
        raise


def launch(job, run_id):
    log = WORK / 'prefill' / f"{run_id}-{job['key']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print(f"START fresh stack {job['year']}", flush=True)
    with log.open('x') as output:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), 'train', '--worker', job['key']],
                       cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)
    if not completed(job):
        raise ValueError('Worker exited without valid receipt')
    print(f"DONE fresh stack {job['year']}", flush=True)


def train(workers):
    cfg, frozen = verify()
    smoke_path = SPEC / 'evidence' / 'smoke.json'
    smoke_result = read_json(smoke_path)
    if smoke_result.get('structure') != 'PASS' or smoke_result.get('run_freeze_sha256') != p.digest(WORK / 'run-freeze.json'):
        raise ValueError('Frozen three-arm structural smoke required before full training')
    if workers not in (1, 2):
        raise ValueError('Only one or two isolated workers are allowed')
    jobs = [j for j in frozen['jobs'] if not completed(j)]
    lock = WORK / 'running.lock'
    lock.mkdir()
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        p.write_json(WORK / 'prefill' / f'{run_id}-run.json', {'workers': workers, 'jobs': jobs,
            'run_freeze_sha256': p.digest(WORK / 'run-freeze.json'), 'smoke_sha256': p.digest(smoke_path)})
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(launch, j, run_id) for j in jobs]
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
    print(f'TRAIN COMPLETE fresh jobs={len(jobs)}', flush=True)


class ReadOnlyFactory(p.CachedFactory):
    def __init__(self, cfg, frozen, matrix, races, name):
        super().__init__(cfg, matrix, races, identity(frozen, 'full'), drops=arm(cfg, name)['drop_features'], label=name)
        self.frozen, self.name = frozen, name

    def fit(self, train_races, *, num_threads=None):
        th = train_identity(train_races)
        year = max(r.race_date.year for r in train_races) + 1
        if self.name == 'stack':
            job = next(j for j in self.frozen['jobs'] if j['year'] == year)
            if not completed(job) or job['train_hash'] != th:
                raise ValueError('Fresh stack cache missing or training population differs')
            key = p.stable_hash([self.identity, self.recipe_hash, th, year])
            if key != job['key']:
                raise ValueError('Fresh stack recipe/cache key mismatch')
            path = WORK / 'cache' / f'{key}.pkl'
            expected_factory = self
        else:
            row = next(r for r in self.frozen['source_caches'] if r['arm'] == self.name and r['year'] == year)
            validate_receipt(row)
            if row['train_hash'] != th:
                raise ValueError('Replayed training population differs')
            key, path = row['key'], Path(row['path'])
            # Preserve native tuple recipe metadata by loading original pickle.
            # Source factory may be built on shared augmented matrix only after
            # exact 110-column projection; no feature reconstruction occurs.
            from horseracing_training.dataset import TrainingMatrix
            source_matrix = TrainingMatrix(self.factory._shared.frame.drop(columns=p.OBSERVATION_COLUMNS),
                p.columns_from_model(), self.factory._shared.categorical_cols, self.factory._shared.build_audit)
            expected_factory = old.CachedFactory(old.load_config(), source_matrix, self.races, [],
                drops=row['source_job']['arm']['drop_features'], label=self.name)
            if (expected_factory.expected_columns != self.expected_columns
                or strip_drops(expected_factory.recipe_meta) != strip_drops(self.recipe_meta)):
                raise ValueError('Replayed effective recipe mismatch')
        cache = reuse.load(path)
        vr = [r for r in self.races if r.context.race_date.year == year]
        reuse.check_payload(cache, key, th, expected_factory, vr)
        print(f'REPLAY {self.name} year={year}', flush=True)
        return p.ReplayPredictor(cache['predictions'])


def smoke(cfg):
    cfg, frozen = verify()
    out = SPEC / 'evidence' / 'smoke.json'
    if out.exists():
        raise FileExistsError('Preserve existing smoke evidence')
    matrix, races, folds = inputs(cfg, smoke=True)
    phase = deepcopy(cfg)
    phase['eval_window'] = cfg['smoke']['eval_window']
    phase['subgroup_guard']['critical_subgroups'] = ['canonical']
    start = dt.date.fromisoformat(phase['eval_window']['from'])
    records = []
    for contrast in cfg['contrasts']:
        cand = factory(cfg, frozen, matrix, races, contrast['candidate'], smoke=True)
        base = factory(cfg, frozen, matrix, races, contrast['baseline'], smoke=True)
        report = p.paired_eval(cand, base, races, gate_config=phase, first_valid_year=start.year,
                              valid_from=start, subgroups=True, num_threads=1)
        records.append({'contrast': contrast, 'candidate_columns': cand.expected_columns,
                        'baseline_columns': base.expected_columns, 'n_races': report.n_races,
                        'primary_loss_changed': p.check_effect_difference(report, smoke=True)})
        del cand, base, report
        gc.collect()
    verify()
    p.write_json(out, {'structure': 'PASS', 'can_adopt': False, 'eligible_for_verdict': False,
        'artifact_kind': 'small_gain_stack_smoke', 'run_freeze_sha256': p.digest(WORK / 'run-freeze.json'),
        'n_estimators': 5, 'n_oof_blocks': 2, 'records': records,
        'note': 'Structural scope/probability check only; no efficacy judgement.'})
    print('SMOKE PASS all three arms', flush=True)


def evaluate(cfg):
    cfg, frozen = verify()
    if (WORK / 'running.lock').exists():
        raise ValueError('Wait for isolated training workers to finish')
    if not all(completed(j) for j in frozen['jobs']):
        raise ValueError('All eight fresh stack caches must have valid receipts')
    matrix, races, folds = inputs(cfg)
    start = dt.date.fromisoformat(cfg['eval_window']['from'])
    for contrast in cfg['contrasts']:
        out = SPEC / 'evidence' / f"full-{contrast['id']}.json"
        if verified_result(out, cfg, contrast):
            print(f"COMPLETED {contrast['id']}; verified existing evidence", flush=True)
            continue
        cand = ReadOnlyFactory(cfg, frozen, matrix, races, contrast['candidate'])
        base = ReadOnlyFactory(cfg, frozen, matrix, races, contrast['baseline'])
        t0 = time.monotonic()
        report = p.paired_eval(cand, base, races, gate_config=cfg, first_valid_year=start.year,
            valid_from=start, subgroups=True, num_threads=1,
            snapshot={'run_freeze_sha256': p.digest(WORK / 'run-freeze.json'),
                      'source_snapshot_sha256': frozen['snapshot_sha256'], 'contrast': contrast,
                      'evidence_regime': frozen['evidence_regime']})
        # Exact registered scope, recipe and input checks already establish that
        # the intended model was evaluated. Equal losses are a valid no-gain
        # research result, and should reach DEFER instead of raising an error.
        primary_loss_changed = any(row.diff != 0 for row in report.evidence.rows)
        evidence = WORK / f"full-{contrast['id']}-evidence.json"
        p.write_json(evidence, report.evidence.to_dict())
        result = report.to_dict()
        result.pop('evidence', None)
        result.pop('diffs_by_day', None)
        result['gate_readout'] = result.pop('decision')
        result.update(artifact_kind='small_gain_stack_research_report', stage='full',
            can_adopt=False, eligible_for_verdict=False, evidence_regime=frozen['evidence_regime'],
            contrast=contrast, study_config_hash=p.gate_config_hash(cfg),
            run_freeze_sha256=p.digest(WORK / 'run-freeze.json'),
            evidence_path=str(evidence), evidence_sha256=p.digest(evidence),
            candidate_columns=cand.expected_columns, baseline_columns=base.expected_columns,
            primary_loss_changed=primary_loss_changed,
            elapsed_seconds=time.monotonic() - t0)
        result['research_disposition'] = research.assess_research(result)
        verify()
        p.write_json(out, result)
        print(f"RESULT {contrast['id']} diff={report.periods['all']['diff']:+.8f} research={result['research_disposition']['state']}", flush=True)
        del cand, base, report
        gc.collect()
    print('EVALUATE COMPLETE; historical research only, no adoption', flush=True)


def verified_result(path, cfg, contrast):
    if not path.exists():
        return False
    result = read_json(path)
    evidence = WORK / f"full-{contrast['id']}-evidence.json"
    if (result.get('artifact_kind') != 'small_gain_stack_research_report'
        or result.get('can_adopt') is not False or result.get('eligible_for_verdict') is not False
        or result.get('study_config_hash') != p.gate_config_hash(cfg)
        or result.get('run_freeze_sha256') != p.digest(WORK / 'run-freeze.json')
        or result.get('contrast') != contrast or result.get('evidence_path') != str(evidence)
        or result.get('evidence_sha256') != p.digest(evidence)
        or result.get('research_disposition') != research.assess_research(result)):
        raise ValueError('Existing research report provenance changed; preserve for diagnosis')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'smoke', 'train', 'evaluate'])
    parser.add_argument('--workers', type=int, choices=[1, 2], default=2)
    parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        if args.stage != 'train':
            parser.error('--worker is only valid with train')
        worker(args.worker)
    else:
        cfg = load_config()
        if args.stage == 'prepare':
            prepare(cfg)
        elif args.stage == 'smoke':
            smoke(cfg)
        elif args.stage == 'train':
            train(args.workers)
        else:
            evaluate(cfg)


if __name__ == '__main__':
    main()
