"""Bounded-memory replay of the existing 2,356-rule exploration.

Writes new research artifacts only; reads no database. This is a reproducibility
check of already-seen periods, not a new unseen holdout experiment.
"""
from __future__ import annotations

import os
for _var in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_var] = '1'

import argparse
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import dsl
import evaluate as ev

pa.set_cpu_count(1)
pa.set_io_thread_count(1)
REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'artifacts/roi_recheck_20260930/pattern_replay'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(out, name, value):
    path = out / name
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=1, default=ev._default)


def read_array(path, name):
    """Read one column, preserving evaluator floats and nullable string semantics."""
    col = pq.read_table(path, columns=[name], use_threads=False)[name].combine_chunks()
    if pa.types.is_boolean(col.type):
        return pc.fill_null(col, False).to_numpy(zero_copy_only=False).astype(float)
    if pa.types.is_integer(col.type) or pa.types.is_floating(col.type):
        return col.to_numpy(zero_copy_only=False).astype(float)
    encoded = col.dictionary_encode()
    values = np.asarray(encoded.dictionary.to_pylist() + [None], dtype=object)
    indices = pc.fill_null(encoded.indices, len(values) - 1).to_numpy(zero_copy_only=False)
    return values[indices]


class LazyRows(dict):
    """Always retain only core columns plus an LRU of at most 24 input columns."""
    def __init__(self, path, order, core):
        super().__init__(core)
        self.path = path
        self.order = order
        self.cache = OrderedDict()
        self.read_count = Counter()

    def __missing__(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        value = read_array(self.path, key)[self.order]
        self.cache[key] = value
        self.read_count[key] += 1
        while len(self.cache) > 24:
            self.cache.popitem(last=False)
        return value


def load_rows():
    path = ev.ART / 'rows.parquet'
    rid = read_array(path, 'race_id')
    hn = read_array(path, 'horse_number')
    ok = read_array(path, 'race_ok').astype(bool)
    dead = read_array(path, 'dead_heat').astype(bool)
    date = read_array(path, 'race_date')
    keep = ok & ~dead
    inds = np.flatnonzero(keep)
    order = inds[np.lexsort((hn[inds], rid[inds]))]
    excluded = {
        'raw_rows': len(rid), 'raw_races': len(np.unique(rid)),
        'invalid_race_rows': int((~ok).sum()), 'invalid_races': len(np.unique(rid[~ok])),
        'dead_heat_rows': int(dead.sum()), 'dead_heat_races': len(np.unique(rid[dead])),
        'excluded_union_rows': int((~keep).sum()), 'excluded_union_races': len(np.unique(rid[~keep])),
        'included_rows': len(order), 'included_races': len(np.unique(rid[keep])),
        'date_min': min(date[keep]), 'date_max': max(date[keep]),
        'population_rule': 'race_ok AND NOT dead_heat; same rule as existing evaluate.py',
    }
    core = {'race_id': rid[order], 'horse_number': hn[order], 'race_date': date[order]}
    _, ri = np.unique(core['race_id'], return_inverse=True)
    days, di = np.unique(core['race_date'], return_inverse=True)
    core.update(_race_idx=ri.astype(np.int64), _day_idx=di.astype(np.int64), _days=days,
                _day_year=np.asarray([int(d[:4]) for d in days]),
                _won=read_array(path, 'won')[order], _year=read_array(path, 'year')[order].astype(int),
                odds=read_array(path, 'odds')[order])
    excluded['included_rows_by_year'] = {int(y): int(n) for y, n in sorted(Counter(core['_year']).items())}
    return LazyRows(path, order, core), excluded


def dsl_checks():
    rejected = []
    for field in sorted(dsl.FORBIDDEN):
        for scope in ('horse_filter', 'race_filter', 'select'):
            p = {'pattern_id': 'test', 'horse_filter': [{'field': 'odds', 'op': 'gt', 'value': 0}]}
            if scope == 'select':
                p[scope] = {'rule': 'top_k', 'by': field, 'k': 1}
            else:
                p[scope] = [{'field': field, 'op': 'eq', 'value': 1}]
            try:
                dsl.parse_pattern(p)
            except dsl.DSLError:
                rejected.append([field, scope])
            else:
                raise AssertionError(f'Outcome field accepted: {field}/{scope}')
    return {'n_cases': len(rejected), 'all_rejected': True, 'cases': rejected,
            'scope': 'Schema rejection only; does not establish historical availability of allowed input columns.'}


def score(p, arr, div):
    if p.bet_type == 'win':
        return ev.score_win(p, arr, 'model' if p.uses_model else 'market')
    return ev.score_exotic(p, arr, div)


def bootstrap(r, window, arr, b):
    if r['kind'] == 'exotic':
        return ev.exotic_bootstrap(r['_bets'], r[window]['_sel'], b=b)
    return ev.day_bootstrap(r['_stake'], r['_payout'], r[window]['_sel'], arr, b=b)


def record(p, r):
    rec = ev.strip(r)
    rec.update(family=p.family, label_ja=p.label_ja, source=p.source, stake=p.stake,
               select=p.select_rule, uses_model=p.uses_model, notes=p.notes)
    okD, whyD = ev.passes(r['D'])
    if r['kind'] == 'exotic':
        _, whyC = ev.passes(r['C'])
        rec.update(stage='C' if okD else 'D', stage_reason=whyC if okD else whyD, screen_pass=okD)
    else:
        okQ, whyQ = ev.passes(r['Q']) if okD else (False, 'not_reached')
        rec.update(screen_pass=okD and okQ, stage='C' if okD and okQ else ('Q' if okD else 'D'),
                   stage_reason=whyD if not okD else (whyQ if not okQ else 'reached_C'))
    return rec


def near_key(rec):
    d = rec['D']
    if rec['kind'] == 'exotic':
        return d['roi'] if d.get('n_bets', 0) >= 50 and d.get('n_hits', 0) >= 5 and d.get('roi') is not None else -1
    q = rec['Q']
    if d.get('n_hits', 0) < ev.DEGRADE['min_hits'] or q.get('n_hits', 0) < ev.DEGRADE['min_hits']:
        return -1
    return min(d['roi'], q['roi'])


def flatten(rows):
    flat = []
    for rec in rows:
        f = {k: v for k, v in rec.items() if k not in ('D', 'Q', 'C', 'C_boot')}
        for w in ('D', 'Q', 'C'):
            st = rec.get(w, {})
            for key in ('n_bets', 'n_races', 'n_days', 'n_hits', 'roi', 'max_hit_share', 'loho_roi', 'years_ge1', 'years_n'):
                f[f'{w}_{key}'] = st.get(key)
        if 'C_boot' in rec:
            f.update(C_ci_lo=rec['C_boot']['ci_lo'], C_ci_hi=rec['C_boot']['ci_hi'])
        flat.append(f)
    return pd.DataFrame(flat)


def parity(current):
    old = pd.read_parquet(ev.ART / 'results/a2/scores.parquet').set_index('pattern_id')
    now = current.set_index('pattern_id')
    columns = [c for c in old.columns if c in now.columns and (c.startswith(('D_', 'Q_', 'C_')) and c not in ('C_ci_lo', 'C_ci_hi', 'C_test') or c in ('stage', 'stage_reason', 'screen_pass', 'n_bets_total'))]
    differences = []
    for pid in sorted(set(old.index) & set(now.index)):
        for col in columns:
            a, b = old.at[pid, col], now.at[pid, col]
            if pd.isna(a) and pd.isna(b):
                continue
            if isinstance(a, (int, float, np.number)) and isinstance(b, (int, float, np.number)):
                match = np.isclose(a, b, rtol=1e-11, atol=1e-11, equal_nan=True)
            else:
                match = a == b
            if not match:
                differences.append({'pattern_id': pid, 'column': col, 'previous': a, 'replayed': b})
    return {'reference': 'artifacts/roi_explore/results/a2/scores.parquet',
            'reference_sha256': sha(ev.ART / 'results/a2/scores.parquet'),
            'compared_patterns_including_controls': len(set(old.index) & set(now.index)),
            'missing_previous': sorted(set(now.index) - set(old.index)),
            'missing_current': sorted(set(old.index) - set(now.index)),
            'compared_columns': columns, 'difference_count': len(differences), 'differences': differences,
            'tolerance': 'rtol=1e-11, atol=1e-11',
            'bootstrap_comparison': 'Not exact parity: replay uses 2000 draws; reference main run uses 10000.'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--boot', type=int, default=2000)
    args = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'freeze.json').exists():
        raise RuntimeError('Append-only run already frozen; do not overwrite artifacts.')
    files = sorted((ev.ART / 'patterns').glob('*.json'))
    pats, rejects = dsl.load_pattern_files(files)
    assert len(pats) == 2356 and not rejects
    controls = [dsl.parse_pattern(d, source='controls') for d in ev.CONTROLS]
    inputs = files + [ev.ART / 'rows.parquet', ev.ART / 'dividends.parquet']
    sources = [Path(dsl.__file__), Path(ev.__file__), Path(__file__)]
    freeze = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'purpose': 'Independent execution replay of previously inspected historical results; not a new unseen holdout.',
        'n_patterns': len(pats), 'n_controls': len(controls), 'pattern_ids': [p.pattern_id for p in pats],
        'inputs_sha256': {str(p.relative_to(REPO)): sha(p) for p in inputs},
        'source_sha256': {str(p.relative_to(REPO)): sha(p) for p in sources},
        'windows': ev.WINDOWS, 'degrade': ev.DEGRADE,
        'screen': 'win: pass D and Q; exotic: pass D. ROI>=1, hits>=20, days>=100, max payout share<=0.5',
        'near_misses': 'Top 40 by existing evaluate.near score; diagnostic only, not added to Holm family',
        'confirmation': 'Day-cluster percentile CI; null-centered bootstrap one-sided p; Holm alpha 0.025 on noncontrol screen passers',
        'bootstrap_reps': args.boot, 'ci_seed': 20260923, 'test_seed': 20260924,
        'null_simulation': 'Not rerun', 'threads': 1, 'input_column_lru': 24,
        'can_adopt': False, 'eligible_for_verdict': False,
        'runtime': {'python': sys.version, 'numpy': np.__version__, 'pandas': pd.__version__, 'pyarrow': pa.__version__},
    }
    save(OUT, 'freeze.json', freeze)
    save(OUT, 'dsl_forbidden_checks.json', dsl_checks())
    ev.log('loading bounded-memory inputs')
    arr, exclusions = load_rows()
    save(OUT, 'population.json', exclusions)
    div = ev.load_dividends()
    ev.log(f"rows={len(arr['race_id']):,} patterns={len(pats)} controls={len(controls)}")
    rows, ctl_rows, pvals = [], [], {}
    byid = {p.pattern_id: p for p in pats + controls}
    closing_fields = {'odds', 'q', 'popularity', 'odds_rank', 'q_share_of_fav', 'is_fav', 'n_fav_under_2',
                      'n_odds_under_10', 'fav_odds', 'second_odds', 'odds_gap12', 'fav_q', 'q_entropy_norm',
                      'ev', 'p_over_q', 'model_fav_is_market_fav', 'place_odds_low', 'place_odds_high'}
    classification = []
    for i, p in enumerate(pats + controls):
        r = score(p, arr, div)
        rec = record(p, r)
        if rec['screen_pass'] and r['C'].get('n_bets', 0) > 0:
            rec['C_boot'] = bootstrap(r, 'C', arr, args.boot)
            rec['confirm_point_ge1'] = bool(r['C']['roi'] >= 1)
            rec['confirm_ci_lo_gt1'] = bool(rec['C_boot']['ci_lo'] > 1)
            if p.family != 'control':
                if r['kind'] == 'exotic':
                    bets = r['_bets']
                    ds, dp = ev.day_aggregates(np.full(len(bets['pay']), 100.), bets['pay'], r['C']['_sel'], bets['days'])
                else:
                    ds, dp = ev.day_aggregates(r['_stake'], r['_payout'], r['C']['_sel'], arr['_day_idx'])
                rec['C_test'] = ev.centered_pvalue(ds, dp, b=args.boot)
                pvals[p.pattern_id] = rec['C_test']['p_one_sided']
        if p.family == 'control':
            ctl = ev.strip(r)
            ctl['label_ja'] = p.label_ja
            for w in ('D', 'Q', 'C'):
                if r.get(w, {}).get('n_bets', 0) > 0:
                    ctl[f'{w}_boot'] = bootstrap(r, w, arr, 2000)
            ctl_rows.append(ctl)
        else:
            fields = {c.field for c in p.race_filter + p.horse_filter}
            if p.select_by:
                fields.add(p.select_by)
            if p.stake == 'inverse_odds':
                fields.add('odds')
            if p.stake == 'edge':
                fields.add('ev')
            # For an unordered box, odds sorting changes enumeration but not bought tickets.
            if p.bet_type != 'win' and p.combo_type == 'axis' and p.select_rule == 'all':
                fields.add('odds')
            classification.append({'pattern_id': p.pattern_id, 'uses_model': p.uses_model,
                                   'current_closing_fields': sorted(fields & closing_fields),
                                   'no_explicit_current_closing_input': not bool(fields & closing_fields)})
        rows.append(rec)
        del r
        if (i + 1) % 100 == 0:
            ev.log(f'scored {i + 1}/{len(pats) + len(controls)} elapsed={time.time()-t0:.1f}s')
    hm = ev.holm(pvals)
    for r in rows:
        if r['pattern_id'] in hm:
            r['confirm_holm'] = hm[r['pattern_id']]
    near = sorted([r for r in rows if r['family'] != 'control'], key=near_key, reverse=True)[:40]
    for rec in near:
        if 'C_boot' not in rec and rec.get('C', {}).get('n_bets', 0) > 0:
            r = score(byid[rec['pattern_id']], arr, div)
            rec['C_boot'] = bootstrap(r, 'C', arr, args.boot)
            del r
    survivors = [r for r in rows if r.get('confirm_ci_lo_gt1')]
    weak = [r for r in rows if r.get('confirm_point_ge1') and not r.get('confirm_ci_lo_gt1')]
    researched = [r for r in rows if r['family'] != 'control']
    summary = {
        'run': 'pattern_replay_20260930', 'n_patterns': len(pats), 'n_rejected': len(rejects), 'n_controls': len(controls),
        'stage_counts': Counter(r['stage'] for r in researched), 'stage_reasons': Counter(r['stage_reason'] for r in researched),
        'n_survivors_ci': len(survivors), 'n_weak_point': len(weak), 'n_reached_C': len(pvals), 'n_confirmed_holm': sum(hm.values()),
        'windows': ev.WINDOWS, 'degrade': ev.DEGRADE, 'elapsed_s': round(time.time()-t0, 1),
        'by_kind': Counter(r['kind'] for r in researched), 'by_source': Counter(r['source'] for r in researched),
        'by_bet_type': Counter(r['bet_type'] for r in researched),
        'n_no_explicit_current_closing_input': sum(r['no_explicit_current_closing_input'] for r in classification),
        'n_no_model_no_explicit_current_closing_input': sum(r['no_explicit_current_closing_input'] and not r['uses_model'] for r in classification),
        'closing_input_classification_limit': 'Only DSL-level explicit dependencies; model training and inherited historical fields require separate provenance audit.',
        'bootstrap_reps': args.boot, 'null_simulation': 'not rerun',
        'max_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'input_column_reads': dict(arr.read_count), 'can_adopt': False, 'eligible_for_verdict': False,
        'population': exclusions,
    }
    flat = flatten(rows)
    comp = parity(flat)
    summary['previous_a2_point_stat_differences'] = comp['difference_count']
    save(OUT, 'summary.json', summary)
    save(OUT, 'scores.json', rows)
    save(OUT, 'survivors.json', {'ci_gt1': survivors, 'point_ge1': weak})
    save(OUT, 'near_misses.json', near)
    save(OUT, 'controls.json', ctl_rows)
    save(OUT, 'rejects.json', rejects)
    save(OUT, 'parity.json', comp)
    save(OUT, 'input_classification.json', classification)
    flat.to_parquet(OUT / 'scores.parquet', index=False)
    flat.to_csv(OUT / 'scores.csv', index=False)
    ev.log(f'done {json.dumps({k: summary[k] for k in ("n_patterns", "n_survivors_ci", "n_weak_point", "n_reached_C", "n_confirmed_holm", "elapsed_s", "max_rss_bytes", "previous_a2_point_stat_differences")})}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
