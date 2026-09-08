"""117 historical, descriptive attribution of the fixed 116 pruning contrast."""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import math
from pathlib import Path
import statistics
import unicodedata

import numpy as np
import pandas as pd

import gap_seed_recheck as d
import gap_seed_summary as summary

SPEC = d.ROOT / 'specs/117-pruning-2026-diagnostic'
WORK = d.ROOT / 'artifacts/117-pruning-2026-diagnostic'
SEEDS = (42, 43, 44)
METRICS = ('pruning', 'correction', 'total')
GROUPS = ('month', 'venue', 'track', 'distance', 'field_size', 'race_class', 'going',
          'nk_share', 'time_coverage', 'relative_coverage', 'debut_share', 'month_track')
CONFIG = {'artifact_kind': 'posthoc_diagnostic_config', 'can_adopt': False,
          'eligible_for_verdict': False, 'seeds': list(SEEDS), 'year': 2026,
          'window': ['2020-01-01', '2026-08-23'], 'same_season_end': '08-23',
          'reference_years': [[2024, 2025], list(range(2020, 2026))],
          'groups': list(GROUPS), 'distance_bounds': [1400, 1800, 2200],
          'field_size_bounds': [8, 12, 16], 'share_bounds': [0, .5, 1],
          'sensitivity_excluded_days': [1, 3, 5], 'distribution_tails': [.01, .99],
          'new_fits': 0, 'new_ci': False, 'new_selection': False}


def same(a, b):
    if not math.isclose(float(a), float(b), abs_tol=1e-12, rel_tol=0):
        raise ValueError('Paired arithmetic mismatch')


def share_band(x):
    if pd.isna(x):
        return 'unknown'
    if not 0 <= x <= 1:
        raise ValueError('Invalid fraction')
    return '0' if x == 0 else '(0,.5]' if x <= .5 else '(.5,1)' if x < 1 else '1'


def numeric_band(x, bounds, labels):
    if pd.isna(x):
        return 'unknown'
    return labels[int(np.searchsorted(bounds, x, side='right'))]


def class_name(x):
    if pd.isna(x):
        return 'unknown'
    name = unicodedata.normalize('NFKC', str(x)).strip()
    return {'500万': '1勝', '1000万': '2勝', '1600万': '3勝'}.get(name, name)


def load_evidence(cfg, frozen):
    reports, evidence = {}, {}
    for seed in SEEDS:
        reports[seed], evidence[seed] = {}, {}
        for c in d.CONTRASTS:
            if not d.verified_result(seed, c, cfg, frozen):
                raise ValueError('Missing verified source report')
            report = d.s.read_json(d.result_path(seed, c['id']))
            reports[seed][c['id']] = report
            evidence[seed][c['id']] = d.s.read_json(report['evidence_path'])
    summary.validate_population(reports, evidence)
    return reports, evidence


def paired_rows(evidence):
    if set(evidence) != set(SEEDS):
        raise ValueError('All three seeds required')
    rows = None
    for seed in SEEDS:
        a, i = (evidence[seed][k]['rows'] for k in ('anchor', 'increment'))
        if len(a) != len(i) or len(a) == 0:
            raise ValueError('Both nonempty contrasts required')
        identity = [(r['race_id'], r['race_day']) for r in a]
        if len(set(x[0] for x in identity)) != len(a):
            raise ValueError('Duplicate race')
        if rows is None:
            rows = pd.DataFrame(identity, columns=['race_id', 'race_day'])
        elif identity != list(zip(rows.race_id, rows.race_day)):
            raise ValueError('Seed populations differ')
        values = {k: [] for k in METRICS}
        for ar, ir in zip(a, i, strict=True):
            if (ar['race_id'], ar['race_day']) != (ir['race_id'], ir['race_day']):
                raise ValueError('Contrast populations differ')
            same(ar['candidate_winner_nll'], ir['candidate_winner_nll'])
            for r in (ar, ir):
                same(r['diff'], r['candidate_winner_nll'] - r['active_winner_nll'])
            pruning = ir['active_winner_nll'] - ar['active_winner_nll']
            same(pruning, ar['diff'] - ir['diff'])
            for k, value in zip(METRICS, (pruning, ir['diff'], ar['diff'])):
                if not np.isfinite(value):
                    raise ValueError('Nonfinite source')
                values[k].append(value)
        for name, values in values.items():
            rows[f'{name}_{seed}'] = values
    for name in METRICS:
        rows[name] = rows[[f'{name}_{s}' for s in SEEDS]].mean(axis=1)
    rows['year'] = rows.race_day.str[:4].astype(int)
    rows['same_season'] = rows.race_day.str[5:] <= CONFIG['same_season_end']
    return rows


def record(frame, denominator=None):
    n = len(frame)
    denominator = n if denominator is None else denominator
    return {'n_races': n, 'n_days': int(frame.race_day.nunique()),
            'share': n / denominator if denominator else None,
            'metrics': {k: {'by_seed': {str(s): float(frame[f'{k}_{s}'].mean()) if n else None for s in SEEDS},
                            'mean': float(frame[k].mean()) if n else None,
                            'contribution': float(frame[k].sum() / denominator) if denominator else None}
                        for k in METRICS}}


def split_records(frame, col):
    out = {str(name): record(g, len(frame)) for name, g in frame.groupby(col, sort=True, observed=True)}
    for k in METRICS:
        same(sum(r['metrics'][k]['contribution'] for r in out.values()), frame[k].mean())
    if sum(v['n_races'] for v in out.values()) != len(frame):
        raise ValueError('Grouping dropped unknown rows')
    return out


def composition(reference, target, column):
    """Exact symmetric descriptive decomposition, keeping unmatched-cell mass."""
    a, b = split_records(reference, column), split_records(target, column)
    composition_value = within_value = unmatched = 0.0
    cells = []
    for key in sorted(set(a) | set(b)):
        x, y = a.get(key), b.get(key)
        p, q = (x['share'] if x else 0), (y['share'] if y else 0)
        m, n = (x['metrics']['pruning']['mean'] if x else None), (y['metrics']['pruning']['mean'] if y else None)
        if x and y:
            c = (q - p) * (n + m) / 2
            w = (n - m) * (q + p) / 2
            composition_value += c
            within_value += w
            u = 0.0
        else:
            c = w = 0.0
            u = q * n if y else -p * m
            unmatched += u
        cells.append({'cell': key, 'reference_share': p, 'target_share': q,
                      'reference_mean': m, 'target_mean': n,
                      'composition': c, 'within': w, 'unmatched': u})
    delta = float(target.pruning.mean() - reference.pruning.mean())
    same(composition_value + within_value + unmatched, delta)
    return {'delta': delta, 'composition': composition_value, 'within': within_value,
            'unmatched': unmatched, 'target_unmatched_share': sum(x['target_share'] for x in cells if x['reference_mean'] is None),
            'reference_unmatched_share': sum(x['reference_share'] for x in cells if x['target_mean'] is None), 'cells': cells}


def sensitivity(frame):
    day_sum = frame.groupby('race_day', sort=True).pruning.sum()
    worst = day_sum.sort_values(ascending=False, kind='stable')
    leave = [(day, record(frame[frame.race_day != day])) for day in day_sum.index]
    return {'days': {day: record(g, len(frame)) for day, g in frame.groupby('race_day', sort=True)},
            'leave_one_day_out': {'min_mean': min(r['metrics']['pruning']['mean'] for _, r in leave),
                                  'max_mean': max(r['metrics']['pruning']['mean'] for _, r in leave)},
            'remove_worst_days_posthoc': {str(k): {'excluded_days': list(worst.index[:k]),
                'removed_pruning_contribution': float(worst.iloc[:k].sum() / len(frame)),
                'remaining': record(frame[~frame.race_day.isin(worst.index[:k])])}
                for k in CONFIG['sensitivity_excluded_days'] if k < len(day_sum)}}


def source_state():
    cfg, frozen = d.verify()
    if (d.WORK / 'running.lock').exists() or not all(d.completed(j) for j in frozen['jobs']):
        raise ValueError('Completed source study required')
    reports, evidence = load_evidence(cfg, frozen)
    stored = d.s.read_json(d.SPEC / 'verdict.json')
    for key, value in summary.summarize_reports(reports).items():
        if stored[key] != value:
            raise ValueError('Source summary mismatch')
    paths = [d.WORK / 'run-freeze.json', d.SPEC / 'verdict.json']
    for seed in SEEDS:
        for c in d.CONTRASTS:
            paths += [d.result_path(seed, c['id']), Path(reports[seed][c['id']]['evidence_path'])]
    return {'files': {str(p): d.s.p.digest(p) for p in paths},
            'snapshot_sha256': frozen['sources']['snapshot_sha256'],
            'source116_hash': frozen['source_hash'], 'runtime': d.s.runtime()}


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify()
        return
    d.write_json(SPEC / 'diagnostic-config.json', CONFIG)
    state = source_state()
    d.write_json(WORK / 'run-freeze.json', {'source': state, 'method_sha256': d.s.p.digest(__file__),
        'config_sha256': d.s.p.digest(SPEC / 'diagnostic-config.json'),
        'prepared_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'can_adopt': False, 'eligible_for_verdict': False})
    print('PREPARE PASS', flush=True)


def verify():
    frozen = d.s.read_json(WORK / 'run-freeze.json')
    if (d.s.read_json(SPEC / 'diagnostic-config.json') != CONFIG
        or frozen['method_sha256'] != d.s.p.digest(__file__)
        or frozen['config_sha256'] != d.s.p.digest(SPEC / 'diagnostic-config.json')
        or frozen['source'] != source_state()):
        raise ValueError('Frozen117 source or method changed')
    return frozen


def attach_attributes(rows, frame, relative, expected_fields=None):
    f = frame[frame.race_id.isin(set(rows.race_id))].copy()
    if f.duplicated(['race_id', 'horse_id']).any():
        raise ValueError('Duplicate snapshot key')
    race_fields = ['venue_code', 'track_type', 'distance', 'field_size', 'race_class', 'going', 'race_date']
    grouped = f.groupby('race_id', sort=False, observed=True)
    if expected_fields is not None:
        actual = grouped.horse_id.agg(set).to_dict()
        if actual != expected_fields:
            raise ValueError('Started horse ID population mismatch')
    if (grouped[race_fields].nunique(dropna=False) > 1).any().any():
        raise ValueError('Nonconstant race attributes')
    first = grouped[race_fields].first()
    distances = first.distance.dropna().to_numpy(dtype=float)
    sizes = first.field_size.to_numpy(dtype=float)
    if (not np.isfinite(distances).all() or (distances <= 0).any()
        or not np.isfinite(sizes).all() or (sizes < 1).any() or (sizes != np.floor(sizes)).any()):
        raise ValueError('Invalid distance or field size')
    if not (first.field_size == grouped.size()).all():
        raise ValueError('Started field size mismatch')
    if np.isinf(f[relative + ['rel_time_avg']].to_numpy(dtype=float)).any():
        raise ValueError('Infinite feature')
    if not f.is_debut.isin([0, 1]).all():
        raise ValueError('Unknown or invalid debut flag')
    f['nk'] = f.horse_id.str.startswith('nk:').astype(float)
    f['time_observed'] = f.rel_time_avg.notna().astype(float)
    f['relative_observed'] = f[relative].notna().mean(axis=1)
    fractions = f.groupby('race_id', observed=True)[['nk', 'time_observed', 'relative_observed', 'is_debut']].mean()
    attrs = pd.DataFrame(index=first.index)
    for dest, src in [('venue', 'venue_code'), ('track', 'track_type'), ('going', 'going')]:
        attrs[dest] = first[src].astype('object').fillna('unknown').astype(str)
    attrs['race_class'] = first.race_class.astype(object).map(class_name)
    attrs['distance'] = first.distance.map(lambda x: numeric_band(x, [1400, 1800, 2200], ['<1400', '1400-1799', '1800-2199', '2200+']))
    attrs['field_size'] = first.field_size.map(lambda x: numeric_band(x, [8, 12, 16], ['<=7', '8-11', '12-15', '16+']))
    for dest, src in [('nk_share', 'nk'), ('time_coverage', 'time_observed'), ('relative_coverage', 'relative_observed'), ('debut_share', 'is_debut')]:
        attrs[dest] = fractions[src].map(share_band)
    attrs['month'] = first.race_date.map(lambda x: str(x.month).zfill(2))
    attrs['month_track'] = attrs.month + ':' + attrs.track
    attrs['snapshot_day'] = first.race_date.map(str)
    out = rows.merge(attrs, left_on='race_id', right_index=True, how='left', validate='one_to_one', sort=False)
    if len(out) != len(rows) or out[list(GROUPS)].isna().any().any() or not (out.race_day == out.snapshot_day).all():
        raise ValueError('Snapshot race/date join mismatch')
    return out.drop(columns='snapshot_day'), f


def distribution(frame, columns):
    output = {}
    for c in columns:
        values = frame[c].to_numpy(dtype=float)
        if np.isinf(values).any():
            raise ValueError('Infinite feature')
        v = values[np.isfinite(values)]
        sd = frame.groupby('race_id', observed=True)[c].std(ddof=0).dropna().to_numpy()
        output[c] = {'n_horses': len(values), 'missing_rate': float(np.isnan(values).mean()),
            'p10_p50_p90': np.quantile(v, [.1, .5, .9]).tolist() if len(v) else None,
            'race_sd_p50_p90': np.quantile(sd, [.5, .9]).tolist() if len(sd) else None}
    return output


def run():
    frozen = verify()
    cfg, f116 = d.verify()
    _, evidence = load_evidence(cfg, f116)
    rows = paired_rows(evidence)
    matrix, races = d.s.reuse.load(d.s.p.WORK / 'snapshot.pkl')
    relative = next(c['drop_features'] for c in d.s.old.load_config()['candidates'] if c['id'] == 'relative_ability')
    absolute = sorted(set(c.removesuffix('_vs_field') for c in relative if c.endswith('_vs_field')))
    cols = ['race_id', 'horse_id', 'race_date', 'venue_code', 'track_type', 'distance', 'field_size', 'race_class', 'going', 'is_debut'] + sorted(set(relative + absolute))
    ids = set(rows.race_id)
    expected_fields = {r.context.race_id: {h.horse_id for h in r.context.started_horses}
                       for r in races if r.context.race_id in ids}
    rows, horses = attach_attributes(rows, matrix.frame[cols], relative, expected_fields)
    del matrix, races
    gc.collect()
    target = rows[rows.year == 2026]
    references = {'2024-2025': rows[rows.year.isin([2024, 2025]) & rows.same_season],
                  '2020-2025': rows[(rows.year < 2026) & rows.same_season]}
    annual = {str(y): {'all': record(g), 'same_season': record(g[g.same_season])} for y, g in rows.groupby('year')}
    partitions = {'2026': {col: split_records(target, col) for col in GROUPS}}
    for name, ref in references.items():
        partitions[name] = {col: split_records(ref, col) for col in GROUPS}
    decompositions = {name: {col: composition(ref, target, col) for col in GROUPS} for name, ref in references.items()}
    distributions = {}
    for y in range(2020, 2027):
        ids = set(rows.loc[(rows.year == y) & rows.same_season, 'race_id'])
        distributions[str(y)] = distribution(horses[horses.race_id.isin(ids)], relative + absolute)
    ref_h = horses[horses.race_id.isin(set(references['2024-2025'].race_id))]
    cur_h = horses[horses.race_id.isin(set(target.race_id))]
    tails = {}
    for c in relative + absolute:
        rv, cv = ref_h[c].dropna().to_numpy(dtype=float), cur_h[c].dropna().to_numpy(dtype=float)
        if len(rv) and len(cv):
            lo, hi = np.quantile(rv, [.01, .99])
            tails[c] = {'reference_p1_p99': [float(lo), float(hi)], 'target_below_p1': float((cv < lo).mean()),
                        'target_above_p99': float((cv > hi).mean()), 'denominator': 'nonmissing_horses'}
        else:
            tails[c] = {'unavailable': True}
    result = {'artifact_kind': 'posthoc_pruning_diagnostic', 'can_adopt': False, 'eligible_for_verdict': False,
        'n_eligible': len(rows), 'n_days': int(rows.race_day.nunique()), 'seeds': list(SEEDS),
        'annual': annual, 'partitions': partitions, 'composition_comparisons': decompositions,
        'day_sensitivity_2026': sensitivity(target), 'feature_distribution_same_season': distributions,
        'feature_tails_vs_2024_2025': tails, 'method_sha256': frozen['method_sha256'],
        'run_freeze_sha256': d.s.p.digest(WORK / 'run-freeze.json'), 'new_fits': 0,
        'limitations': ['Posthoc historical diagnostic; groups are not causal feature attribution.',
            'Means average seed-specific losses, not probabilities; same races are not independent seed observations.',
            'No new CI/p-value/production decision; selected-day exclusions are sensitivity only.',
            'Feature distributions use eligible-race started horses; loss metrics are race weighted.',
            'Composition/within decomposition is descriptive and may reflect changing models as well as data.',
            'Only month-track is crossed; overlapping partition contributions must not be added.']}
    verify()
    d.write_json(WORK / 'race-diagnostic.json', {'rows': rows.to_dict(orient='records'), 'run_freeze_sha256': result['run_freeze_sha256']})
    result['race_rows_sha256'] = d.s.p.digest(WORK / 'race-diagnostic.json')
    d.write_json(SPEC / 'evidence/diagnostic.json', result)
    print('RUN COMPLETE descriptive117; no fits, no adoption', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    {'prepare': prepare, 'run': run}[parser.parse_args().action]()
