"""114: frozen historical residual screen; never a production adoption artifact."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import gc
import json
from pathlib import Path
import pickle

import numpy as np
import pandas as pd
import small_gain_stack as stack
from horseracing_eval import bootstrap, dataset, residual_probe as probe

ROOT = stack.ROOT
SPEC = ROOT / 'specs/114-gap-season-recheck'
WORK = ROOT / 'artifacts/114-gap-season-recheck'
CANDIDATES = {'current_gap_shape': [0, 1, 2], 'gap_log': [0], 'seasonal_sex': [3, 4]}
COLUMNS = ['race_id', 'horse_id', 'race_date', 'days_since_last', 'sex']


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def load_config():
    cfg = read_json(SPEC / 'gate-config.json')
    if stack.p.gate_config_hash(cfg) != (SPEC / 'gate-config.hash.txt').read_text().strip():
        raise ValueError('Frozen 114 configuration changed')
    if (cfg['candidate_ids'] != list(CANDIDATES)
        or cfg['warmup_year'] != 2019 or cfg['eval_window'] != {'from': '2020-01-01', 'to': '2026-08-23'}
        or cfg['bootstrap'] != {'b': 4000, 'seed': 20260907, 'alpha': .0125, 'block': 'race_day'}
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('Registered screening scope changed')
    return cfg


def source_hash():
    return stack.p.stable_hash({str(Path(p)): stack.p.digest(p) for p in (
        __file__, stack.__file__, probe.__file__, bootstrap.__file__, dataset.__file__,
        ROOT / 'training/src/horseracing_training/folklore_candidates.sql')})


def candidate_matrix(frame):
    """Same gap function shapes as 081, using the current snapshot history scope."""
    if frame[COLUMNS[:3]].isna().any().any() or frame.duplicated(['race_id', 'horse_id']).any():
        raise ValueError('Missing/duplicate input identity')
    gap = frame['days_since_last'].to_numpy(dtype=float)
    if np.isinf(gap).any() or (gap[np.isfinite(gap)] <= 0).any():
        raise ValueError('Observed gap must be a positive strict-past day count')
    if (gap[np.isfinite(gap)] % 1 != 0).any():
        raise ValueError('Gap day count must be integral')
    sex = frame['sex'].astype(object)
    if not set(sex.dropna().unique()).issubset({'牡', '牝', 'セ'}):
        raise ValueError('Unknown sex encoding')
    dates = pd.to_datetime(frame['race_date'], errors='raise')
    fraction = (dates.dt.dayofyear.to_numpy() - 1) / np.where(dates.dt.is_leap_year, 366., 365.)
    female = np.where(sex.isna(), np.nan, (sex == '牝').to_numpy(dtype=float))
    theta = 2 * np.pi * fraction
    return np.column_stack((np.log1p(gap), np.maximum(0., 14. - gap),
                            np.maximum(0., gap - 70.), female * np.sin(theta), female * np.cos(theta)))


def validate_probabilities(pred, horse_ids):
    if set(pred) != set(horse_ids) or len(horse_ids) != len(set(horse_ids)):
        raise ValueError('Cached started-horse population mismatch')
    p = np.asarray([pred[h].win for h in horse_ids], dtype=float)
    if not np.isfinite(p).all() or (p <= 0).any() or (p > 1).any() or not np.isclose(p.sum(), 1., atol=1e-8, rtol=0):
        raise ValueError('Invalid cached win probabilities')
    return p


def load_inputs(cfg, frozen113):
    """Discard the large matrix before opening any cache; never fit a booster."""
    with (stack.p.WORK / 'snapshot.pkl').open('rb') as f:
        matrix, all_races = pickle.load(f)
    start, end = dt.date(2019, 1, 1), dt.date.fromisoformat(cfg['eval_window']['to'])
    races = [r for r in all_races if start <= r.context.race_date <= end]
    ids = {r.context.race_id for r in races}
    frame = matrix.frame.loc[matrix.frame['race_id'].isin(ids), COLUMNS].copy()
    del matrix, all_races
    gc.collect()
    h = candidate_matrix(frame)
    rows = {(r, horse): i for i, (r, horse) in enumerate(zip(frame.race_id, frame.horse_id, strict=True))}
    if set(frame.race_id) != ids:
        raise ValueError('Snapshot race population mismatch')
    audit = {'input_rows': len(frame), 'input_races': len(races),
             'observed_sex_values': sorted(str(s) for s in frame.sex.dropna().unique()),
             'gap_missing_rows': int(frame.days_since_last.isna().sum()),
             'sex_missing_rows': int(frame.sex.isna().sum()),
             'gap_history_scope': cfg['gap_history_scope'], 'years': {}}
    horses_by_race = frame.groupby('race_id', sort=False)['horse_id'].agg(set).to_dict()
    dates = dict(zip(zip(frame.race_id, frame.horse_id, strict=True), frame.race_date, strict=True))
    del frame
    records = [r for r in frozen113['source_caches'] if r['arm'] == 'anchor']
    if sorted(r['year'] for r in records) != list(range(2019, 2027)):
        raise ValueError('Expected exactly eight source anchor caches')
    old_cfg = stack.old.load_config()
    expected_recipe = stack.old.CalibSplitFactory(None, stack.old.make_recipe(old_cfg, (), False),
        n_oof_blocks=old_cfg['arms']['n_oof_blocks'], method='isotonic', require_sufficient=True).recipe_meta
    expected_columns = stack.p.columns_from_model()
    folds = []
    for record in sorted(records, key=lambda r: r['year']):
        stack.validate_receipt(record)
        year_races = [r for r in races if r.context.race_date.year == record['year']]
        with Path(record['path']).open('rb') as f:
            cache = pickle.load(f)
        if (cache['key'] != record['key'] or cache['train_hash'] != record['train_hash']
            or not cache['oof_info']['sufficient'] or cache['feature_columns'] != expected_columns
            or cache['recipe_meta'] != expected_recipe
            or set(cache['predictions']) != {r.context.race_id for r in year_races}):
            raise ValueError('Anchor cache identity/population mismatch')
        fold, excluded = [], 0
        for race in year_races:
            if race.n_result_rows is None:
                raise ValueError('Unknown result-row coverage')
            pop = dataset.population_masks(race)
            horse_ids = pop.started_horse_ids
            p = validate_probabilities(cache['predictions'][race.context.race_id], horse_ids)
            keys = [(race.context.race_id, horse) for horse in horse_ids]
            if horses_by_race[race.context.race_id] != set(horse_ids):
                raise ValueError('Snapshot started-horse population mismatch')
            if any(dates[k] != race.context.race_date for k in keys):
                raise ValueError('Snapshot race date mismatch')
            if not pop.eligible:
                excluded += 1
                continue
            indices = [rows[k] for k in keys]
            fold.append(probe.RaceProbe(str(race.context.race_date), p, h[indices], horse_ids.index(pop.winner_horse_id)))
        audit['years'][str(record['year'])] = {'all_races': len(year_races), 'eligible_races': len(fold), 'excluded_races': excluded}
        if not fold:
            raise ValueError('Empty chronological fold')
        folds.append(fold)
        del cache
    audit['initial_fit_races'] = len(folds[0])
    audit['evaluated_races'] = sum(map(len, folds[1:]))
    audit['evaluated_days'] = len({r.day for f in folds[1:] for r in f})
    return folds, audit


def input_identity(frozen113):
    return {'113_freeze_sha256': stack.p.digest(stack.WORK / 'run-freeze.json'),
            'snapshot_sha256': stack.p.digest(stack.p.WORK / 'snapshot.pkl'),
            'anchor_sources': [r for r in frozen113['source_caches'] if r['arm'] == 'anchor']}


def verify():
    cfg = load_config()
    _, frozen113 = stack.verify()
    frozen = read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != stack.p.gate_config_hash(cfg)
        or frozen['runtime'] != stack.runtime() or frozen['inputs'] != input_identity(frozen113)):
        raise ValueError('114 source/config/runtime/input changed after freeze')
    return cfg, frozen, frozen113


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify()
        print('PREPARE verified existing freeze; no rewrite', flush=True)
        return
    cfg = load_config()
    before = source_hash()
    _, frozen113 = stack.verify()
    inputs_before = input_identity(frozen113)
    folds, audit = load_inputs(cfg, frozen113)
    del folds
    if before != source_hash() or inputs_before != input_identity(frozen113):
        raise ValueError('Source/input changed during preparation')
    write_json(WORK / 'run-freeze.json', {'artifact_kind': 'gap_season_recheck_freeze',
        'source_hash': before, 'config_hash': stack.p.gate_config_hash(cfg), 'runtime': stack.runtime(),
        'inputs': inputs_before, 'audit': audit, 'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'can_adopt': False, 'eligible_for_verdict': False})
    print(json.dumps(audit, ensure_ascii=False), flush=True)


def fit_diagnostics(folds, gammas):
    """Reject numerical failures; never retune the frozen Newton fit after outcomes."""
    diagnostics, prior = [], list(folds[0])
    for fold, values in zip(folds[1:], gammas, strict=True):
        gamma = np.asarray(values, dtype=float)
        if not np.isfinite(gamma).all():
            raise ValueError('Nonfinite fitted gamma')
        losses, grad = [], np.zeros(len(gamma))
        for r in prior:
            p, h = probe._clean(r)
            losses.append(probe._delta_nll_race(p, h, r.winner_idx, gamma))
            z = h @ gamma
            w = p * np.exp(z - z.max())
            grad += (w / w.sum()) @ h - h[r.winner_idx]
        objective = float(np.mean(losses) + .5e-6 * (gamma @ gamma))
        norm = float(np.max(np.abs(grad / len(prior) + 1e-6 * gamma)))
        if not np.isfinite(objective) or objective > 1e-8 or not np.isfinite(norm) or norm > 1e-5:
            raise ValueError(f'Newton convergence check failed: objective={objective}, gradient_inf={norm}')
        diagnostics.append({'eval_year': int(fold[0].day[:4]), 'fit_races': len(prior),
                            'fit_last_day': max(r.day for r in prior), 'eval_first_day': min(r.day for r in fold),
                            'regularized_fit_objective': objective, 'gradient_inf': norm})
        if diagnostics[-1]['fit_last_day'] >= diagnostics[-1]['eval_first_day']:
            raise ValueError('Prequential chronology violation')
        prior.extend(fold)
    return diagnostics


def assess_candidate(folds, candidate_id, cfg):
    selected = [[probe.RaceProbe(r.day, r.p, r.h[:, CANDIDATES[candidate_id]], r.winner_idx) for r in f] for f in folds]
    k = len(CANDIDATES[candidate_id])
    if any(not f for f in selected) or any(max(r.day for r in a) >= min(r.day for r in b) for a, b in zip(selected, selected[1:])):
        raise ValueError('Prequential folds must be nonempty and strictly chronological')
    common = {'candidate_id': candidate_id, 'can_adopt': False, 'eligible_for_verdict': False,
              'n_initial_fit_races': len(selected[0]), 'n_evaluated_races': sum(map(len, selected[1:])),
              'quality_guards': 'NOT_MEASURED: top2/top3/ECE/recent/subgroup',
              'evidence_regime': 'historical_development_full_information'}
    try:
        result = probe.prequential_delta_nll(selected, candidate_id, k)
        diagnostics = fit_diagnostics(selected, result.gammas_by_fold)
        if not np.isfinite(result.point_delta_nll) or any(not np.isfinite(v) for ds in result.delta_nll_by_day.values() for v in ds):
            raise ValueError('Nonfinite prequential held-out loss')
        bs = cfg['bootstrap']
        ci = bootstrap.race_day_cluster_bootstrap_ci_v1(result.delta_nll_by_day, b=bs['b'], seed=bs['seed'], alpha=bs['alpha'])
        if ci.no_decision or sum(map(len, result.delta_nll_by_day.values())) != common['n_evaluated_races']:
            raise ValueError('Missing held-out evaluation population')
        return {**common, 'state': 'POINT_IMPROVEMENT' if result.point_delta_nll < 0 else 'NO_OBSERVED_IMPROVEMENT',
                'point_delta_winner_nll': result.point_delta_nll,
                'sample_ci': {**asdict(ci), 'alpha': bs['alpha'], 'is_total_ci': False, 'accounts_for_seed_noise': False},
                'gammas_by_fold': result.gammas_by_fold, 'fit_diagnostics': diagnostics,
                'evaluated_by_year': {str(y): {'n_races': sum(len(v) for d, v in result.delta_nll_by_day.items() if d.startswith(str(y))),
                    'point_delta_winner_nll': float(np.mean([x for d, ds in result.delta_nll_by_day.items() if d.startswith(str(y)) for x in ds]))} for y in range(2020, 2027)},
                'warmup_inclusive_descriptive': {'n_races': result.n_races, 'coverage': result.coverage, 'mean_score_U': result.mean_score_U},
                'delta_nll_by_day': result.delta_nll_by_day}
    except (ValueError, FloatingPointError, OverflowError) as exc:
        return {**common, 'state': 'BLOCKED_NUMERICAL', 'reason': str(exc)}


def run():
    cfg, frozen, frozen113 = verify()
    output = SPEC / 'evidence' / 'residual-screen.json'
    receipt = WORK / 'result-receipt.json'
    if output.exists():
        stored = read_json(receipt)
        if stored != {'output_sha256': stack.p.digest(output), 'freeze_sha256': stack.p.digest(WORK / 'run-freeze.json')}:
            raise ValueError('Existing result receipt mismatch; preserve for diagnosis')
        print('RUN verified existing evidence; no rerun or rewrite', flush=True)
        return
    if receipt.exists():
        raise ValueError('Orphan result receipt; preserve for diagnosis')
    folds, audit = load_inputs(cfg, frozen113)
    if audit != frozen['audit']:
        raise ValueError('Input population changed since preparation')
    results = []
    for candidate_id in CANDIDATES:
        print(f'PROBE {candidate_id}: prior-year gamma only', flush=True)
        results.append(assess_candidate(folds, candidate_id, cfg))
    verify()
    report = {'artifact_kind': 'gap_season_residual_screen', 'eligible_for_verdict': False, 'can_adopt': False,
              'config_hash': stack.p.gate_config_hash(cfg), 'run_freeze_sha256': stack.p.digest(WORK / 'run-freeze.json'),
              'audit': audit, 'candidates': results, 'gap_history_scope': cfg['gap_history_scope'],
              'limitations': cfg['limitations']}
    write_json(output, report)
    write_json(receipt, {'output_sha256': stack.p.digest(output), 'freeze_sha256': stack.p.digest(WORK / 'run-freeze.json')})
    print(json.dumps([{'candidate': r['candidate_id'], 'state': r['state'], 'delta': r.get('point_delta_winner_nll')} for r in results]), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    args = parser.parse_args()
    {'prepare': prepare, 'run': run}[args.action]()
