"""Replay frozen hand-defined patterns on features rebuilt from 2007 onward.

The input must contain 2007-truncated history, not merely date-filtered 1986
features. Historical C outcomes are already known: all findings are exploratory.
"""
from __future__ import annotations

# Import the prior bounded-memory implementation before numeric libraries so its
# one-thread environment and Arrow settings are applied before initialization.
import replay_patterns_20260930 as core

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import dsl
import evaluate as ev

REPO = Path(__file__).resolve().parents[2]
PATTERNS = REPO / 'artifacts/roi_explore/patterns'
OUT = REPO / 'artifacts/roi_strict2007_20260930/pattern_replay'
WINDOWS = {
    'market': {'D': (2007, 2012), 'Q': (2013, 2018), 'C': (2019, 2026)},
    'model': {'D': (2008, 2012), 'Q': (2013, 2018), 'C': (2019, 2026)},
    'exotic': {'D': (2025, 2025), 'C': (2026, 2026)},
}
CUTOFF = '2026-09-22'


def load_dividends(path):
    dv = pd.read_parquet(path)
    out = {}
    for bt, g in dv.groupby('bet_type'):
        table = {}
        for rid, sel, odds in zip(g['race_id'], g['selection'], g['odds']):
            table.setdefault(str(rid), {})[tuple(int(x) for x in json.loads(sel))] = float(odds)
        out[bt] = table
    return out


def required_fields(pats):
    fields = {'race_id', 'horse_number', 'race_date', 'race_ok', 'dead_heat', 'won', 'year', 'odds'}
    for p in pats:
        fields.update(c.field for c in p.race_filter + p.horse_filter)
        if p.select_by:
            fields.add(p.select_by)
        if p.stake == 'edge':
            fields.add('ev')
    return fields


def add_all_and_yearly(r, arr):
    """Return year records; add overall stats while keeping historical windows."""
    out = []
    first_year = 2025 if r['kind'] == 'exotic' else 2008 if r['kind'] == 'model' else 2007
    if r['kind'] != 'exotic':
        r['ALL'] = ev.window_stats(r['_stake'], r['_payout'], r['_idx'], arr, (first_year, 2026))
        for year in range(first_year, 2027):
            st = ev.window_stats(r['_stake'], r['_payout'], r['_idx'], arr, (year, year))
            out.append({'year': year, **{k: v for k, v in st.items() if not k.startswith('_') and k != 'yearly'}})
    else:
        bets = r['_bets']
        for year, window in ((2025, 'D'), (2026, 'C')):
            out.append({'year': year, **{k: v for k, v in r[window].items() if not k.startswith('_') and k != 'yearly'}})
        if bets is None or not len(bets['pay']):
            r['ALL'] = {'n_bets': 0}
        else:
            assert np.all((bets['years'] >= 2025) & (bets['years'] <= 2026)), 'Unexpected dividend years'
            active = [r[w] for w in ('D', 'C') if r[w].get('n_bets', 0)]
            pay = bets['pay']; stake = 100. * len(pay); psum = float(pay.sum()); mx = float(pay.max())
            yr = {str(y['year']): round(y['roi'], 4) if y.get('roi') is not None else None for y in out}
            r['ALL'] = {
                'n_bets': len(pay), 'n_races': sum(x['n_races'] for x in active),
                'n_days': len(np.unique(bets['days'])), 'n_hits': int((pay > 0).sum()),
                'stake': stake, 'payout': psum, 'roi': psum / stake,
                'max_hit_share': mx / psum if psum else None, 'loho_roi': (psum - mx) / stake,
                'yearly': yr, 'years_ge1': sum(v is not None and v >= 1 for v in yr.values()),
                'years_n': sum(v is not None for v in yr.values()), '_sel': np.arange(len(pay)),
            }
    return out


def score_and_record(p, arr, div):
    r = core.score(p, arr, div)
    yearly = add_all_and_yearly(r, arr)
    rec = core.record(p, r)
    rec['status'] = 'evaluated'
    for row in yearly:
        row.update(pattern_id=p.pattern_id, uses_model=p.uses_model, bet_type=p.bet_type, family=p.family)
    return r, rec, yearly


def flatten(rows):
    out = []
    for rec in rows:
        f = {k: v for k, v in rec.items() if k not in ('ALL', 'D', 'Q', 'C') and not k.endswith('_boot')}
        for w in ('ALL', 'D', 'Q', 'C'):
            st = rec.get(w, {})
            for key in ('n_bets', 'n_races', 'n_days', 'n_hits', 'stake', 'payout', 'roi', 'max_hit_share', 'loho_roi', 'years_ge1', 'years_n'):
                f[f'{w}_{key}'] = st.get(key)
            if f'{w}_boot' in rec:
                f[f'{w}_ci_lo'] = rec[f'{w}_boot']['ci_lo']; f[f'{w}_ci_hi'] = rec[f'{w}_boot']['ci_hi']
        out.append(f)
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rows', type=Path, required=True)
    ap.add_argument('--dividends', type=Path, default=REPO / 'artifacts/roi_explore/dividends.parquet')
    ap.add_argument('--provenance', type=Path, required=True)
    ap.add_argument('--model-provenance', choices=('verified', 'unverified'), required=True,
                    help='verified requires independently audited model training AND feature history from 2007 onward')
    ap.add_argument('--allow-documented-model', action='store_true',
                    help='Score unverified but documented-2007 models only in a separate non-strict reference analysis')
    ap.add_argument('--boot', type=int, default=2000)
    ap.add_argument('--ci-all', action='store_true', help='Also compute diagnostic C CIs for every evaluated rule')
    args = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'freeze.json').exists():
        raise RuntimeError('This append-only run is already frozen; never overwrite its artifacts.')
    assert args.rows.name == 'rows.parquet', 'Bounded-memory input loader expects rows.parquet'
    provenance = json.loads(args.provenance.read_text())
    assert provenance.get('strict2007_histories_rebuilt') is True, 'Rebuilt 2007 histories must be audited'
    assert provenance.get('history_start') == '2007-01-01', 'History boundary must be 2007-01-01'
    assert provenance.get('rows_sha256') == core.sha(args.rows), 'Rows do not match audited provenance'
    if args.model_provenance == 'verified' and provenance.get('model_patterns_allowed') is False:
        raise ValueError('Provenance explicitly forbids model-dependent evaluation')
    files = sorted(PATTERNS.glob('*.json'))
    pats, rejects = dsl.load_pattern_files(files)
    assert len(pats) == 2356 and not rejects
    controls = [dsl.parse_pattern(d, source='controls') for d in ev.CONTROLS]
    candidates = pats + controls
    evaluable = [p for p in candidates if not p.uses_model or args.model_provenance == 'verified' or args.allow_documented_model]
    def analysis_set(p):
        return 'strict' if not p.uses_model or args.model_provenance == 'verified' else 'documented_model_reference'
    blocked = [p for p in candidates if p not in evaluable]
    fields = required_fields(evaluable)
    available = set(pq.ParquetFile(args.rows).schema_arrow.names)
    assert fields <= available, f'Missing fields: {sorted(fields - available)}'
    # Read dates only for the boundary check; this cannot establish history provenance.
    dates = core.read_array(args.rows, 'race_date')
    assert len(dates) and min(dates) >= '2007-01-01' and max(dates) <= CUTOFF
    del dates
    inputs = files + [args.rows.resolve(), args.dividends.resolve(), args.provenance.resolve()]
    sources = [Path(dsl.__file__), Path(ev.__file__), Path(core.__file__), Path(__file__)]
    freeze = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'purpose': 'Existing fixed rules on rebuilt history starting in 2007; C is already known and this is exploratory.',
        'provenance_requirement': 'All feature history as well as stored/model training rows must start in 2007; filtering the 1986-derived feature table is prohibited.',
        'input_provenance': provenance, 'model_provenance_status': args.model_provenance,
        'n_patterns': len(pats), 'n_controls': len(controls), 'n_evaluable_including_controls': len(evaluable),
        'allow_documented_model_reference': args.allow_documented_model,
        'pattern_analysis_sets': {p.pattern_id: analysis_set(p) if p in evaluable else 'held_model_provenance' for p in candidates},
        'pattern_ids': [p.pattern_id for p in pats], 'held_pattern_ids': [p.pattern_id for p in blocked],
        'inputs_sha256': {str(p.relative_to(REPO)): core.sha(p) for p in inputs},
        'source_sha256': {str(p.relative_to(REPO)): core.sha(p) for p in sources},
        'windows': WINDOWS, 'date_cutoff': CUTOFF, 'degrade': ev.DEGRADE,
        'screen': 'win: pass D and Q; exotic: pass D. ROI>=1, hits>=20, days>=100, largest payout share<=0.5.',
        'near_misses': '40 by unchanged existing near-key definition, diagnostic only, excluded from Holm family unless already screen-pass.',
        'bootstrap_reps': args.boot, 'ci_seed': 20260923, 'test_seed': 20260924,
        'bootstrap_method': 'Existing day-cluster percentile CI and multiplicative null-centered p (payout/observed ROI).',
        'holm': 'Separate one-sided alpha .025 families for strict and documented-model-reference screen-pass rules having C bets. No pooled confirmatory claim.',
        'diagnostic_ci_all': args.ci_all, 'null_simulation': 'Not rerun', 'threads': 1,
        'settlement': 'Win=stored odds times stake; exotic=stored dividend. Verified official-win-dividend subset requires separate diagnostic.',
        'can_adopt': False, 'eligible_for_verdict': False,
    }
    core.save(OUT, 'freeze.json', freeze)
    core.save(OUT, 'dsl_forbidden_checks.json', core.dsl_checks())
    ev.WINDOWS = WINDOWS
    ev.ART = args.rows.parent
    ev._COVERAGE_CACHE.clear()
    ev.log('loading strict-2007 history input')
    arr, population = core.load_rows()
    assert min(arr['race_date']) >= '2007-01-01' and max(arr['race_date']) <= CUTOFF
    core.save(OUT, 'population.json', population)
    div = load_dividends(args.dividends)
    # Fast equivalent coverage computation, avoiding object-array np.isin's
    # O(rows*dividend-races) loop. Membership is independent of outcomes.
    for bt, table in div.items():
        ev._COVERAGE_CACHE[f"{bt}:{len(arr['race_id'])}"] = pd.Series(arr['race_id'], copy=False).isin(table).to_numpy()
    rows, years, ctl_rows = [], [], []
    pvals = {'strict': {}, 'documented_model_reference': {}}
    byid = {p.pattern_id: p for p in candidates}
    for i, p in enumerate(evaluable):
        r, rec, yearly = score_and_record(p, arr, div)
        group = analysis_set(p)
        rec.update(analysis_set=group, strict_eligible=group == 'strict')
        for year in yearly:
            year.update(analysis_set=group, strict_eligible=group == 'strict')
        if rec['screen_pass'] and r['C'].get('n_bets', 0) > 0:
            rec['C_boot'] = core.bootstrap(r, 'C', arr, args.boot)
            rec['confirm_point_ge1'] = bool(r['C']['roi'] >= 1)
            rec['confirm_ci_lo_gt1'] = bool(rec['C_boot']['ci_lo'] > 1)
            if p.family != 'control':
                if r['kind'] == 'exotic':
                    bets = r['_bets']
                    ds, dp = ev.day_aggregates(np.full(len(bets['pay']), 100.), bets['pay'], r['C']['_sel'], bets['days'])
                else:
                    ds, dp = ev.day_aggregates(r['_stake'], r['_payout'], r['C']['_sel'], arr['_day_idx'])
                rec['C_test'] = ev.centered_pvalue(ds, dp, b=args.boot)
                pvals[group][p.pattern_id] = rec['C_test']['p_one_sided']
        if args.ci_all and 'C_boot' not in rec and r['C'].get('n_bets', 0):
            rec['C_boot'] = core.bootstrap(r, 'C', arr, args.boot)
        if p.family == 'control':
            ctl = ev.strip(r)
            ctl.update(label_ja=p.label_ja, uses_model=p.uses_model, analysis_set=group, strict_eligible=group == 'strict')
            for w in ('ALL', 'D', 'Q', 'C'):
                if r.get(w, {}).get('n_bets', 0):
                    ctl[f'{w}_boot'] = core.bootstrap(r, w, arr, args.boot)
            ctl_rows.append(ctl)
        rows.append(rec); years.extend(yearly)
        del r
        if (i + 1) % 100 == 0:
            ev.log(f'scored {i + 1}/{len(evaluable)} elapsed={time.time()-t0:.1f}s')
    for p in blocked:
        rows.append({'pattern_id': p.pattern_id, 'family': p.family, 'label_ja': p.label_ja, 'bet_type': p.bet_type,
                     'uses_model': p.uses_model, 'source': p.source, 'status': 'held_model_provenance',
                     'analysis_set': 'held_model_provenance', 'strict_eligible': False,
                     'reason': 'Strict-2007 model training/feature-history provenance not verified; no ROI computed.'})
    hm = {group: ev.holm(pvs) for group, pvs in pvals.items()}
    for rec in rows:
        group = rec['analysis_set']
        if rec['pattern_id'] in hm.get(group, {}):
            rec['confirm_holm'] = hm[group][rec['pattern_id']]
    researched = [r for r in rows if r['family'] != 'control' and r['status'] == 'evaluated']
    near_sets = {}
    for group in ('strict', 'documented_model_reference'):
        near = sorted([r for r in researched if r['analysis_set'] == group], key=core.near_key, reverse=True)[:40]
        for rec in near:
            if 'C_boot' not in rec and rec.get('C', {}).get('n_bets', 0):
                r = core.score(byid[rec['pattern_id']], arr, div)
                rec['C_boot'] = core.bootstrap(r, 'C', arr, args.boot)
                del r
        near_sets[group] = near
    hashes = {rel: core.sha(REPO / rel) == val for rel, val in freeze['inputs_sha256'].items() | freeze['source_sha256'].items()}
    assert all(hashes.values()), 'Frozen source/input changed during evaluation'
    core.save(OUT, 'integrity.json', {'all_unchanged': True, 'checks': hashes})
    core.save(OUT, 'held_patterns.json', [r for r in rows if r['status'] != 'evaluated'])
    core.save(OUT, 'rejects.json', rejects)
    for group in ('strict', 'documented_model_reference'):
        group_rows = [r for r in rows if r['analysis_set'] == group]
        if not group_rows:
            continue
        group_researched = [r for r in group_rows if r['family'] != 'control']
        group_controls = [r for r in ctl_rows if r['analysis_set'] == group]
        group_years = [r for r in years if r['analysis_set'] == group]
        survivors = [r for r in group_researched if r.get('confirm_ci_lo_gt1')]
        weak = [r for r in group_researched if r.get('confirm_point_ge1') and not r.get('confirm_ci_lo_gt1')]
        group_out = OUT if group == 'strict' else OUT / 'model_reference'
        group_out.mkdir(parents=True, exist_ok=True)
        summary = {
            'analysis_set': group, 'strict_eligible': group == 'strict',
            'n_patterns_defined_total': len(pats), 'n_patterns': len(group_researched),
            'n_evaluated_patterns': len(group_researched),
            'n_held_patterns_global': sum(p.family != 'control' for p in blocked),
            'n_documented_reference_patterns_global': sum(r['analysis_set'] == 'documented_model_reference' for r in researched),
            'n_controls': len(group_controls), 'n_rejected': len(rejects),
            'stage_counts': Counter(r['stage'] for r in group_researched),
            'stage_reasons': Counter(r['stage_reason'] for r in group_researched),
            'n_reached_C': len(pvals[group]), 'n_survivors_ci': len(survivors), 'n_weak_point': len(weak),
            'n_confirmed_holm': sum(hm[group].values()), 'windows': WINDOWS, 'date_cutoff': CUTOFF,
            'by_kind': Counter(r['kind'] for r in group_researched),
            'by_bet_type': Counter(r['bet_type'] for r in group_researched),
            'by_source': Counter(r['source'] for r in group_researched), 'population': population,
            'by_model_use': {str(flag): {'n_patterns': sum(r['uses_model'] == flag for r in group_researched),
                'n_screen_pass': sum(r['uses_model'] == flag and r['screen_pass'] for r in group_researched),
                'n_confirm_point_ge1': sum(r['uses_model'] == flag and r.get('confirm_point_ge1', False) for r in group_researched),
                'n_confirm_ci_lo_gt1': sum(r['uses_model'] == flag and r.get('confirm_ci_lo_gt1', False) for r in group_researched)} for flag in (False, True)},
            'bootstrap_reps': args.boot, 'elapsed_s': round(time.time()-t0, 1),
            'max_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'settlement_caveat': 'Win ROI uses stored odds, not independently verified official payout; interpretation is provisional.',
            'history_caveat': 'Strict inputs independently rebuilt from 2007; reference model documentation/code indicate 2007 but effective historical environment is not recorded.',
            'can_adopt': False, 'eligible_for_verdict': False,
        }
        core.save(group_out, 'summary.json', summary)
        core.save(group_out, 'scores.json', group_rows)
        core.save(group_out, 'yearly.json', group_years)
        core.save(group_out, 'survivors.json', {'ci_gt1': survivors, 'point_ge1': weak})
        core.save(group_out, 'controls.json', group_controls)
        core.save(group_out, 'near_misses.json', near_sets[group])
        flat = flatten(group_rows)
        flat.to_parquet(group_out / 'scores.parquet', index=False); flat.to_csv(group_out / 'scores.csv', index=False)
        pd.DataFrame(group_years).to_parquet(group_out / 'yearly.parquet', index=False); pd.DataFrame(group_years).to_csv(group_out / 'yearly.csv', index=False)
        ev.log(f'done {group}: {json.dumps({k:summary[k] for k in ("n_evaluated_patterns", "n_reached_C", "n_survivors_ci", "n_weak_point", "n_confirmed_holm", "elapsed_s")})}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
