"""Independent read-only reproduction of 117 from saved losses and the frozen snapshot."""
from __future__ import annotations

from collections import defaultdict
import gc
import math
from pathlib import Path
import sys
import unicodedata

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import pruning_2026_diagnostic as source

D = source.d
SEEDS = (42, 43, 44)
METRICS = ('pruning', 'correction', 'total')
GROUPS = ('month', 'venue', 'track', 'distance', 'field_size', 'race_class', 'going',
          'nk_share', 'time_coverage', 'relative_coverage', 'debut_share', 'month_track')
CHECKS = 0
MAX_ERROR = 0.0


def equal(a, b):
    global CHECKS, MAX_ERROR
    CHECKS += 1
    if isinstance(a, dict):
        assert set(a) == set(b), (set(a), set(b))
        for k in a:
            equal(a[k], b[k])
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        for x, y in zip(a, b, strict=True):
            equal(x, y)
    elif isinstance(a, (float, np.floating)):
        assert math.isfinite(a) and math.isfinite(b)
        error = abs(a - b)
        MAX_ERROR = max(MAX_ERROR, error)
        assert error <= 1e-12, (a, b, error)
    else:
        assert a == b, (a, b)


def average(values):
    return math.fsum(values) / len(values)


def summary(rows, denominator=None):
    n = len(rows)
    denominator = n if denominator is None else denominator
    return {'n_races': n, 'n_days': len({r['race_day'] for r in rows}),
            'share': n / denominator if denominator else None,
            'metrics': {m: {
                'by_seed': {str(s): average([r[f'{m}_{s}'] for r in rows]) if n else None for s in SEEDS},
                'mean': average([r[m] for r in rows]) if n else None,
                'contribution': math.fsum(r[m] for r in rows) / denominator if denominator else None}
                for m in METRICS}}


def groups(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return dict(result)


def fraction(x):
    assert 0 <= x <= 1
    if x == 0:
        return '0'
    if x <= .5:
        return '(0,.5]'
    return '1' if x == 1 else '(.5,1)'


def text_value(x):
    return 'unknown' if pd.isna(x) else str(x)


def independent_attributes(frame, expected):
    result = {}
    for rid, f in frame.groupby('race_id', sort=False, observed=True):
        assert not f.horse_id.duplicated().any()
        assert set(f.horse_id) == expected[rid]
        a = f.iloc[0]
        for c in ('venue_code', 'track_type', 'distance', 'field_size', 'race_class', 'going', 'race_date'):
            assert f[c].nunique(dropna=False) == 1
        assert int(a.field_size) == len(f)
        distance = a.distance
        assert pd.isna(distance) or (math.isfinite(distance) and distance > 0)
        dist = ('unknown' if pd.isna(distance) else '<1400' if distance < 1400 else
                '1400-1799' if distance < 1800 else '1800-2199' if distance < 2200 else '2200+')
        size = '<=7' if len(f) <= 7 else '8-11' if len(f) <= 11 else '12-15' if len(f) <= 15 else '16+'
        raw_class = text_value(a.race_class)
        name = unicodedata.normalize('NFKC', raw_class).strip()
        name = {'500万': '1勝', '1000万': '2勝', '1600万': '3勝'}.get(name, name)
        assert f.is_debut.isin([0, 1]).all()
        values = f[RELATIVE].to_numpy(dtype=float)
        assert not np.isinf(values).any()
        row = {'venue': text_value(a.venue_code), 'track': text_value(a.track_type),
               'going': text_value(a.going), 'distance': dist, 'field_size': size, 'race_class': name,
               'month': f'{a.race_date.month:02d}',
               'nk_share': fraction(sum(h.startswith('nk:') for h in f.horse_id) / len(f)),
               'time_coverage': fraction(int(f.rel_time_avg.notna().sum()) / len(f)),
               'relative_coverage': fraction(int(np.isfinite(values).sum()) / values.size),
               'debut_share': fraction(int(f.is_debut.sum()) / len(f))}
        row['month_track'] = row['month'] + ':' + row['track']
        result[rid] = (str(a.race_date), row)
    return result


def independent_distribution(frame, columns):
    result = {}
    indices = list(frame.groupby('race_id', sort=False, observed=True).indices.values())
    for c in columns:
        values = frame[c].to_numpy(dtype=float)
        assert not np.isinf(values).any()
        finite = values[np.isfinite(values)]
        deviations = []
        for idx in indices:
            v = values[idx]
            v = v[np.isfinite(v)]
            if len(v):
                deviations.append(float(np.std(v, ddof=0)))
        result[c] = {'n_horses': len(values), 'missing_rate': int(np.isnan(values).sum()) / len(values),
            'p10_p50_p90': np.quantile(finite, [.1, .5, .9]).tolist() if len(finite) else None,
            'race_sd_p50_p90': np.quantile(deviations, [.5, .9]).tolist() if deviations else None}
    return result


def main():
    global RELATIVE
    output = Path(__file__).with_suffix('.json')
    assert not output.exists(), 'Preserve earlier independent review'
    frozen = source.verify()
    report_path = source.SPEC / 'evidence/diagnostic.json'
    rows_path = source.WORK / 'race-diagnostic.json'
    result = D.s.read_json(report_path)
    saved = D.s.read_json(rows_path)
    equal(result['method_sha256'], D.s.p.digest(ROOT / 'scripts/pruning_2026_diagnostic.py'))
    equal(result['run_freeze_sha256'], D.s.p.digest(source.WORK / 'run-freeze.json'))
    equal(saved['run_freeze_sha256'], result['run_freeze_sha256'])
    equal(result['race_rows_sha256'], D.s.p.digest(rows_path))
    assert result['can_adopt'] is False and result['eligible_for_verdict'] is False and result['new_fits'] == 0
    rows = saved['rows']
    equal(len(rows), 22990)
    equal(len({r['race_id'] for r in rows}), 22990)
    equal(len({r['race_day'] for r in rows}), 715)
    assert all('2020-01-01' <= r['race_day'] <= '2026-08-23' for r in rows)
    evidence_hashes = {}
    max_direct_error = 0.0
    for seed in SEEDS:
        old = {}
        for name in ('anchor', 'increment'):
            source_report = D.s.read_json(D.result_path(seed, name))
            path = Path(source_report['evidence_path'])
            old[name] = D.s.read_json(path)['rows']
            evidence_hashes[f'{seed}-{name}'] = D.s.p.digest(path)
            equal([(x['race_id'], x['race_day']) for x in old[name]],
                  [(x['race_id'], x['race_day']) for x in rows])
        for r, a, i in zip(rows, old['anchor'], old['increment'], strict=True):
            direct = i['active_winner_nll'] - a['active_winner_nll']
            equal(direct, a['diff'] - i['diff'])
            equal(direct, r[f'pruning_{seed}'])
            max_direct_error = max(max_direct_error, abs(direct - r[f'pruning_{seed}']))
            equal(i['diff'], r[f'correction_{seed}'])
            equal(a['diff'], r[f'total_{seed}'])
    for row in rows:
        for m in METRICS:
            equal(average([row[f'{m}_{s}'] for s in SEEDS]), row[m])
        equal(row['pruning'] + row['correction'], row['total'])
        equal(row['year'], int(row['race_day'][:4]))
        equal(row['same_season'], row['race_day'][5:] <= '08-23')
    for year in range(2020, 2027):
        selected = [r for r in rows if r['year'] == year]
        equal(summary(selected), result['annual'][str(year)]['all'])
        equal(summary([r for r in selected if r['same_season']]), result['annual'][str(year)]['same_season'])
    target = [r for r in rows if r['year'] == 2026]
    references = {'2024-2025': [r for r in rows if r['year'] in (2024, 2025) and r['same_season']],
                  '2020-2025': [r for r in rows if r['year'] < 2026 and r['same_season']]}
    for name, selected in {'2026': target, **references}.items():
        for key in GROUPS:
            partition = groups(selected, key)
            equal({k: summary(v, len(selected)) for k, v in partition.items()}, result['partitions'][name][key])
            for m in METRICS:
                equal(math.fsum(summary(v, len(selected))['metrics'][m]['contribution'] for v in partition.values()),
                      average([r[m] for r in selected]))
    for name, reference in references.items():
        for key in GROUPS:
            a, b = groups(reference, key), groups(target, key)
            saved_comp = result['composition_comparisons'][name][key]
            cells = []
            for cell in sorted(set(a) | set(b)):
                p, q = len(a.get(cell, [])) / len(reference), len(b.get(cell, [])) / len(target)
                m = average([r['pruning'] for r in a[cell]]) if cell in a else None
                n = average([r['pruning'] for r in b[cell]]) if cell in b else None
                c = (q - p) * (n + m) / 2 if m is not None and n is not None else 0.0
                w = (n - m) * (q + p) / 2 if m is not None and n is not None else 0.0
                u = 0.0 if m is not None and n is not None else q * n if m is None else -p * m
                cells.append({'cell': cell, 'reference_share': p, 'target_share': q,
                    'reference_mean': m, 'target_mean': n, 'composition': c, 'within': w, 'unmatched': u})
            equal(cells, saved_comp['cells'])
            for field in ('composition', 'within', 'unmatched'):
                equal(math.fsum(c[field] for c in cells), saved_comp[field])
            equal(math.fsum(saved_comp[k] for k in ('composition', 'within', 'unmatched')), saved_comp['delta'])
            equal(average([r['pruning'] for r in target]) - average([r['pruning'] for r in reference]), saved_comp['delta'])
            equal(sum(c['target_share'] for c in cells if c['reference_mean'] is None), saved_comp['target_unmatched_share'])
            equal(sum(c['reference_share'] for c in cells if c['target_mean'] is None), saved_comp['reference_unmatched_share'])
    days = groups(target, 'race_day')
    sensitivity = result['day_sensitivity_2026']
    equal({k: summary(v, len(target)) for k, v in days.items()}, sensitivity['days'])
    leave = [average([r['pruning'] for r in target if r['race_day'] != day]) for day in days]
    equal({'min_mean': min(leave), 'max_mean': max(leave)}, sensitivity['leave_one_day_out'])
    worst = sorted(days, key=lambda day: (-math.fsum(r['pruning'] for r in days[day]), day))
    for count in (1, 3, 5):
        excluded = worst[:count]
        observed = sensitivity['remove_worst_days_posthoc'][str(count)]
        equal(excluded, observed['excluded_days'])
        equal(summary([r for r in target if r['race_day'] not in excluded]), observed['remaining'])
        equal(math.fsum(r['pruning'] for r in target if r['race_day'] in excluded) / len(target), observed['removed_pruning_contribution'])
    print('Losses, annual/partitions, 24 decompositions and whole-day sensitivity PASS', flush=True)
    matrix, eval_races = D.s.reuse.load(D.s.p.WORK / 'snapshot.pkl')
    ids = {r['race_id'] for r in rows}
    expected = {r.context.race_id: {h.horse_id for h in r.context.started_horses}
                for r in eval_races if r.context.race_id in ids}
    equal(set(expected), ids)
    RELATIVE = next(c['drop_features'] for c in D.s.old.load_config()['candidates'] if c['id'] == 'relative_ability')
    absolute = sorted({c[:-9] for c in RELATIVE if c.endswith('_vs_field')})
    assert len(RELATIVE) == 13 and len(absolute) == 11
    columns = RELATIVE + absolute
    fields = ['race_id', 'horse_id', 'race_date', 'venue_code', 'track_type', 'distance', 'field_size', 'race_class', 'going', 'is_debut']
    horses = matrix.frame.loc[matrix.frame.race_id.isin(ids), fields + sorted(set(columns))].copy()
    del matrix, eval_races
    gc.collect()
    attrs = independent_attributes(horses, expected)
    equal(set(attrs), ids)
    for row in rows:
        date, attr = attrs[row['race_id']]
        equal(date, row['race_day'])
        equal(attr, {key: row[key] for key in GROUPS})
    print('All eligible started-horse identities and group assignments PASS', flush=True)
    horse_counts = {}
    for year in range(2020, 2027):
        selected_ids = {r['race_id'] for r in rows if r['year'] == year and r['same_season']}
        selected = horses[horses.race_id.isin(selected_ids)]
        equal(independent_distribution(selected, columns), result['feature_distribution_same_season'][str(year)])
        horse_counts[str(year)] = len(selected)
    ref = horses[horses.race_id.isin({r['race_id'] for r in references['2024-2025']})]
    cur = horses[horses.race_id.isin({r['race_id'] for r in target})]
    for column in columns:
        a, b = ref[column].to_numpy(dtype=float), cur[column].to_numpy(dtype=float)
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        if len(a) and len(b):
            lo, hi = np.quantile(a, [.01, .99])
            equal({'reference_p1_p99': [float(lo), float(hi)],
                   'target_below_p1': sum(b < lo) / len(b), 'target_above_p99': sum(b > hi) / len(b),
                   'denominator': 'nonmissing_horses'}, result['feature_tails_vs_2024_2025'][column])
        else:
            equal({'unavailable': True}, result['feature_tails_vs_2024_2025'][column])
    equal(frozen, source.verify())
    review = {'artifact_kind': 'independent_posthoc_diagnostic_review', 'status': 'PASS',
        'can_adopt': False, 'eligible_for_verdict': False, 'additional_fits': 0,
        'method_sha256': D.s.p.digest(__file__), 'source_method_sha256': result['method_sha256'],
        'run_freeze_sha256': result['run_freeze_sha256'], 'diagnostic_sha256': D.s.p.digest(report_path),
        'race_rows_sha256': D.s.p.digest(rows_path), 'source_evidence_sha256': evidence_hashes,
        'eligible_races': len(rows), 'race_days': len({r['race_day'] for r in rows}),
        'started_horse_rows': len(horses), 'same_season_horse_counts': horse_counts,
        'checks': CHECKS, 'max_numerical_error': MAX_ERROR, 'max_direct_pruning_nll_error': max_direct_error,
        'all_seed_direct_loss_and_contrast_identity': True, 'all_annual_and_same_season_summaries': True,
        'all_36_partitions_and_contributions': True, 'all_24_symmetric_decompositions_and_unmatched_mass': True,
        'whole_day_sensitivity_and_leave_one_day_out': True, 'all_started_horse_ids_and_group_assignments': True,
        'all_168_feature_year_distributions_and_24_tail_comparisons': True,
        'frozen_source_runtime_upstream_and_receipts_before_after': True,
        'limitations': ['No new fit, CI, p-value, selection, or production decision.',
            'Features describe eligible-race started horses; loss summaries are race weighted.',
            'Same-season feature distributions were independently recomputed; relative-feature construction is covered by the separate integrity review.',
            'Posthoc group concentration and descriptive composition changes do not identify a causal feature or a valid deployment rule.']}
    D.write_json(output, review)
    print(f'PASS {output}; checks={CHECKS}; max_error={MAX_ERROR}; direct_error={max_direct_error}', flush=True)


if __name__ == '__main__':
    main()
