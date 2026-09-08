"""Read-only relative-feature integrity audit; no outcomes, feature selection or fitting."""
from __future__ import annotations

import datetime as dt
import gc
import math
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import numpy as np
import pandas as pd
import gap_seed_recheck as d
from horseracing_db.enums import EntryStatus
from horseracing_features import relative_ability_features as rel

OUT = Path(__file__).with_suffix('.json')
KEYS = ['race_id', 'horse_id']
ATOL = 1e-12
RTOL = 1e-12


def compare(actual, expected):
    a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
    finite = np.isfinite(a) & np.isfinite(b)
    delta = np.abs(a[finite] - b[finite])
    return {'rows': len(a), 'paired_finite': int(finite.sum()),
        'nan_pattern_mismatch': int(np.count_nonzero(np.isnan(a) != np.isnan(b))),
        'stored_infinite': int(np.isinf(a).sum()), 'recomputed_infinite': int(np.isinf(b).sum()),
        'finite_not_bitwise_equal': int(np.count_nonzero(a[finite] != b[finite])),
        'beyond_tolerance': int(np.count_nonzero(delta > ATOL + RTOL * np.abs(b[finite]))),
        'max_absolute_error': float(delta.max()) if len(delta) else 0.0}


def independent_recompute(frame):
    """Explicit other-horse fsum and count-based ties, independent of production helpers."""
    base_columns = rel._DEV_INPUTS
    out = np.full((len(frame), len(rel.RELATIVE_ABILITY_COLUMNS)), np.nan, dtype=float)
    bases = frame[base_columns].to_numpy(dtype=float)
    for indices in frame.groupby('race_id', sort=False).indices.values():
        block = bases[indices]
        for j in range(len(base_columns)):
            values = block[:, j]
            valid = np.isfinite(values)
            for k in range(len(values)):
                if not valid[k]:
                    continue
                others = values[valid & (np.arange(len(values)) != k)]
                if len(others):
                    out[indices[k], j] = values[k] - math.fsum(float(v) for v in others) / len(others)
        for offset, col in enumerate(rel._RANK_INPUTS, start=len(base_columns)):
            values = block[:, base_columns.index(col)]
            valid = values[np.isfinite(values)]
            for k, value in enumerate(values):
                if np.isfinite(value):
                    # Average rank among ties, divided by the nonmissing started count.
                    rank = 1 + np.count_nonzero(valid < value) + (np.count_nonzero(valid == value) - 1) / 2
                    out[indices[k], offset] = rank / len(valid)
    return pd.DataFrame(out, columns=rel.RELATIVE_ABILITY_COLUMNS)


def rates(frame, mask):
    sub = frame.loc[mask]
    return {'horse_rows': len(sub), 'races': int(sub.race_id.nunique()),
            'days': int(sub.race_date.nunique()),
            'columns': {col: {'missing': int(sub[col].isna().sum()),
                             'missing_rate': float(sub[col].isna().mean()),
                             'infinite': int(np.isinf(sub[col].to_numpy(dtype=float)).sum())}
                        for col in rel._DEV_INPUTS + rel.RELATIVE_ABILITY_COLUMNS}}


def main():
    if OUT.exists():
        raise FileExistsError('Preserve existing audit evidence')
    method = d.s.p.digest(__file__)
    cfg, frozen = d.verify()
    frozen_sha = d.s.p.digest(d.WORK / 'run-freeze.json')
    print('116 source/runtime/upstream verified before audit', flush=True)
    matrix, all_races = d.s.reuse.load(d.s.p.WORK / 'snapshot.pkl')
    columns = KEYS + ['race_date'] + rel._DEV_INPUTS + rel.RELATIVE_ABILITY_COLUMNS
    frame = matrix.frame.loc[matrix.frame.race_date >= dt.date(2020, 1, 1), columns].copy().reset_index(drop=True)
    if (frame.race_date > dt.date(2026, 8, 23)).any() or frame.duplicated(KEYS).any():
        raise ValueError('Unexpected snapshot date/key scope')
    actual_pop = frame.groupby('race_id', sort=False).horse_id.agg(set).to_dict()
    expected_pop = {r.context.race_id: {h.horse_id for h in r.context.started_horses}
                    for r in all_races if r.context.race_date >= dt.date(2020, 1, 1)}
    extra = sorted(actual_pop.keys() - expected_pop.keys())
    extras = [{'race_id': rid, 'race_date': str(frame.loc[frame.race_id == rid, 'race_date'].iloc[0]),
               'horse_rows': len(actual_pop[rid]), 'reason': 'Not in frozen EvalRace population; underlying exclusion reason not determined by this audit'}
              for rid in extra]
    if (expected_pop.keys() - actual_pop.keys()
        or any(actual_pop[rid] != horses for rid, horses in expected_pop.items())):
        raise ValueError('Audit rows do not equal each full started field')
    frame = frame.loc[frame.race_id.isin(expected_pop)].reset_index(drop=True)
    del matrix, all_races, actual_pop, expected_pop
    gc.collect()
    if np.isinf(frame[rel._DEV_INPUTS].to_numpy(dtype=float)).any():
        raise ValueError('Nonfinite absolute as-of input prevents stated finite arithmetic audit')
    print(f'Started population exact rows={len(frame)} races={frame.race_id.nunique()}', flush=True)
    independent = independent_recompute(frame)
    # Exercise the actual registered builder as a second check, using stored as-of
    # values and exact started populations only; no history/results are rebuilt.
    rh = frame[KEYS].copy()
    rh['entry_status'] = EntryStatus.STARTED
    produced = rel.build_relative_ability_features(SimpleNamespace(race_horses=rh), ability_frame=frame)
    produced = frame[KEYS].merge(produced, on=KEYS, validate='one_to_one', sort=False)
    checks = {col: {'independent_formula': compare(frame[col], independent[col]),
                    'registered_builder': compare(frame[col], produced[col])}
              for col in rel.RELATIVE_ABILITY_COLUMNS}
    ranks = {}
    for col in [f'{c}_field_rank' for c in rel._RANK_INPUTS]:
        vals = frame[col].dropna().to_numpy(dtype=float)
        ranks[col] = {'finite_count': len(vals), 'min': float(vals.min()), 'max': float(vals.max()),
                      'outside_open_zero_closed_one': int(np.count_nonzero((vals <= 0) | (vals > 1)))}
    years = frame.race_date.map(lambda v: v.year)
    same_period = frame.race_date.map(lambda v: (v.month, v.day) <= (8, 23))
    yearly = {str(y): rates(frame, years == y) for y in range(2020, 2027)}
    comparable = {str(y): rates(frame, (years == y) & same_period) for y in range(2020, 2027)}
    changes = {}
    for col in rel.RELATIVE_ABILITY_COLUMNS:
        history = [comparable[str(y)]['columns'][col]['missing_rate'] for y in range(2020, 2026)]
        now = comparable['2026']['columns'][col]['missing_rate']
        changes[col] = {'2026_missing_rate': now, '2020_2025_same_period_min': min(history),
                        '2020_2025_same_period_max': max(history),
                        '2026_minus_2025_percentage_points': (now - comparable['2025']['columns'][col]['missing_rate']) * 100,
                        'above_prior_same_period_max': now > max(history)}
    failure = any(v['nan_pattern_mismatch'] or v['stored_infinite'] or v['recomputed_infinite'] or v['beyond_tolerance']
                  for checks_by_method in checks.values() for v in checks_by_method.values())
    failure |= any(v['outside_open_zero_closed_one'] for v in ranks.values())
    cfg_after, frozen_after = d.verify()
    if (cfg_after != cfg or frozen_after != frozen or d.s.p.digest(d.WORK / 'run-freeze.json') != frozen_sha
        or d.s.p.digest(__file__) != method):
        raise ValueError('Frozen sources or audit method changed while computing')
    result = {'artifact_kind': 'relative_feature_integrity_research_audit', 'can_adopt': False,
        'eligible_for_verdict': False, 'state': 'MISMATCH_FOUND' if failure else 'PASS',
        'method_sha256': method, 'source116_freeze_sha256': frozen_sha,
        'source116_config_hash': d.s.p.gate_config_hash(cfg), 'source116_source_hash': frozen['source_hash'],
        'source_snapshot_sha256': frozen['sources']['snapshot_sha256'],
        'relative_builder_sha256': d.s.p.digest(rel.__file__), 'runtime': d.s.runtime(),
        'verification_before_and_after': True, 'window': {'from': '2020-01-01', 'to': '2026-08-23'},
        'rows': len(frame), 'races': int(frame.race_id.nunique()), 'columns': rel.RELATIVE_ABILITY_COLUMNS,
        'feature_cells_checked_per_method': len(frame) * len(rel.RELATIVE_ABILITY_COLUMNS),
        'tolerance': {'atol': ATOL, 'rtol': RTOL}, 'started_population_exact': True,
        'snapshot_extra_races_excluded_from_evaluation_audit': extras,
        'audit_population': 'All23030 frozen2020..2026 EvalRaces with their entire started field; no missing evaluation race or horse mismatch',
        'initial_attempt': 'Stopped before producing JSON because whole snapshot race set included one extra non-evaluation race. Read-only diagnosis found no missing evaluation races or shared-race horse differences. Parent independent review approved explicit recorded exclusion before rerun; old source/artifacts unchanged.',
        'checks': checks, 'rank_ranges': ranks, 'full_available_years': yearly,
        'same_period_jan01_aug23': comparable, 'missingness_comparison': changes,
        'limitations': ['Stored upstream as-of values are audited as inputs, not rebuilt from raw history; strict-past upstream correctness is not newly proven.',
            'Snapshot retains started horses only; equality is verified for the full observed started field, not hypothetical prerace scratch changes.',
            'Full2026 endsAug23, so cross-year missingness comparison usesJan1..Aug23 for every year.',
            'Missing-rate changes are descriptive and can reflect population mix; no outcome/NLL association, feature selection or additional fit is performed.',
            'Sub-tolerance floating-point discrepancies between fsum and pandas grouped summation are explicitly counted, not silently called bitwise equality.']}
    d.write_json(OUT, result)
    print({'state': result['state'], 'cells_per_method': result['feature_cells_checked_per_method'],
           'max_absolute_error': max(v['max_absolute_error'] for m in checks.values() for v in m.values()),
           'nan_pattern_mismatches': sum(v['nan_pattern_mismatch'] for m in checks.values() for v in m.values()),
           '2026_above_historical_sameperiod_max': [k for k, v in changes.items() if v['above_prior_same_period_max']]}, flush=True)


if __name__ == '__main__':
    main()
