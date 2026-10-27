"""Read-only audit: synthetic reproductions and a hash-pinned historical input scan."""
from __future__ import annotations

from dataclasses import replace
import datetime as dt
import hashlib
import json
from pathlib import Path
import pickle
import sys

import pandas as pd

ROOT = Path('/Users/kuwatawaku/workspace/horseracing')
sys.path.insert(0, str(ROOT / 'features/tests'))
from _frames import make_frames
from horseracing_features.owner_breeder_features import build_owner_breeder_features
from horseracing_features.pace_features import build_pace_features
from horseracing_features.pace_scenario_features import build_pace_scenario_features


def owner_reproduction():
    specs = []
    for i in range(20):
        specs.append({'race_id': f'20200101{i:04d}',
                      'race_date': str(dt.date(2020, 1, 1) + dt.timedelta(days=i)),
                      'horses': [
                          {'horse_id': 'a', 'owner_name': 'A', 'finish_order': 1},
                          {'horse_id': 'b', 'owner_name': 'B', 'finish_order': 2},
                      ]})
    rid = '202002010001'
    specs.append({'race_id': rid, 'race_date': '2020-02-01', 'horses': [
        {'horse_id': 'target', 'owner_name': 'A', 'result_status': None},
        {'horse_id': 'rival', 'owner_name': 'B', 'result_status': None},
    ]})
    frames = make_frames(specs)
    before = build_owner_breeder_features(frames).query('race_id == @rid')
    later_master = frames.horses.copy()
    later_master.loc[later_master.horse_id.eq('target'), 'owner_name'] = 'B'
    after = build_owner_breeder_features(replace(frames, horses=later_master)).query('race_id == @rid')
    old = float(before.loc[before.horse_id.eq('target'), 'asof_owner_win_rate'].iloc[0])
    new = float(after.loc[after.horse_id.eq('target'), 'asof_owner_win_rate'].iloc[0])
    assert (old, new) == (1., 0.)
    return {'same_races_and_results': True, 'target_race_date': '2020-02-01',
            'only_change': 'current horses.owner_name of target: A -> B',
            'owner_win_rate_before': old, 'owner_win_rate_after': new,
            'claim': 'Historical as-of features change when a later mutable master changes; real transfer incidence unmeasured.'}


def unknown_reproduction():
    frames = make_frames([
        {'race_id': '202001010001', 'race_date': '2020-01-01', 'horses': [
            {'horse_id': 'unknown', 'running_style': None, 'finish_order': 1},
            {'horse_id': 'front', 'running_style': '逃げ', 'finish_order': 2},
        ]},
        {'race_id': '202002010001', 'race_date': '2020-02-01', 'horses': [
            {'horse_id': 'unknown', 'result_status': None},
            {'horse_id': 'front', 'result_status': None},
        ]},
    ])
    pace = build_pace_features(frames)
    target = pace.query("race_id == '202002010001'")
    unknown = target.loc[target.horse_id.eq('unknown')].iloc[0]
    scenario = build_pace_scenario_features(frames, pace=pace).query("race_id == '202002010001'")
    coverage = float(scenario.field_style_coverage.iloc[0])
    assert unknown.front_runner_rate == unknown.closer_rate == 0. and coverage == 1.
    return {'unknown_front_rate': float(unknown.front_runner_rate),
            'unknown_closer_rate': float(unknown.closer_rate),
            'reported_field_style_coverage': coverage, 'actual_known_horses_fraction': .5}


def frozen_input_scan():
    folder = ROOT / 'artifacts/111-ability-observation'
    meta = json.loads((folder / 'snapshot.json').read_text())
    assert dt.date.fromisoformat(meta['data_through']) <= dt.date(2026, 9, 6)
    source = folder / 'source-frames.pkl'
    h = hashlib.sha256()
    with source.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    assert h.hexdigest() == meta['source_frames_sha256'] == '7dc539c4224fe4132c3892099f2c62790b47d1f3136fe8b79bbaeadd5c02ef11'
    with source.open('rb') as fh:
        frames = pickle.load(fh)
    dates = pd.to_datetime(frames.races.race_date)
    assert dates.max().date() == dt.date(2026, 8, 23)
    rh = frames.race_horses
    rr = frames.race_results
    assert not rh.duplicated(['race_id', 'horse_id']).any()
    assert not rr.duplicated(['race_id', 'horse_id']).any()
    joined = rh[['race_id', 'horse_id', 'entry_status']].merge(
        rr[['race_id', 'horse_id', 'result_status']], how='outer',
        on=['race_id', 'horse_id'], indicator=True)
    missing = joined.loc[joined.entry_status.eq('started') & joined['_merge'].eq('left_only')]
    extra = joined.loc[joined.entry_status.ne('started') & joined['_merge'].ne('left_only')]
    counts = pd.DataFrame({
        'started': rh.loc[rh.entry_status.eq('started')].groupby('race_id').size(),
        'all_result_rows': rr.groupby('race_id').size(),
        'missing_started_result_rows': missing.groupby('race_id').size(),
        'nonstarted_result_rows': extra.groupby('race_id').size(),
    }).fillna(0).astype(int)
    false_complete = counts.loc[(counts.missing_started_result_rows > 0) &
                                (counts.all_result_rows >= counts.started)]
    styles = rh.running_style
    unknown = styles.isna() | ~styles.isin(['逃げ', '先行', '中団', '後方', '差し', '追込', 'ﾏｸﾘ', 'マクリ'])
    # Quantify the source-definition shift separately from genuinely missing style values.
    rear = rh.loc[rh.entry_status.eq('started') & styles.eq('後方'), ['race_id', 'horse_id']].merge(
        rr[['race_id', 'horse_id', 'corner_orders']], on=['race_id', 'horse_id'], how='left')
    field_sizes = rh.loc[rh.entry_status.eq('started')].groupby('race_id').size()
    rear['field_size'] = rear.race_id.map(field_sizes)
    def first_corner(value):
        if isinstance(value, (list, tuple)) and value:
            return int(value[0]) if value[0] is not None else None
        return None
    rear['first_corner'] = rear.corner_orders.map(first_corner)
    comparable = rear.first_corner.gt(0) & rear.field_size.gt(0)
    modern_closer = comparable & rear.first_corner.gt(1) & (rear.first_corner / rear.field_size).gt(.5)
    rear_years = rear.merge(frames.races[['race_id', 'race_date']], on='race_id')
    rear_by_year = rear_years.assign(year=pd.to_datetime(rear_years.race_date).dt.year).groupby('year').size()
    return {'source_sha256': h.hexdigest(), 'data_through': str(dates.max().date()),
            'source_races': len(frames.races), 'started_rows': int(rh.entry_status.eq('started').sum()),
            'style_missing_all_rows': int(styles.isna().sum()),
            'style_missing_started_rows': int((styles.isna() & rh.entry_status.eq('started')).sum()),
            'style_unknown_started_rows': int((unknown & rh.entry_status.eq('started')).sum()),
            'style_tokens_started': {str(k): int(v) for k, v in rh.loc[rh.entry_status.eq('started'), 'running_style'].value_counts(dropna=False).items()},
            'rear_style_rows': len(rear),
            'rear_style_comparable_first_corner_rows': int(comparable.sum()),
            'rear_style_current_closer_zero_but_modern_heuristic_one_rows': int(modern_closer.sum()),
            'rear_style_rows_by_year': {str(k): int(v) for k, v in rear_by_year.items()},
            'missing_started_results': len(missing), 'missing_started_result_races': len(missing.race_id.unique()),
            'nonstarted_results': len(extra),
            'false_complete_races': false_complete.reset_index().to_dict('records'),
            'missing_started_result_race_details': counts.loc[counts.missing_started_result_rows > 0].reset_index().to_dict('records'),
            'db_reads': 0, 'new_model_fits': 0}


if __name__ == '__main__':
    print(json.dumps({'owner': owner_reproduction(), 'unknown_style': unknown_reproduction(),
                      'frozen_inputs': frozen_input_scan()}, ensure_ascii=False, indent=2))
