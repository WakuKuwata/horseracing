"""130: seven-year walk-forward of the 129 six-member mixture under the PREWEIGHT regime.

Why: research 125 scored the mixture with full information (same-day weight known), while
serving predicts before weights are published. The 129 rehearsal (4 race days) could not
settle whether the research gain survives that regime. This driver refits both recipes
per year (2020..2026, expanding window, 111 snapshot) and predicts every scored race
under BOTH regimes from the SAME fit, so the regime effect is isolated from fit noise.

Comparator = anchor-42 (138 columns, seed 42, no correction) = the production recipe.
Candidate = six corrected members averaged, with the SAME annual coefficients research 125
and 118 saved (fit on prior years under full information; a deployment would use them as-is).

Diagnostic only: no DB write, no model registration, can_adopt=False.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import datetime as dt
import gc
import json
import math
import os
from pathlib import Path
import pickle
import subprocess
import sys
import time

import numpy as np
import pandas as pd

import candidate_mixture_build as build
import joint_residual_stack as research
from horseracing_eval.bootstrap import inflate_for_seed_noise, race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import population_masks
from horseracing_features.weight_mask import MaskSpec
from horseracing_training.calib_split import OofCalibratedPredictor

sys.path.insert(0, str(build.ROOT / 'serving/src'))
from horseracing_serving import mixture_correction as mc  # noqa: E402

ROOT = build.ROOT
WORK = ROOT / 'artifacts/130-mixture-preweight-walkforward'
SPEC = ROOT / 'specs/130-mixture-preweight-walkforward'
MEMBERS = build.MEMBERS
YEARS = list(range(2020, 2027))
REGIMES = {'full': None, 'preweight': MaskSpec(rate=1.0, seed=20260810)}
BOOT = dict(b=4000, seed=20260907, alpha=.0125)
SD_FOLD = .001816
digest, read_json, write_json, stable_hash = build.digest, build.read_json, build.write_json, build.s.p.stable_hash


def config():
    return dict(schema_version=1, artifact_kind='mixture_preweight_walkforward_config', years=YEARS,
                members=[m['id'] for m in MEMBERS], comparator='anchor-42', regimes=list(REGIMES),
                preweight_mask=dict(rate=1., seed=20260810, unit='race'), n_estimators=900, n_oof_blocks=8,
                bootstrap=BOOT, sd_fold=SD_FOLD, k_seeds=1, n_effective_folds=1,
                coefficients='annual, from research 125 (joint) and 118 (anchor), fit under full information',
                can_adopt=False, eligible_for_verdict=False)


def source_hash():
    files = [Path(__file__).resolve(), ROOT / 'scripts/candidate_mixture_build.py',
             ROOT / 'serving/src/horseracing_serving/mixture_correction.py']
    return stable_hash({str(p): digest(p) for p in files} | {'125_source': research.source_hash()})


def inputs():
    matrix, races, folds = research.s.inputs(research.s.load_config())
    missing = [y for y in YEARS if y not in folds]
    if missing:
        raise ValueError(f'Snapshot lacks folds for {missing}')
    return matrix, races, folds


def freeze_path(): return WORK / 'run-freeze.json'
def cache_path(member_id, year, smoke=False):
    return WORK / ('smoke' if smoke else 'cache') / f'{member_id}-{year}.pkl'
def receipt_path(member_id, year, smoke=False):
    return WORK / ('smoke-receipts' if smoke else 'receipts') / f'{member_id}-{year}.json'


def prepare():
    if freeze_path().exists():
        raise FileExistsError('130 freeze exists; never overwrite')
    matrix, races, folds = inputs()
    population = {}
    for year in YEARS:
        fold = folds[year]
        train = [r.context for r in fold.train]
        if max(r.race_date.year for r in train) + 1 != year:
            raise ValueError('Fold boundary differs')
        population[str(year)] = {'n_train_races': len(train), 'n_valid_races': len(fold.valid),
                                 'train_hash': build.s.train_identity(train),
                                 'valid_ids_sha256': stable_hash([r.context.race_id for r in fold.valid])}
    frozen125 = read_json(research.WORK / 'run-freeze.json')
    frozen118 = read_json(research.a.WORK / 'run-freeze.json')
    coefficients = {}
    for seed in (42, 43, 44):
        joint = research.verified_coefficients(seed, frozen125)
        anchor = research.a.verified_coefficients(seed, frozen118)
        coefficients[f'joint-{seed}'] = {str(y): [float(v) for v in joint['gammas'][str(y)]] for y in YEARS}
        coefficients[f'anchor-{seed}'] = {str(y): [float(anchor['gammas'][str(y)])] for y in YEARS}
    frozen = {'schema_version': 1, 'artifact_kind': 'mixture_preweight_walkforward_freeze', 'config': config(),
              'config_hash': stable_hash(config()), 'source_hash': source_hash(), 'runtime': build.runtime(),
              'snapshot_sha256': digest(build.s.p.WORK / 'snapshot.pkl'),
              'research125_freeze_sha256': digest(research.WORK / 'run-freeze.json'),
              'research118_freeze_sha256': digest(research.a.WORK / 'run-freeze.json'),
              'coefficients': coefficients, 'population': population,
              'jobs': [{'member': m['id'], 'year': y} for m in MEMBERS for y in YEARS],
              'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'can_adopt': False, 'eligible_for_verdict': False}
    del matrix, races, folds
    gc.collect()
    write_json(freeze_path(), frozen)
    print(f'PREPARE PASS: {len(frozen["jobs"])} fits registered', flush=True)


def verify():
    frozen = read_json(freeze_path())
    if (frozen.get('config') != config() or frozen.get('config_hash') != stable_hash(config())
            or frozen.get('source_hash') != source_hash() or frozen.get('runtime') != build.runtime()
            or frozen.get('snapshot_sha256') != digest(build.s.p.WORK / 'snapshot.pkl')):
        raise ValueError('Frozen config/source/runtime/snapshot changed')
    return frozen


def _heads(predictions, ids):
    return {h: (float(predictions[h].win), float(predictions[h].top2), float(predictions[h].top3)) for h in ids}


def completed(member_id, year, frozen, smoke=False):
    path, receipt = cache_path(member_id, year, smoke), receipt_path(member_id, year, smoke)
    if not path.exists() and not receipt.exists():
        return False
    if not (path.exists() and receipt.exists()):
        raise ValueError(f'Partial output for {member_id}-{year}; inspect before retry')
    r = read_json(receipt)
    if (r.get('source_hash') != frozen['source_hash'] or r.get('cache_sha256') != digest(path)
            or r.get('member') != member_id or r.get('year') != year or r.get('smoke') is not smoke):
        raise ValueError(f'Receipt identity changed for {member_id}-{year}')
    return True


def fit_member(member_id, smoke=False):
    frozen = verify()
    member = build.member_for(member_id)
    matrix, races, folds = inputs()
    years = [YEARS[0]] if smoke else YEARS
    for year in years:
        if completed(member_id, year, frozen, smoke):
            print(f'CACHE {member_id} {year}', flush=True)
            continue
        fold = folds[year]
        train = list(fold.train)
        valid = list(fold.valid)
        if smoke:
            train = [r for r in train if r.context.race_date.year <= 2008]
            valid = valid[:40]
        if build.s.train_identity([r.context for r in fold.train]) != frozen['population'][str(year)]['train_hash']:
            raise ValueError('Training population differs from freeze')
        t0 = time.monotonic()
        print(f'FIT {member_id} {year} train={len(train)} valid={len(valid)}', flush=True)
        predictor = OofCalibratedPredictor(None, build.make_recipe(member, smoke), shared_data=matrix,
                                           n_oof_blocks=2 if smoke else 8, method='isotonic',
                                           require_sufficient=True)
        with build.frozen_outcomes(train):
            predictor.fit([r.context for r in train], num_threads=1)
        if predictor._base.feature_cols_ != build.expected_columns(member):
            raise ValueError('Fitted columns differ from the registered member profile')
        out = {}
        for name, spec in REGIMES.items():
            predictor.set_predict_weight_mask(spec)
            out[name] = {}
            for er in valid:
                ctx = er.context
                ids = [h.horse_id for h in ctx.started_horses]
                p = predictor.predict_race(ctx)
                if list(p) != ids:
                    raise ValueError('Prediction started order differs')
                heads = _heads(p, ids)
                wins = np.array([v[0] for v in heads.values()])
                if not np.isfinite(wins).all() or not np.isclose(wins.sum(), 1., atol=1e-8):
                    raise ValueError('Invalid win vector')
                out[name][ctx.race_id] = heads
        predictor.set_predict_weight_mask(None)
        elapsed = time.monotonic() - t0
        path = cache_path(member_id, year, smoke)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as fh:
            pickle.dump({'member': member_id, 'year': year, 'smoke': smoke, 'predictions': out,
                         'oof_info': build.clean(predictor.oof_info_), 'feature_cols': predictor._base.feature_cols_,
                         'n_valid': len(valid), 'elapsed_seconds': elapsed}, fh, protocol=5)
        write_json(receipt_path(member_id, year, smoke), {
            'member': member_id, 'year': year, 'smoke': smoke, 'source_hash': frozen['source_hash'],
            'cache_sha256': digest(path), 'n_valid': len(valid), 'fit_seconds': elapsed,
            'completed_at': dt.datetime.now(dt.timezone.utc).isoformat()})
        del predictor
        gc.collect()
        print(f'FIT OK {member_id} {year} elapsed={elapsed:.0f}s', flush=True)


def run(workers=2, smoke=False):
    build.check_workers(workers)
    build.assert_no_other_training()
    verify()
    lock = WORK / 'running.lock'
    if lock.exists():
        raise FileExistsError('130 run already active')
    write_json(lock, {'pid': os.getpid(), 'workers': workers, 'smoke': smoke})
    logs = WORK / 'logs' / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    logs.mkdir(parents=True)

    def launch(member):
        cmd = [sys.executable, str(Path(__file__).resolve()), 'worker', '--member', member['id']]
        if smoke:
            cmd.append('--smoke')
        with (logs / (member['id'] + '.log')).open('x') as fh:
            result = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                                    env={**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'})
        if result.returncode:
            raise RuntimeError(f'Worker failed; see {logs / (member["id"] + ".log")}')
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            queue = iter(MEMBERS)
            running = {pool.submit(launch, m) for m in [next(queue, None) for _ in range(workers)] if m}
            while running:
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for f in done:
                    f.result()
                for _ in done:
                    m = next(queue, None)
                    if m:
                        running.add(pool.submit(launch, m))
    finally:
        lock.unlink()
    print(f'RUN COMPLETE smoke={smoke}', flush=True)


def _ci(diffs_by_day):
    ci = race_day_cluster_bootstrap_ci_v1(diffs_by_day, **BOOT)
    total = inflate_for_seed_noise(ci, sd_fold=SD_FOLD, n_folds=1, k_seeds=1, alpha=BOOT['alpha'])
    return {'point': ci.point, 'sample_ci': [ci.ci_low, ci.ci_high], 'total_ci': [total.ci_low, total.ci_high],
            'n_days': len(diffs_by_day), 'n_races': sum(len(v) for v in diffs_by_day.values())}


def summarize(output=None):
    frozen = verify()
    for m in MEMBERS:
        for y in YEARS:
            if not completed(m['id'], y, frozen):
                raise ValueError(f'Missing {m["id"]}-{y}')
    output = Path(output or SPEC / 'evidence/walkforward-summary.json')
    if output.exists():
        raise FileExistsError('Summary exists; never overwrite')
    matrix, races, folds = inputs()
    KEYS = mc.KEYS
    frame = matrix.frame
    years = pd.to_datetime(frame.race_date).dt.year
    target = frame.loc[years.isin(YEARS), KEYS + ['days_since_last', 'sex']]
    inputs_frame = mc.build_correction_inputs(target, frame[KEYS])
    per_race = {rid: g.set_index('horse_id', drop=False) for rid, g in inputs_frame.groupby('race_id', sort=False)}
    del target, inputs_frame
    caches = {(m['id'], y): pickle.load(cache_path(m['id'], y).open('rb')) for m in MEMBERS for y in YEARS}
    rows = []
    by_day = {reg: {} for reg in REGIMES}
    by_day_year = {reg: {y: {} for y in YEARS} for reg in REGIMES}
    anchor_regime_gap = {}
    for year in YEARS:
        for er in folds[year].valid:
            ctx = er.context
            pop = population_masks(er)
            if not pop.eligible:
                continue
            ids = [h.horse_id for h in ctx.started_horses]
            corr = per_race[ctx.race_id].loc[ids].reset_index(drop=True)
            record = {'race_id': ctx.race_id, 'race_day': str(ctx.race_date), 'year': year, 'winner': pop.winner_horse_id}
            for reg in REGIMES:
                members = []
                for m in MEMBERS:
                    heads = caches[(m['id'], year)]['predictions'][reg][ctx.race_id]
                    base = np.array([heads[h][0] for h in ids])
                    terms = mc.JOINT_TERMS if m['branch'] == 'pruning' else ['gap_log']
                    coef = frozen['coefficients'][m['id']][str(year)]
                    members.append(mc.correct_member_predictions(ids, base, corr, terms, coef))
                mix = mc.average_member_predictions(members)
                anchor = caches[('anchor-42', year)]['predictions'][reg][ctx.race_id]
                c_nll = -math.log(mix[pop.winner_horse_id].win)
                a_nll = -math.log(anchor[pop.winner_horse_id][0])
                record[f'{reg}_candidate_nll'] = c_nll
                record[f'{reg}_anchor_nll'] = a_nll
                by_day[reg].setdefault(str(ctx.race_date), []).append(c_nll - a_nll)
                by_day_year[reg][year].setdefault(str(ctx.race_date), []).append(c_nll - a_nll)
            anchor_regime_gap.setdefault(str(ctx.race_date), []).append(record['preweight_anchor_nll'] - record['full_anchor_nll'])
            rows.append(record)
    summary = {
        'artifact_kind': 'mixture_preweight_walkforward_summary', 'config_hash': frozen['config_hash'],
        'source_hash': frozen['source_hash'], 'n_eligible_races': len(rows), 'years': YEARS,
        'candidate_minus_anchor42': {reg: _ci(by_day[reg]) for reg in REGIMES},
        'by_year': {reg: {str(y): _ci(by_day_year[reg][y]) for y in YEARS} for reg in REGIMES},
        'mean_nll': {reg: {'candidate': float(np.mean([r[f'{reg}_candidate_nll'] for r in rows])),
                           'anchor42': float(np.mean([r[f'{reg}_anchor_nll'] for r in rows]))} for reg in REGIMES},
        'anchor42_preweight_minus_full': _ci(anchor_regime_gap),
        'research125_full_reference': -0.009245463,
        'fit_seconds': {f'{m["id"]}-{y}': read_json(receipt_path(m['id'], y))['fit_seconds'] for m in MEMBERS for y in YEARS},
        'scope': ('Fresh annual refits of both recipes on the 111 snapshot; the same fit predicts both regimes. '
                  'Coefficients are the research annual vectors (full-information fit). Diagnostic for the '
                  'preweight regime question only; not a prospective confirmation.'),
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'can_adopt': False, 'eligible_for_verdict': False}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, summary)
    write_json(output.with_name('walkforward-races.json'), {'rows': rows, 'summary_sha256': digest(output)})
    print(json.dumps({reg: summary['candidate_minus_anchor42'][reg] for reg in REGIMES}, indent=1))
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'run', 'worker', 'summarize'])
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--member', choices=[m['id'] for m in MEMBERS])
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--output', type=Path, default=None)
    a = p.parse_args()
    if a.command == 'prepare':
        prepare()
    elif a.command == 'run':
        run(a.workers, a.smoke)
    elif a.command == 'worker':
        if a.member is None:
            p.error('worker requires --member')
        fit_member(a.member, a.smoke)
    else:
        summarize(a.output)


if __name__ == '__main__':
    main()
