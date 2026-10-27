"""Independent descriptive accounting of frozen149/150; never evaluate new policies."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PERIODS = ['all', 'before2026', '2026'] + [str(y) for y in range(2019, 2026)]
CATEGORIES = ['added', 'removed', 'common_stake_changed', 'common_same_stake', 'neither']


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def periods(year):
    return ['all', 'before2026', str(year)] if year < 2026 else ['all', '2026']


def money(stake=0., payout=0.):
    return dict(stake_yen=stake, payout_yen=payout, profit_yen=payout-stake)


def check_hashes():
    checks, bound = {}, {}
    for study in ('148-postprediction-normalization', '149-half-normalization', '150-flat-stake'):
        base = ROOT / 'artifacts' / study
        proto = read(base/'protocol.json')
        assert sha(base/'protocol.json') == (base/'protocol.sha256').read_text().strip()
        for rel, digest in proto['inputs_and_source_sha256'].items():
            if rel in bound:
                assert bound[rel] == digest, rel
            else:
                assert sha(ROOT/rel) == digest, rel
                bound[rel] = digest
        evidence = ROOT/'specs'/study/'evidence'
        if study.startswith(('149', '150')):
            audit = read(evidence/'independent_final.json')
            assert audit['protocol_sha256'] == sha(base/'protocol.json')
            assert audit['summary_sha256'] == sha(base/'comparison/summary.json')
            assert audit['audit_script_sha256'] == sha(evidence/'independent_audit.py')
            assert not audit['blockers']
            assert audit['independent143_helpers_sha256'] == sha(ROOT/'specs/143-existing-feature-roi/evidence/independent_audit.py')
        checks[study] = dict(protocol_sha256=sha(base/'protocol.json'), input_source_count=len(proto['inputs_and_source_sha256']), summary_sha256=sha(base/'comparison/summary.json'))
    return dict(protocols=checks, unique_frozen_input_source_hashes_checked=len(bound))


def data(study, arm):
    base = ROOT/'artifacts'/study/'comparison'
    frame = pd.read_parquet(base/f'{arm}_ensemble.parquet')
    tickets = pd.read_parquet(base/f'{arm}_bets.parquet')
    stakes = {(str(r.race_id), str(r.horse_id)): float(r.stake_yen) for r in tickets.itertuples()}
    assert len(stakes) == len(tickets)
    return frame, stakes


def diff(study, newarm, oldarm):
    old, os = data(study, oldarm)
    new, ns = data(study, newarm)
    keys = ['race_id', 'horse_id', 'race_date', 'year', 'odds', 'won']
    assert old[keys].equals(new[keys])
    result = {p: {c: dict(horses=0, winner_rows=0, old_n_bets=0, new_n_bets=0, old_stake_yen=0., old_payout_yen=0., old_profit_yen=0., new_stake_yen=0., new_payout_yen=0., new_profit_yen=0., delta_profit_yen=0.) for c in CATEGORIES} for p in PERIODS}
    by_race = defaultdict(float)
    for row in old.itertuples():
        key = (str(row.race_id), str(row.horse_id))
        a, b = os.get(key, 0.), ns.get(key, 0.)
        if a == b == 0:
            cat = 'neither'
        elif a == 0:
            cat = 'added'
        elif b == 0:
            cat = 'removed'
        elif a != b:
            cat = 'common_stake_changed'
        else:
            cat = 'common_same_stake'
        ap, bp = a*row.won*row.odds, b*row.won*row.odds
        delta = (bp-b)-(ap-a)
        by_race[str(row.race_id)] += delta
        for period in periods(int(row.year)):
            x = result[period][cat]
            x['horses'] += 1
            x['winner_rows'] += int(row.won)
            x['old_n_bets'] += int(a > 0)
            x['new_n_bets'] += int(b > 0)
            for name, value in [('old_stake_yen', a), ('old_payout_yen', ap), ('old_profit_yen', ap-a), ('new_stake_yen', b), ('new_payout_yen', bp), ('new_profit_yen', bp-b), ('delta_profit_yen', delta)]:
                x[name] += value
    for period in PERIODS:
        result[period]['total'] = {k: sum(result[period][c][k] for c in CATEGORIES) for k in result[period][CATEGORIES[0]]}
    return dict(periods=result, strongest_positive_races=sorted(by_race.items(), key=lambda x: x[1], reverse=True)[:10], strongest_negative_races=sorted(by_race.items(), key=lambda x: x[1])[:10])


def flat_accounting(parent):
    study = '150-flat-stake'
    old, os = data(study, parent)
    new, ns = data(study, 'flat_'+parent)
    assert old.equals(new)
    assert set(os) == set(ns)
    assert set(ns.values()) == {100.}
    agg = {p: dict(old_stake=0., old_payout=0., new_stake=0., new_payout=0.) for p in PERIODS}
    for r in old.itertuples():
        k = (str(r.race_id), str(r.horse_id))
        a, b = os.get(k, 0.), ns.get(k, 0.)
        for p in periods(int(r.year)):
            x = agg[p]
            x['old_stake'] += a
            x['new_stake'] += b
            x['old_payout'] += a*r.won*r.odds
            x['new_payout'] += b*r.won*r.odds
    for x in agg.values():
        a, b, c, d = (x[k] for k in ['old_stake', 'old_payout', 'new_stake', 'new_payout'])
        old_profit, new_profit = b-a, d-c
        scale = old_profit*(c/a-1)
        composition = c*(d/c-b/a)
        assert abs(scale+composition-(new_profit-old_profit)) < 1e-6
        x.update(old_profit=old_profit, new_profit=new_profit, delta_profit=new_profit-old_profit, old_roi=b/a, new_roi=d/c, stake_ratio=c/a, scale_component_yen=scale, composition_component_yen=composition)
    return agg


def count_composition():
    base = ROOT/'artifacts/150-flat-stake/comparison'
    arm = 'normalized_weighted_baseline'
    old = pd.read_parquet(base/f'{arm}_bets.parquet')
    flat = pd.read_parquet(base/f'flat_{arm}_bets.parquet')
    race_tickets = defaultdict(list)
    flat_tickets = {(r.race_id, r.horse_id): r for r in flat.itertuples()}
    for r in old.itertuples():
        race_tickets[r.race_id].append(r)
    counts = {p: {str(n): dict(races=0, tickets=0, old_stake_yen=0., old_payout_yen=0., flat_stake_yen=0., flat_payout_yen=0.) for n in range(11)} for p in PERIODS}
    population = pd.read_parquet(base/f'{arm}_ensemble.parquet')
    seen = set()
    for row in population.itertuples():
        if row.race_id not in seen:
            seen.add(row.race_id)
            if row.race_id not in race_tickets:
                for p in periods(int(row.year)):
                    counts[p]['0']['races'] += 1
    for race, rows in race_tickets.items():
        n = len(rows)
        assert 1 <= n <= 10
        for r in rows:
            f = flat_tickets[(r.race_id, r.horse_id)]
            for p in periods(int(r.race_date[:4])):
                x = counts[p][str(n)]
                x['tickets'] += 1
                x['old_stake_yen'] += r.stake_yen
                x['old_payout_yen'] += r.payout_yen
                x['flat_stake_yen'] += f.stake_yen
                x['flat_payout_yen'] += f.payout_yen
        for p in periods(int(rows[0].race_date[:4])):
            counts[p][str(n)]['races'] += 1
    for period in counts.values():
        totals = {f: sum(v[f] for v in period.values()) for f in ['old_stake_yen', 'flat_stake_yen']}
        for x in period.values():
            for tag in ['old', 'flat']:
                stake, payout = x[tag+'_stake_yen'], x[tag+'_payout_yen']
                x[tag+'_profit_yen'] = payout-stake
                x[tag+'_roi'] = payout/stake if stake else None
                x[tag+'_stake_share'] = stake/totals[tag+'_stake_yen']
    return counts


def cross_check_primary(result):
    path = ROOT/'artifacts/roi-after-149-150-20261025/analysis.json'
    primary = read(path)
    checked = 0
    def eq(a, b):
        nonlocal checked
        if a is None or b is None:
            assert a is b, (a, b)
        else:
            assert abs(a-b) <= 1e-6, (a, b)
        checked += 1
    for period, groups in result['half_minus_full']['periods'].items():
        for cat, x in groups.items():
            y = primary['half_minus_full'][period]['total'] if cat == 'total' else primary['half_minus_full'][period]['groups'][cat]
            eq(x['horses'], y['rows'])
            eq(x['winner_rows'], y['winner_rows'])
            eq(x['delta_profit_yen'], y['delta_yen'])
            for own, peer in [('old', 'reference'), ('new', 'candidate')]:
                eq(x[own+'_n_bets'], y[peer]['n_bets'])
                for field in ['stake_yen', 'payout_yen', 'profit_yen']:
                    eq(x[own+'_'+field], y[peer][field])
                s, p = x[own+'_stake_yen'], x[own+'_payout_yen']
                eq(p/s if s else None, y[peer]['roi'])
    keymap = dict(old_stake='stake_old', new_stake='stake_new', old_profit='profit_old', new_profit='profit_new', delta_profit='profit_delta', old_roi='roi_old', new_roi='roi_new')
    for arm, peerarm in [('weighted_baseline','gamma'), ('normalized_weighted_baseline','full')]:
        for period, x in result['flat_accounting'][arm].items():
            y = primary['flat_accounting'][peerarm][period]
            for key in ['old_stake', 'new_stake', 'old_profit', 'new_profit', 'delta_profit', 'old_roi', 'new_roi', 'stake_ratio', 'scale_component_yen', 'composition_component_yen']:
                eq(x[key], y[keymap.get(key, key)])
    for period, counts in result['normalized_ticket_count_composition'].items():
        for n, x in counts.items():
            y = primary['normalized_ticket_count_composition'][period].get(n)
            if y is None:
                assert x['races'] == x['tickets'] == x['old_stake_yen'] == x['flat_stake_yen'] == 0
                y = dict(races=0, n_bets=0, stake_old=0., stake_new=0., profit_old=0., profit_new=0., roi_old=None, roi_new=None, stake_share_old=0., stake_share_new=0.)
            for own, peer in [('races','races'), ('tickets','n_bets'), ('old_stake_yen','stake_old'), ('flat_stake_yen','stake_new'), ('old_profit_yen','profit_old'), ('flat_profit_yen','profit_new'), ('old_roi','roi_old'), ('flat_roi','roi_new'), ('old_stake_share','stake_share_old'), ('flat_stake_share','stake_share_new')]:
                eq(x[own], y[peer])
    pace_path = ROOT/'artifacts/143-existing-feature-roi/features_comparison/pace_ensemble.parquet'
    frame = pd.read_parquet(pace_path)
    sums = defaultdict(float)
    for row in frame.itertuples():
        sums[row.race_id] += row.p
    masses = list(sums.values())
    mean = sum(masses)/len(masses)
    diag = dict(rows=len(frame), races=len(masses), mass_mean=mean, mass_sd=math.sqrt(sum((s-mean)**2 for s in masses)/(len(masses)-1)), mass_quantiles={str(q):float(np.quantile(masses,q)) for q in [.01,.25,.5,.75,.99]}, outside_095_105_fraction=sum(s<.95 or s>1.05 for s in masses)/len(masses))
    for key in ['rows', 'races', 'mass_mean', 'mass_sd', 'outside_095_105_fraction']:
        eq(diag[key], primary['pace_mass_diagnostic'][key])
    for q, value in diag['mass_quantiles'].items():
        eq(value, primary['pace_mass_diagnostic']['mass_quantiles'][q])
    for rel, digest in primary['source_sha256'].items():
        assert sha(ROOT/rel) == digest, rel
    assert primary['new_fits'] == primary['new_policies_scored'] == 0
    return dict(primary_analysis_sha256=sha(path), primary_script_sha256=sha(HERE/'analyze.py'), matched_values=checked, primary_source_hashes_checked=len(primary['source_sha256']), pace_mass_independent=diag, pace_ensemble_sha256=sha(pace_path), mismatch_count=0)


def main():
    result = dict(method='Independent saved-ticket accounting, dictionary loops; no new policy or model evaluation', hashes=check_hashes(), half_minus_full=diff('149-half-normalization', 'half_normalized_weighted_baseline', 'normalized_weighted_baseline'), flat_accounting={p: flat_accounting(p) for p in ['weighted_baseline', 'normalized_weighted_baseline']}, normalized_ticket_count_composition=count_composition(), new_fits=0, new_policy_scores=0, can_adopt=False, script_sha256=sha(Path(__file__)))
    result['primary_cross_check'] = cross_check_primary(result)
    target = HERE/'independent_verify.json'
    with target.open('w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(dict(hashes=result['hashes'], half_minus_full_all=result['half_minus_full']['periods']['all'], half_minus_full_2026=result['half_minus_full']['periods']['2026'], flat_accounting_all={p: v['all'] for p,v in result['flat_accounting'].items()}), indent=2))


if __name__ == '__main__':
    main()
