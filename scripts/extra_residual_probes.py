"""121: three registered, strict-prior-year residual probes on frozen pruning125 seeds."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import gc
import importlib.metadata
from pathlib import Path
import pickle
import time

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import minimize

import anchor_gap_recheck as a
import anchor_gap_summary as previous_summary
import gap_log_quality as g
import gap_seed_recheck as d
import small_gain_stack as s
from screen_architecture import _nll_grad
import screen_architecture as legacy
from horseracing_eval.dataset import population_masks
from horseracing_eval.residual_probe import RaceProbe, fit_gamma
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.paired import _clip_nll

ROOT = s.ROOT
SPEC = ROOT / 'specs/121-extra-residual-probes'
WORK = ROOT / 'artifacts/121-extra-residual-probes'
SEEDS = [42, 43, 44]
CANDIDATES = ['prior_gap', 'global_temperature', 'context_temperature']
CONTRASTS = {'prior_gap': ['retained'], 'global_temperature': ['retained'], 'context_temperature': ['retained', 'global_temperature']}
YEARS = list(range(2019, 2027))
write_json = g.screen.write_json


@dataclass(frozen=True)
class Row:
    race_id: str
    day: str
    p: np.ndarray
    gap: np.ndarray
    prior_gap: np.ndarray
    winner: int


def runtime():
    return {**s.runtime(), 'scipy': importlib.metadata.version('scipy')}


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    if s.p.gate_config_hash(cfg) != (SPEC / 'gate-config.hash.txt').read_text().strip():
        raise ValueError('121 fixed configuration changed')
    if (cfg['candidates'] != CANDIDATES or cfg['contrasts'] != CONTRASTS or cfg['seeds'] != SEEDS
        or cfg['warmup_year'] != 2019 or cfg['eval_window'] != {'from': '2020-01-01', 'to': '2026-08-23'}
        or cfg['bootstrap'] != {'b': 4000, 'seed': 20260907, 'alpha': .0125, 'block': 'race_day'}
        or cfg['fit'] != {'base_ridge': 1e-6, 'context_lambda': 1000.0, 'context_ridge_scale': 'lambda/n_fit_races',
                          'ab_max_iter': 50, 'ab_tol': 1e-9, 'c_max_iter': 1500, 'c_ftol': 1e-13, 'c_gtol': 1e-8,
                          'objective_max': 1e-8, 'gradient_inf_max': 1e-5, 'auto_retune': False}
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('121 registered scope changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '118_source': a.source_hash(),
                           'summary118': s.p.digest(previous_summary.__file__), 'legacy_context_formula': s.p.digest(legacy.__file__)})


def source_state():
    cfg118, frozen118 = a.verify()
    if (a.WORK / 'running.lock').exists() or not all(a.completed(j) for j in frozen118['jobs']):
        raise ValueError('118 must be complete')
    reports, hashes = {}, {}
    paths = [a.WORK / 'run-freeze.json', a.SPEC / 'verdict.json', d.SPEC / 'verdict.json',
             a.SPEC / 'evidence/independent-review.py', a.SPEC / 'evidence/independent-review.json']
    for seed in SEEDS:
        reports[seed] = {}
        hashes[str(seed)] = {}
        a.verified_coefficients(seed, frozen118)
        paths.extend([a.old_coefficient_path(seed), a.old_coefficient_path(seed).with_name('coefficients-receipt.json')])
        for contrast in a.CONTRASTS:
            if not a.verified_result(seed, contrast, cfg118, frozen118):
                raise ValueError('All 118 comparisons required')
            path = a.result_path(seed, contrast['id'])
            reports[seed][contrast['id']] = report = s.read_json(path)
            hashes[str(seed)][contrast['id']] = {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(report['evidence_path'])}
            paths.extend([path, Path(report['evidence_path']), a.seed_area(seed) / f"{contrast['id']}-receipt.json"])
    summary = s.read_json(a.SPEC / 'verdict.json')
    if (any(summary.get(k) != v for k, v in previous_summary.summarize_reports(reports).items())
        or summary.get('preferred_research_configuration') != 'PRUNING_GAP'):
        raise ValueError('121 requires retained pruning125+gap branch')
    review = s.read_json(a.SPEC / 'evidence/independent-review.json')
    if (review.get('status') != 'PASS' or review.get('can_adopt') is not False or review.get('eligible_for_verdict') is not False
        or review.get('method_sha256') != s.p.digest(a.SPEC / 'evidence/independent-review.py')
        or review.get('run_freeze_sha256') != s.p.digest(a.WORK / 'run-freeze.json')
        or review.get('summary_sha256') != s.p.digest(a.SPEC / 'verdict.json') or review.get('report_hashes') != hashes):
        raise ValueError('118 independent-review method/freeze/summary/report binding changed')
    return {'files': {str(p): s.p.digest(p) for p in paths}, 'population': frozen118['sources']['population'],
            'snapshot_sha256': frozen118['sources']['snapshot_sha256'],
            'retained_coefficients': {str(seed): a.old_coefficients(seed) for seed in SEEDS}}


def prior_gap_features(frame):
    """D1-D2 for distinct started days D2<D1<D; no target results or cross-ID repair."""
    needed = ['race_id', 'horse_id', 'race_date', 'days_since_last']
    if frame[needed[:3]].isna().any().any() or frame.duplicated(['race_id', 'horse_id']).any():
        raise ValueError('Missing/duplicate source identity')
    target = frame[needed].copy()
    target['race_date'] = pd.to_datetime(target['race_date'], errors='raise')
    hist = target[['horse_id', 'race_date']].drop_duplicates().sort_values(['horse_id', 'race_date'], kind='stable')
    group = hist.groupby('horse_id', sort=False)['race_date']
    hist['D1'], hist['D2'] = group.shift(1), group.shift(2)
    hist['prior_gap_days'] = (hist['D1'] - hist['D2']).dt.days
    if ((hist['D1'].notna() & (hist['D1'] >= hist['race_date']))
        | (hist['D2'].notna() & (hist['D2'] >= hist['D1']))).any():
        raise ValueError('Prior-day chronology violated')
    out = target.merge(hist, on=['horse_id', 'race_date'], how='left', validate='many_to_one', sort=False)
    gap = out['days_since_last'].to_numpy(dtype=float)
    if np.isinf(gap).any() or (gap[np.isfinite(gap)] <= 0).any() or (gap[np.isfinite(gap)] % 1 != 0).any():
        raise ValueError('Invalid current-gap day counts')
    prior = out['prior_gap_days'].to_numpy(dtype=float)
    if np.isinf(prior).any() or (prior[np.isfinite(prior)] <= 0).any():
        raise ValueError('Invalid prior-gap day counts')
    out['gap_log'], out['prior_gap_log'] = np.log1p(gap), np.log1p(prior)
    reconstructed_current = (out['race_date'] - out['D1']).dt.days.to_numpy(dtype=float)
    comparable = np.isfinite(gap) & np.isfinite(reconstructed_current)
    audit = {'source_rows': len(out), 'distinct_horse_days': len(hist),
             'prior_gap_missing_rows': int(np.isnan(prior).sum()), 'current_gap_missing_rows': int(np.isnan(gap).sum()),
             'current_gap_definition_differences': int(np.sum(gap[comparable] != reconstructed_current[comparable])),
             'history_scope': 'Only started rows retained in frozen111 matrix; distinct strictly-prior dates, same ID, no ID stitching.',
             'source_current_gap_preserved': True}
    return out, audit


def read_rows(factory, folds, lookup):
    rows, all_counts = [], {}
    for year, fold in sorted(folds.items()):
        predictor = factory.fit([r.context for r in fold.train], num_threads=1)
        all_counts[str(year)] = {'all_races': len(fold.valid), 'eligible_races': 0}
        for race in fold.valid:
            if race.n_result_rows is None:
                raise ValueError('Unknown result-row population')
            pop = population_masks(race)
            p = g.screen.validate_probabilities(predictor.predict_race(race.context), pop.started_horse_ids)
            keys = [(race.context.race_id, h) for h in pop.started_horse_ids]
            if any(k not in lookup or lookup[k][0] != str(race.context.race_date) for k in keys):
                raise ValueError('Residual source date/horse mismatch')
            if not pop.eligible:
                continue
            all_counts[str(year)]['eligible_races'] += 1
            rows.append(Row(race.context.race_id, str(race.context.race_date), p,
                            np.array([lookup[k][1] for k in keys]), np.array([lookup[k][2] for k in keys]),
                            pop.started_horse_ids.index(pop.winner_horse_id)))
        del predictor
    return rows, all_counts


def retained_parity(rows, reference, coefficients):
    scored = [r for r in rows if r.day >= '2020-01-01']
    if [(r.race_id,r.day) for r in scored] != [(r['race_id'],r['race_day']) for r in reference]:
        raise ValueError('Retained source ordered population differs')
    max_error = 0.0
    for row, old in zip(scored, reference, strict=True):
        q = corrected_p(row,np.nan_to_num(row.gap,nan=0.)[:,None],np.array([coefficients['gammas'][row.day[:4]]]))
        error = abs(_clip_nll(float(q[row.winner])) - old['candidate_winner_nll'])
        if error > 1e-12:
            raise ValueError('Retained baseline NLL differs from frozen115/116 evidence')
        max_error = max(max_error,error)
    return {'eligible_races':len(scored),'ordered_identity_sha256':s.p.stable_hash([(r.race_id,r.day) for r in scored]),
            'max_abs_nll_error':max_error, 'tolerance':1e-12}


def prepared_path(seed):
    return WORK / f'inputs-seed-{seed}.pkl'


def verify():
    cfg = load_config()
    frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != runtime() or frozen['sources'] != source_state()):
        raise ValueError('121 frozen source/config/runtime changed')
    for seed in SEEDS:
        if frozen['prepared_input_sha256'][str(seed)] != s.p.digest(prepared_path(seed)):
            raise ValueError('121 prepared input changed')
    return cfg, frozen


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify()
        print('PREPARE existing freeze verified', flush=True)
        return
    if any(prepared_path(seed).exists() for seed in SEEDS):
        raise ValueError('Unfrozen prepared inputs exist; preserve for diagnosis')
    cfg, before, state = load_config(), source_hash(), source_state()
    matrix, races, folds = s.inputs(s.load_config())
    augmented, audit = prior_gap_features(matrix.frame)
    augmented = augmented.loc[augmented.race_date >= pd.Timestamp('2019-01-01')]
    lookup = {(r.race_id, r.horse_id): (str(r.race_date.date()), float(r.gap_log), float(r.prior_gap_log)) for r in augmented.itertuples()}
    horse_sets = augmented.groupby('race_id', sort=False)['horse_id'].agg(set).to_dict()
    if any(horse_sets.get(r.context.race_id) != {h.horse_id for h in r.context.started_horses}
           for f in folds.values() for r in f.valid):
        raise ValueError('Snapshot started-horse population mismatch')
    del augmented
    population, reference = {}, None
    WORK.mkdir(parents=True, exist_ok=True)
    for seed in SEEDS:
        factory = a.retained_factory(matrix, races, seed)
        rows, counts = read_rows(factory, folds, lookup)
        identity = [(r.race_id, r.day) for r in rows]
        if reference is None:
            reference = identity
        if identity != reference or any(counts[str(y)][key] != state['population'][str(y)][key] for y in YEARS for key in ('all_races','eligible_races')):
            raise ValueError('Paired seed population mismatch')
        source_report = s.read_json(d.result_path(seed,'increment'))
        baseline_parity = retained_parity(rows,s.read_json(source_report['evidence_path'])['rows'],state['retained_coefficients'][str(seed)])
        with prepared_path(seed).open('xb') as f:
            pickle.dump([r.__dict__ for r in rows], f, protocol=pickle.HIGHEST_PROTOCOL)
        population[str(seed)] = {'years': counts, 'ordered_eligible_hash': s.p.stable_hash(identity), 'retained_baseline_parity':baseline_parity}
        del rows, factory
        gc.collect()
    del matrix, races, lookup
    gc.collect()
    if before != source_hash() or state != source_state():
        raise ValueError('Source changed during preparation')
    write_json(WORK / 'run-freeze.json', {'artifact_kind': 'extra_residual_probe_freeze', 'can_adopt': False,
        'eligible_for_verdict': False, 'source_hash': before, 'config_hash': s.p.gate_config_hash(cfg),
        'runtime': runtime(), 'sources': state, 'prior_gap_audit': audit, 'population': population,
        'prepared_input_sha256': {str(seed): s.p.digest(prepared_path(seed)) for seed in SEEDS},
        'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    print('PREPARE frozen three seeds; no coefficient fit or numerical probe yet', flush=True)


def score_context(row):
    p = row.p
    if not np.isfinite(p).all() or (p <= 0).any() or not np.isclose(p.sum(), 1., atol=1e-8, rtol=0):
        raise ValueError('Invalid context probability')
    lp = np.log(p)
    x = lp - lp.mean()
    n = len(p)
    entropy = float(-np.sum(p * lp) / np.log(n)) if n > 1 else 0.
    two = np.sort(p)[-2:] if n > 1 else np.array([0., p[0]])
    top_gap = float(np.log(two[-1] / max(two[0], 1e-12)))
    return x, entropy, top_gap


def context_edges(prior):
    values = np.array([score_context(r)[1:] for r in prior])
    return {'entropy': np.unique(np.quantile(values[:, 0], [1/3, 2/3])).tolist(),
            'top_gap': np.unique(np.quantile(values[:, 1], [1/3, 2/3])).tolist()}


def design(row, candidate, edges=None):
    gap = np.nan_to_num(row.gap, nan=0.)
    if candidate == 'prior_gap':
        return np.column_stack((gap, np.nan_to_num(row.prior_gap, nan=0.)))
    x, entropy, top_gap = score_context(row)
    if candidate == 'global_temperature':
        return np.column_stack((gap, x))
    if candidate != 'context_temperature' or edges is None:
        raise ValueError('Unknown or unfitted candidate design')
    labels = [0 if len(x) <= 9 else 1 if len(x) <= 13 else 2,
              int(np.digitize(entropy, edges['entropy'])), int(np.digitize(top_gap, edges['top_gap']))]
    context = np.zeros((len(x), 9))
    for axis, value in enumerate(labels):
        context[:, axis * 3 + value] = x
    return np.column_stack((gap, x, context))


def objective(beta, X, rid, n_races, lp, winner, ridge):
    value, gradient = _nll_grad(beta, X, rid, n_races, lp, winner, 0.0)
    return value + .5 * float(np.sum(ridge * beta**2)), gradient + ridge * beta


def fit_context(prior, edges, cfg):
    X = sparse.csr_matrix(np.vstack([design(r, 'context_temperature', edges) for r in prior]))
    rid = np.concatenate([np.full(len(r.p), i, dtype=int) for i, r in enumerate(prior)])
    lp = np.concatenate([np.log(r.p) for r in prior])
    winner = np.concatenate([np.arange(len(r.p)) == r.winner for r in prior])
    ridge = np.array([cfg['fit']['base_ridge']] * 2 + [cfg['fit']['context_lambda'] / len(prior)] * 9)
    args = (X, rid, len(prior), lp, winner, ridge)
    start = np.zeros(11)
    result = minimize(objective, start, args=args, jac=True, method='L-BFGS-B',
        options={'maxiter': cfg['fit']['c_max_iter'], 'ftol': cfg['fit']['c_ftol'], 'gtol': cfg['fit']['c_gtol']})
    val, grad = objective(result.x, *args)
    zero = objective(start, *args)[0]
    delta, norm = float(val-zero), float(np.max(np.abs(grad)))
    if (not result.success or not np.isfinite(result.x).all() or not np.isfinite(delta)
        or delta > cfg['fit']['objective_max'] or not np.isfinite(norm) or norm > cfg['fit']['gradient_inf_max']):
        raise ValueError(f'Context numerical fit blocked: success={result.success}; delta={delta}; gradient={norm}; {result.message}')
    return result.x, {'regularized_fit_objective_delta': delta, 'gradient_inf': norm,
                      'iterations': int(result.nit), 'context_ridge': float(ridge[-1])}


class TemperatureDomainError(ValueError):
    pass


def effective_temperature(row, candidate, beta, edges=None):
    if candidate == 'prior_gap':
        return 1.0
    value = 1.0 + float(beta[1])
    if candidate == 'context_temperature':
        _, entropy, top_gap = score_context(row)
        labels = [0 if len(row.p) <= 9 else 1 if len(row.p) <= 13 else 2,
                  int(np.digitize(entropy, edges['entropy'])), int(np.digitize(top_gap, edges['top_gap']))]
        value += sum(float(beta[2+axis*3+index]) for axis,index in enumerate(labels))
    if not np.isfinite(value) or value <= 0:
        raise TemperatureDomainError(f'Effective temperature exponent must be positive: {value}')
    return value


def corrected_p(row, h, beta):
    if not np.isfinite(beta).all() or not np.isfinite(h).all():
        raise ValueError('Nonfinite residual design/coefficient')
    z = h @ beta
    if not np.isfinite(z).all():
        raise ValueError('Nonfinite offset')
    q = row.p * np.exp(z-z.max())
    if not np.isfinite(q).all() or q.sum() <= 0:
        raise ValueError('Invalid residual probabilities')
    q /= q.sum()
    if (q <= 0).any() or (len(q)>1 and (q>=1).any()) or not np.isclose(q.sum(), 1., atol=1e-12, rtol=0):
        raise ValueError('Residual probability endpoint or sum failure; no clipping repair')
    return q


def run_seed(rows, retained, cfg, seed):
    by_year = {y: [r for r in rows if int(r.day[:4]) == y] for y in YEARS}
    if any(not block for block in by_year.values()):
        raise ValueError('Missing warmup/evaluation year')
    outputs = {c: {'candidate_id': c, 'seed': seed, 'state': 'COMPLETE', 'folds': [], 'rows': []} for c in CANDIDATES}
    prior = list(by_year[2019])
    for year in YEARS[1:]:
        held = by_year[year]
        if max(r.day for r in prior) >= min(r.day for r in held):
            raise ValueError('Coefficient lookahead')
        beta, edges = {}, context_edges(prior)
        for candidate in CANDIDATES:
            if outputs[candidate]['state'] != 'COMPLETE':
                continue
            try:
                if candidate == 'context_temperature':
                    beta[candidate], diagnostics = fit_context(prior, edges, cfg)
                else:
                    fit_rows = [RaceProbe(r.day, r.p, design(r, candidate), r.winner) for r in prior]
                    value = fit_gamma(fit_rows, k=2, ridge=cfg['fit']['base_ridge'], max_iter=cfg['fit']['ab_max_iter'], tol=cfg['fit']['ab_tol'])
                    held_rows = [RaceProbe(r.day, r.p, design(r, candidate), r.winner) for r in held]
                    diagnostics = g.screen.fit_diagnostics([fit_rows, held_rows], [value.tolist()])[0]
                    beta[candidate] = value
                fit_temperatures = [effective_temperature(r, candidate, beta[candidate], edges) for r in prior]
                held_temperatures = [effective_temperature(r, candidate, beta[candidate], edges) for r in held]
                outputs[candidate]['folds'].append({'year': year, 'fit_last_day': max(r.day for r in prior),
                    'eval_first_day': min(r.day for r in held), 'fit_races': len(prior), 'eval_races': len(held),
                    'beta': beta[candidate].tolist(), 'context_edges': edges if candidate=='context_temperature' else None,
                    'diagnostics': diagnostics,
                    'temperature_exponent_fit_range': [min(fit_temperatures),max(fit_temperatures)],
                    'temperature_exponent_held_range': [min(held_temperatures),max(held_temperatures)]})
            except (ValueError, FloatingPointError, OverflowError) as exc:
                outputs[candidate].update(state='BLOCKED_TEMPERATURE_DOMAIN' if isinstance(exc,TemperatureDomainError) else 'BLOCKED_NUMERICAL', reason=str(exc), blocked_year=year)
        for row in held:
            reference = corrected_p(row, np.nan_to_num(row.gap, nan=0.)[:, None], np.array([retained['gammas'][str(year)]]))
            base_loss = _clip_nll(float(reference[row.winner]))
            losses = {}
            for candidate in CANDIDATES:
                if outputs[candidate]['state'] != 'COMPLETE':
                    continue
                try:
                    q = corrected_p(row, design(row, candidate, edges), beta[candidate])
                    losses[candidate] = _clip_nll(float(q[row.winner]))
                    outputs[candidate]['rows'].append({'race_id': row.race_id, 'day': row.day,
                        'candidate_nll': losses[candidate], 'retained_nll': base_loss,
                        'global_temperature_nll': losses.get('global_temperature') if candidate=='context_temperature' else None})
                except (ValueError, FloatingPointError, OverflowError) as exc:
                    outputs[candidate].update(state='BLOCKED_TEMPERATURE_DOMAIN' if isinstance(exc,TemperatureDomainError) else 'BLOCKED_NUMERICAL', reason=str(exc), blocked_year=year)
        prior.extend(held)
    if outputs['global_temperature']['state'] != 'COMPLETE' and outputs['context_temperature']['state']=='COMPLETE':
        outputs['context_temperature'].update(state='BLOCKED_REFERENCE', reason='Global-temperature comparison incomplete')
    for out in outputs.values():
        out.update(can_adopt=False, eligible_for_verdict=False, n_initial_fit_races=len(by_year[2019]),
                   n_evaluated_races=sum(len(by_year[y]) for y in YEARS[1:]))
    return outputs


def interval(rows, contrast, cfg):
    by_day = {}
    key = 'retained_nll' if contrast=='retained' else 'global_temperature_nll'
    for r in rows:
        delta = r['candidate_nll']-r[key]
        if not np.isfinite(delta):
            raise ValueError('Nonfinite screening difference')
        by_day.setdefault(r['day'], []).append(float(delta))
    bs = cfg['bootstrap']
    ci = race_day_cluster_bootstrap_ci_v1(by_day, b=bs['b'], seed=bs['seed'], alpha=bs['alpha'])
    if ci.no_decision:
        raise ValueError('Insufficient screening days')
    return {'point': ci.point, 'ci_low': ci.ci_low, 'ci_high': ci.ci_high, 'n_days': ci.n_days,
            'n_races': len(rows), 'alpha': bs['alpha'], 'b': bs['b'], 'seed': bs['seed'],
            'kind': 'conditional_sample_ci', 'is_total_ci': False}


def summarize(results, cfg):
    if set(results) != set(SEEDS):
        raise ValueError('All three seeds required; no completed-subset average')
    output, common_identity = {}, None
    for candidate in CANDIDATES:
        if any(results[seed][candidate]['state'] != 'COMPLETE' for seed in SEEDS):
            output[candidate] = {'state': 'BLOCKED', 'can_adopt':False, 'eligible_for_verdict':False, 'seed_states': {str(seed): results[seed][candidate]['state'] for seed in SEEDS}}
            continue
        blocks = [results[seed][candidate]['rows'] for seed in SEEDS]
        if any(len(results[seed][candidate]['rows']) != results[seed][candidate]['n_evaluated_races'] for seed in SEEDS):
            raise ValueError('Completed seed evaluation count differs')
        identity = [[(r['race_id'], r['day']) for r in rows] for rows in blocks]
        if any(i != identity[0] for i in identity) or len(set(identity[0])) != len(identity[0]):
            raise ValueError('Seed evaluation populations differ')
        if common_identity is None:
            common_identity = identity[0]
        if identity[0] != common_identity:
            raise ValueError('Candidate evaluation populations differ')
        pooled = []
        for records in zip(*blocks, strict=True):
            r = records[0]
            pooled.append({'race_id': r['race_id'], 'day': r['day'],
                **{key: float(np.mean([record[key] for record in records])) for key in
                   ['candidate_nll', 'retained_nll'] + (['global_temperature_nll'] if candidate=='context_temperature' else [])}})
        contrasts = {contrast: {'mean_seed_loss_difference': interval(pooled, contrast, cfg),
            'per_seed': {str(seed): interval(results[seed][candidate]['rows'], contrast, cfg) for seed in SEEDS}} for contrast in CONTRASTS[candidate]}
        advance = all(value['mean_seed_loss_difference']['point'] < 0 for value in contrasts.values())
        output[candidate] = {'state': 'ADVANCE_TO_FULL_QUALITY' if advance else 'NO_OBSERVED_MEAN_IMPROVEMENT',
                             'contrasts': contrasts, 'can_adopt': False, 'eligible_for_verdict': False}
    return output


def verified_output(output, receipt, cfg):
    expected = {'report_sha256':s.p.digest(output),'freeze_sha256':s.p.digest(WORK/'run-freeze.json')}
    if s.read_json(receipt) != expected:
        raise ValueError('Existing121 result receipt changed')
    report = s.read_json(output)
    if (report.get('artifact_kind') != 'extra_residual_probe_report'
        or report.get('can_adopt') is not False or report.get('eligible_for_verdict') is not False
        or report.get('study_config_hash') != s.p.gate_config_hash(cfg)
        or report.get('run_freeze_sha256') != expected['freeze_sha256']
        or set(report.get('seed_results',{})) != {str(seed) for seed in SEEDS}
        or set(report.get('summary',{})) != set(CANDIDATES)):
        raise ValueError('Existing121 report scope changed')
    if report['summary'] != summarize({int(seed): value for seed,value in report['seed_results'].items()},cfg):
        raise ValueError('Existing121 summary differs from saved seed rows')
    return report


def run():
    cfg, frozen = verify()
    output, receipt = SPEC / 'evidence/residual-probes.json', WORK / 'result-receipt.json'
    if output.exists():
        verified_output(output,receipt,cfg)
        print('RUN existing result verified; no rerun', flush=True)
        return
    if receipt.exists():
        raise ValueError('Orphan result receipt')
    results = {}
    started = time.monotonic()
    for seed in SEEDS:
        with prepared_path(seed).open('rb') as f:
            rows = [Row(**record) for record in pickle.load(f)]
        print(f'PROBE seed={seed}: fixed A/B/C joint fits', flush=True)
        results[seed] = run_seed(rows, frozen['sources']['retained_coefficients'][str(seed)], cfg, seed)
        del rows
        gc.collect()
    summary = summarize(results, cfg)
    verify()
    report = {'artifact_kind': 'extra_residual_probe_report', 'can_adopt': False, 'eligible_for_verdict': False,
        'study_config_hash': s.p.gate_config_hash(cfg), 'run_freeze_sha256': s.p.digest(WORK/'run-freeze.json'),
        'summary': summary, 'seed_results': {str(seed): r for seed,r in results.items()},
        'limitations': cfg['limitations'], 'elapsed_seconds': time.monotonic()-started}
    write_json(output, report)
    write_json(receipt, {'report_sha256': s.p.digest(output), 'freeze_sha256': s.p.digest(WORK/'run-freeze.json')})
    print({k:v['state'] for k,v in summary.items()}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    args = parser.parse_args()
    {'prepare': prepare, 'run': run}[args.action]()
