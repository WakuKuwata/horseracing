"""Explicitly outcome-selected descriptive follow-up of the new 480-rule study.

The complete set satisfying ALL bets>=1000 and ALL ROI>=1 is frozen before this
follow-up computes uncertainty or corrected settlements. Nothing is promoted to
the original primary/near-miss sets; no original output is overwritten.
"""
from __future__ import annotations

import explore_new_patterns_20260930 as evaluator

from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import pyarrow as pa

import new_pattern_features_20260930 as features
import new_patterns_settlement_20260930 as settlement

pa.set_cpu_count(1)
pa.set_io_thread_count(1)
ROOT = evaluator.ROOT
STUDY = evaluator.OUT
OUT = STUDY / 'descriptive_followup'
REPETITIONS = 4000
TAIL_COUNTS = (1, 3, 5, 10)
PERIODS = ('ALL', 'C', 'R', 'POST')


def hash_files(paths):
    return {str(p.relative_to(ROOT)): evaluator.common.sha(p) for p in paths}


def tail_sensitivity(bets):
    pay = (bets.ret + 100.).to_numpy(dtype=float)
    winners = np.sort(pay[pay > 0])[::-1]
    total = float(pay.sum()); stake = len(pay) * 100.
    return {
        'method': 'Remove the k largest winning payouts while retaining all original stakes; descriptive stress, not a new policy.',
        'largest_payout_yen': float(winners[0]) if len(winners) else None,
        'n_winning_bets': len(winners),
        'stress': [{
            'top_k': k, 'removed_winning_payouts': min(k, len(winners)),
            'removed_payout_yen': float(winners[:k].sum()),
            'removed_share_of_payout': float(winners[:k].sum() / total) if total else None,
            'roi_with_all_stakes_retained': float((total - winners[:k].sum()) / stake) if stake else None,
        } for k in TAIL_COUNTS],
    }


def archived_universe(basic):
    div = pd.read_json(evaluator.DIV, lines=True, dtype={'race_id': str})
    target = basic.loc[basic.race_date.between(settlement.FROM, settlement.THROUGH)]
    ids = set(target.race_id) & set(div.race_id)
    winners = div.loc[div.race_id.isin(ids)]
    assert len(ids) == len(winners) == winners.race_id.nunique() == 394
    assert winners.db_winner_matches.all()
    eligible = target.loc[target.race_id.isin(ids)].merge(
        winners[['race_id', 'horse_number', 'payout_yen_per_100']],
        on=['race_id', 'horse_number'], how='left', validate='one_to_one')
    eligible['actual_payout'] = eligible.payout_yen_per_100.fillna(0.)
    eligible['saved_payout'] = eligible.ret + 100.
    assert ((eligible.actual_payout > 0) == eligible.won).all()
    days = sorted(eligible.race_date.unique())
    assert len(eligible) == 5240 and len(days) == 14
    return eligible[['race_id', 'horse_id', 'horse_number', 'actual_payout', 'saved_payout']], ids, days


def main():
    start = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'freeze.json').exists():
        raise RuntimeError('Descriptive follow-up already frozen; refusing overwrite.')
    scores_path = STUDY / 'scores.json'
    scores = json.loads(scores_path.read_text())
    chosen = sorted(
        [r for r in scores if r['periods']['ALL']['n_bets'] >= 1000
         and r['periods']['ALL'].get('roi') is not None and r['periods']['ALL']['roi'] >= 1.],
        key=lambda r: (-r['periods']['ALL']['roi'], r['id']))
    pattern_map = {p['id']: p for p in json.loads((STUDY / 'patterns.json').read_text())}
    for record in chosen:
        assert record['conditions'] == pattern_map[record['id']]['conditions']
    original_freeze = json.loads((STUDY / 'freeze.json').read_text())
    for group in ('input_sha256', 'source_sha256'):
        assert all(evaluator.common.sha(Path(p)) == h for p, h in original_freeze[group].items())
    source_paths = [Path(__file__), Path(evaluator.__file__), Path(features.__file__),
                    Path(evaluator.common.__file__), Path(evaluator.old_eval.__file__),
                    Path(evaluator.old_eval.dsl.__file__), Path(settlement.__file__),
                    Path(settlement.common.__file__)]
    input_paths = [scores_path, STUDY / 'patterns.json', STUDY / 'freeze.json',
                   STUDY / 'selection_before_later_periods.json', STUDY / 'summary.json',
                   evaluator.ROWS, evaluator.PROVENANCE, evaluator.PRED, evaluator.RECEIPT, evaluator.DIV]
    input_hashes, source_hashes = hash_files(input_paths), hash_files(source_paths)
    freeze = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'analysis_role': 'Results-viewed descriptive follow-up only',
        'after_outcome_selection': True, 'selection_adjusted': False,
        'filter': 'Across all 480 original records, ALL.n_bets >= 1000 AND ALL.roi >= 1; model and nonmodel treated identically.',
        'n_source_patterns': len(scores), 'n_selected': len(chosen),
        'selected_ids': sorted(r['id'] for r in chosen),
        'display_order': [r['id'] for r in chosen],
        'display_sort': 'ALL ROI descending, then id ascending',
        'selected_definitions': [{k: r[k] for k in ('id', 'family', 'label', 'uses_model', 'conditions')} for r in chosen],
        'not_promoted_to_primary': True, 'original_analysis_unchanged': True,
        'original_holm_family_unchanged': True, 'new_holm_or_confirmation_claim': False,
        'input_sha256': input_hashes, 'source_sha256': source_hashes,
        'bootstrap': {'function': 'explore_new_patterns_20260930.evidence',
                      'repetitions': REPETITIONS, 'seed': 20260930,
                      'unit': 'All eligible race dates, including zero-bet dates',
                      'selection_adjusted': False,
                      'interpretation': 'Even a lower CI above 1 does not validate this outcome-selected strategy.'},
        'periods': {w: evaluator.WINDOWS[w] for w in PERIODS},
        'all_model_start': 2010, 'all_nonmodel_start': 2007,
        'date_end': '2026-09-22', 'year_2026_is_partial': True,
        'tail_stress_top_k': TAIL_COUNTS, 'tail_stress_retains_original_stakes': True,
        'archived_settlement': {'date_from': settlement.FROM, 'date_to': settlement.THROUGH,
                               'include_every_selected_id_even_with_zero_bets': True,
                               'fixed_original_choices': True,
                               'source': 'Displayed netkeiba result-page payouts, not independent JRA verification'},
        'can_adopt': False, 'untouched_holdout': False,
    }
    # This is the first output and precedes all new CI/tail/subset calculations.
    evaluator.save(OUT / 'freeze.json', freeze)
    print(f'Frozen {len(chosen)} explicitly outcome-selected descriptive rules', flush=True)

    provenance = json.loads(evaluator.PROVENANCE.read_text())
    assert provenance['strict2007_histories_rebuilt']
    assert provenance['rows_sha256'] == evaluator.common.sha(evaluator.ROWS)
    fields = {c['field'] for r in chosen for c in r['conditions']}
    core_fields = {'race_id', 'horse_id', 'horse_number', 'race_date', 'year', 'odds', 'won', 'race_ok', 'dead_heat'}
    raw_columns = (fields - set(features.NEW_COLUMNS) - {'market_er'}) | set(features.REQUIRED_COLUMNS) | core_fields
    raw = pd.read_parquet(evaluator.ROWS, columns=sorted(raw_columns))
    d = features.build_features(raw)
    del raw
    receipt = json.loads(evaluator.RECEIPT.read_text())
    assert evaluator.common.sha(evaluator.PRED) == receipt['prediction_sha256']
    pred = pd.read_parquet(evaluator.PRED, columns=['race_id', 'horse_id', 'pred', 'odds', 'ret'])
    pred = pred.rename(columns={'odds': 'prediction_odds', 'ret': 'prediction_ret'})
    d = d.merge(pred, on=['race_id', 'horse_id'], how='left', validate='one_to_one')
    del pred
    d = d.loc[d.race_ok & ~d.dead_heat].sort_values(['race_date', 'race_id', 'horse_id']).reset_index(drop=True)
    d['ret'] = np.where(d.won, d.odds * 100., 0.) - 100.
    has = d.pred.notna()
    assert has.equals(d.year.ge(2010))
    assert np.allclose(d.loc[has, 'odds'], d.loc[has, 'prediction_odds'])
    assert np.allclose(d.loc[has, 'ret'], d.loc[has, 'prediction_ret'])
    d['market_er'] = 1. + d.pred / 100.
    core_columns = ['race_id', 'horse_id', 'horse_number', 'race_date', 'year', 'odds', 'won', 'ret']
    arr = {field: d[field].to_numpy() for field in fields | {'race_id'}}
    d = d[list(dict.fromkeys(core_columns + sorted(fields)))].copy()
    basic = d[core_columns].copy()
    lookup, archived_races, archive_days = archived_universe(basic)
    universes = {}
    def universe(record, window):
        lo, hi = evaluator.WINDOWS[window]
        if record['uses_model']:
            lo = max(lo, 2010)
        if (lo, hi) not in universes:
            universes[(lo, hi)] = basic.loc[basic.year.between(lo, hi)].copy()
        return universes[(lo, hi)]
    year_universes = {year: basic.loc[basic.year.eq(year)].copy() for year in range(2007, 2027)}
    (OUT / 'bets').mkdir()
    (OUT / 'archived_bets').mkdir()
    cache, records, period_rows, year_rows, subset_rows = {}, [], [], [], []
    for original in chosen:
        mask = evaluator.select(arr, original, cache)
        bets = basic.loc[mask].copy()
        record = {k: original[k] for k in ('id', 'family', 'label', 'uses_model', 'conditions')}
        record.update(original_role=original['role'], analysis_role='post_outcome_descriptive',
                      after_outcome_selection=True, selection_adjusted=False,
                      original_screen_pass=original['screen_pass'],
                      original_failure_reasons=original['failure_reasons'],
                      not_promoted_to_primary=True, can_adopt=False, periods={}, yearly={})
        for window in ('D', 'Q', *PERIODS):
            u = universe(original, window)
            selected = u.loc[mask[u.index]]
            stats = evaluator.stats(selected, u)
            old = original['periods'][window]
            for key in ('n_bets', 'n_hits', 'n_days', 'stake_yen', 'payout_yen', 'roi', 'largest_hit_share', 'max_daily_drawdown_yen'):
                a, b = stats.get(key), old.get(key)
                assert (a is None and b is None) or np.isclose(a, b, rtol=1e-11, atol=1e-7), (original['id'], window, key, a, b)
            if window in PERIODS:
                stats.update(evaluator.evidence(selected, sorted(u.race_date.unique()), REPETITIONS))
                stats['tail_sensitivity'] = tail_sensitivity(selected)
            stats['original_support_roi_failure_reasons'] = evaluator.failure_reasons(stats)
            record['periods'][window] = stats
            if window in PERIODS:
                ci = stats.get('ci95')
                tails = {s['top_k']: s for s in stats['tail_sensitivity']['stress']}
                period_rows.append({'id': original['id'], 'label': original['label'], 'family': original['family'],
                    'uses_model': original['uses_model'], 'period': window,
                    'after_outcome_selection': True, 'selection_adjusted': False,
                    **{key: stats.get(key) for key in ('n_bets', 'n_hits', 'n_days', 'stake_yen', 'payout_yen', 'profit_yen', 'roi', 'largest_hit_share', 'roi_without_largest_hit')},
                    'ci95_lo': ci['lo'] if ci else None, 'ci95_hi': ci['hi'] if ci else None,
                    'top5_payout_share': tails[5]['removed_share_of_payout'],
                    'roi_without_top5_payouts_stake_unchanged': tails[5]['roi_with_all_stakes_retained']})
        for year in range(2010 if original['uses_model'] else 2007, 2027):
            st = evaluator.stats(bets.loc[bets.year.eq(year)], year_universes[year])
            record['yearly'][str(year)] = st
            year_rows.append({'id': original['id'], 'uses_model': original['uses_model'], 'year': year,
                              'partial_year': year == 2026, **st})
        assert sum(s['n_bets'] for s in record['yearly'].values()) == record['periods']['ALL']['n_bets']
        subset, corrected_bets = settlement.settle_one(bets, lookup, archived_races, archive_days)
        record['archived_settlement'] = subset
        subset_rows.append({'id': original['id'], 'uses_model': original['uses_model'],
            **{k: subset[k] for k in ('n_bets', 'n_hits', 'stake_yen', 'saved_payout_yen', 'archived_payout_yen',
                                     'saved_roi', 'archived_roi', 'archived_largest_hit_share', 'archived_roi_without_largest_hit',
                                     'zero_target_period_bets', 'zero_audited_bets')},
            'archived_ci95_lo': subset['bootstrap']['archived_ci95']['lo'] if subset['bootstrap']['archived_ci95'] else None,
            'archived_ci95_hi': subset['bootstrap']['archived_ci95']['hi'] if subset['bootstrap']['archived_ci95'] else None})
        payout_leaders = bets.assign(payout_yen=bets.ret + 100.).loc[lambda x: x.payout_yen > 0]
        payout_leaders = payout_leaders.sort_values(['payout_yen', 'race_date', 'race_id', 'horse_id'], ascending=[False, True, True, True]).head(10)
        record['largest_ten_payout_bets'] = payout_leaders[['race_id', 'horse_id', 'horse_number', 'race_date', 'odds', 'payout_yen']].to_dict('records')
        d.loc[mask].to_parquet(OUT / 'bets' / f"{original['id']}.parquet", index=False)
        corrected_bets.to_parquet(OUT / 'archived_bets' / f"{original['id']}.parquet", index=False)
        records.append(record)
        print(f"Computed {len(records)}/{len(chosen)} {original['id']}", flush=True)

    unchanged = input_hashes == hash_files(input_paths) and source_hashes == hash_files(source_paths)
    assert unchanged
    summary = {
        'analysis_role': 'Results-viewed descriptive follow-up only', 'after_outcome_selection': True,
        'selection_adjusted': False, 'n_source_patterns': len(scores), 'n_selected': len(records),
        'n_model': sum(r['uses_model'] for r in records), 'n_nonmodel': sum(not r['uses_model'] for r in records),
        'selected_ids': freeze['selected_ids'], 'display_order': freeze['display_order'],
        'original_analysis_unchanged': unchanged, 'not_promoted_to_primary': True,
        'original_holm_family_unchanged': True, 'can_adopt': False, 'untouched_holdout': False,
        'bootstrap_repetitions': REPETITIONS, 'archived_races': len(archived_races), 'archive_days': len(archive_days),
        'n_zero_archive_bets': sum(r['archived_settlement']['zero_audited_bets'] for r in records),
        'elapsed_s': round(time.time() - start, 2),
        'limitations': [
            'Selected after inspecting ALL outcomes; even a CI lower bound above 1 is not validation.',
            'Rules overlap and are not independent evidence; CIs are unadjusted for the 480-rule search and prior exploration.',
            'Model ALL begins in 2010, nonmodel ALL in 2007, and 2026 ends on September 22.',
            'Original win payouts are stored odds times 100; original choices lack certified decision-time prices.',
            'Archived correction covers only 394 races across 14 dates and uses displayed netkeiba payouts, not independently verified JRA data.',
            'Archived corrections change settlement only; they do not repair model training targets or change the original choices.',
            'Removing top payouts keeps original stakes and is a descriptive sensitivity, not an implementable strategy.',
        ],
    }
    evaluator.save(OUT / 'summary.json', summary)
    evaluator.save(OUT / 'records.json', records)
    evaluator.save(OUT / 'integrity.json', {'original_inputs_and_sources_unchanged': True,
                                         'input_sha256': input_hashes, 'source_sha256': source_hashes,
                                         'all_recomputed_point_stats_match_original': True})
    pd.DataFrame(period_rows).to_csv(OUT / 'periods.csv', index=False)
    pd.DataFrame(year_rows).to_csv(OUT / 'yearly.csv', index=False)
    pd.DataFrame(subset_rows).to_csv(OUT / 'archived_settlement.csv', index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)


if __name__ == '__main__':
    main()
