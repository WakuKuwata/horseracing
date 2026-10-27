"""Independent scalar accounting of saved152/153; no fitting or new policy scoring."""
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REPORT = ROOT / 'artifacts/roi-after-152-153-20261025/analysis.json'
P152 = ROOT / 'artifacts/152-normalized-pace/comparison'
P153 = ROOT / 'artifacts/153-market-three-column-ablation/comparison'
LOC = {'baseline': (P153, 'baseline'), 'gamma': (P153, 'weighted_baseline'),
       'full': (P153, 'normalized_weighted_baseline'), 'pace_raw': (P152, 'pace'),
       'pace_full': (P152, 'normalized_pace'), 'drop3_full': (P153, 'normalized_market_drop3')}
PERIODS = ['all', 'before2026', '2026'] + [str(y) for y in range(2019, 2026)]
CATS = ['added', 'removed', 'common_stake_changed', 'common_same_stake', 'neither']
CHECKS = 0


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(8 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def checked(a, b, path):
    global CHECKS
    if isinstance(a, dict):
        assert a.keys() == b.keys(), (path, a.keys(), b.keys())
        for key in a:
            checked(a[key], b[key], path + '/' + key)
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, value in enumerate(a):
            checked(value, b[i], path + '/' + str(i))
    elif isinstance(a, (int, float)) and not isinstance(a, bool):
        assert math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7), (path, a, b)
        CHECKS += 1
    else:
        assert a == b, (path, a, b)


def periods(year):
    return ['all', 'before2026', str(year)] if year < 2026 else ['all', '2026']


def accumulator():
    return dict(rows=0, winner_rows=0, cs=[], rs=[], cp=[], rp=[], cb=0, rb=0)


def add(d, won, odds, ca, re):
    d['rows'] += 1
    d['winner_rows'] += int(won)
    d['cs'].append(ca)
    d['rs'].append(re)
    d['cp'].append(ca * won * odds)
    d['rp'].append(re * won * odds)
    d['cb'] += int(ca > 0)
    d['rb'] += int(re > 0)


def finish(d):
    out = dict(rows=d['rows'], winner_rows=d['winner_rows'])
    for name, prefix in [('candidate', 'c'), ('reference', 'r')]:
        stake = math.fsum(d[prefix + 's'])
        payout = math.fsum(d[prefix + 'p'])
        out[name] = dict(n_bets=d[prefix + 'b'], stake_yen=stake,
                         payout_yen=payout, profit_yen=payout-stake,
                         roi=payout/stake if stake else None)
    out['delta_yen'] = out['candidate']['profit_yen'] - out['reference']['profit_yen']
    return out


def main():
    report = json.loads(REPORT.read_text())
    hashes = {}
    for name, digest in report['source_sha256'].items():
        assert sha(ROOT/name) == digest, name
    for study in ['152-normalized-pace', '153-market-three-column-ablation']:
        folder = ROOT/'artifacts'/study
        proto = json.loads((folder/'protocol.json').read_text())
        assert sha(folder/'protocol.json') == (folder/'protocol.sha256').read_text().strip()
        audit = json.loads((ROOT/'specs'/study/'evidence/independent_final.json').read_text())
        assert not audit['blockers']
        assert audit['protocol_sha256'] == sha(folder/'protocol.json')
        assert audit['summary_sha256'] == sha(folder/'comparison/summary.json')
        for name, digest in proto['inputs_and_source_sha256'].items():
            if name not in hashes:
                assert sha(ROOT/name) == digest, name
                hashes[name] = digest
            else:
                assert hashes[name] == digest
    frames, stakes, masses = {}, {}, {}
    for tag, (folder, arm) in LOC.items():
        data = pd.read_parquet(folder/f'{arm}_ensemble.parquet')
        frame = {(str(r.race_id), str(r.horse_id)): r for r in data.itertuples()}
        tickets = pd.read_parquet(folder/f'{arm}_bets.parquet')
        s = {(str(r.race_id), str(r.horse_id)): float(r.stake_yen) for r in tickets.itertuples()}
        assert len(frame) == 343883 and len(s) == len(tickets)
        assert len(set(r.race_id for r in frame.values())) == 24855
        assert len(set(r.race_date for r in frame.values())) == 807
        mass_lists = defaultdict(list)
        for r in frame.values():
            mass_lists[str(r.race_id)].append(float(r.p))
        frames[tag], stakes[tag] = frame, s
        masses[tag] = {k: math.fsum(v) for k, v in mass_lists.items()}
        if tag != 'baseline':
            assert frame.keys() == frames['baseline'].keys()
            for k, row in frame.items():
                ref = frames['baseline'][k]
                assert (row.race_date, row.year, row.odds, row.won, row.field_size, row.horse_number) == (
                    ref.race_date, ref.year, ref.odds, ref.won, ref.field_size, ref.horse_number)
        selected = {p: [] for p in PERIODS}
        for key, r in frame.items():
            stake = s.get(key, 0.)
            if stake:
                for period in periods(int(r.year)):
                    selected[period].append((float(r.p), int(r.won), stake, float(r.odds)))
        diagnostics = {}
        for period, rows in selected.items():
            hits = sum(r[1] for r in rows)
            pw = math.fsum(r[0] for r in rows)
            stake = math.fsum(r[2] for r in rows)
            payout = math.fsum(r[2]*r[1]*r[3] for r in rows)
            predicted = math.fsum(r[2]*r[0]*r[3] for r in rows)
            diagnostics[period] = dict(n_bets=len(rows), hits=hits, sum_predicted_wins=pw,
                observed_over_predicted_wins=hits/pw, stake_yen=stake, profit_yen=payout-stake,
                predicted_payout_yen=predicted, predicted_roi=predicted/stake, observed_roi=payout/stake)
        checked(diagnostics, report['selected_diagnostics'][tag], 'selected/'+tag)
    for name, target in report['attribution'].items():
        ca, re = name.split('_minus_')
        sums = {p: {c: accumulator() for c in CATS} for p in PERIODS}
        totals = {p: accumulator() for p in PERIODS}
        races = {p: defaultdict(list) for p in PERIODS}
        for key, row in frames[re].items():
            c, r = stakes[ca].get(key, 0.), stakes[re].get(key, 0.)
            cat = ('neither' if c == r == 0 else 'added' if r == 0 else 'removed' if c == 0
                   else 'common_same_stake' if c == r else 'common_stake_changed')
            for period in periods(int(row.year)):
                add(sums[period][cat], row.won, row.odds, c, r)
                add(totals[period], row.won, row.odds, c, r)
                races[period][str(row.race_id)].append((c-r)*(row.won*row.odds-1))
        for period in PERIODS:
            expected = target[period]
            groups = {c: finish(d) for c, d in sums[period].items() if d['rows']}
            checked(groups, expected['groups'], name+'/'+period+'/groups')
            checked(finish(totals[period]), expected['total'], name+'/'+period+'/total')
            by_race = {k: math.fsum(v) for k, v in races[period].items()}
            checked(math.fsum(v for k,v in by_race.items() if k != '202608030111'),
                    expected['without_known_race_delta_yen'], name+'/'+period+'/known')
            for side in ['top_negative', 'top_positive']:
                ordered = sorted(by_race.values(), reverse=side == 'top_positive')[:10]
                checked(ordered, [r['delta_yen'] for r in expected[side]], name+'/'+period+'/'+side+'/values')
                for row in expected[side]:
                    checked(by_race[row['race_id']], row['delta_yen'], name+'/'+period+'/'+side+'/race')
    for ca in ['pace_full', 'drop3_full']:
        target = report['fixed_reference_ev_winner_nll'][ca+'_minus_full']
        entries = {p: defaultdict(list) for p in PERIODS}
        for key, row in frames['full'].items():
            if not row.won:
                continue
            ev = row.p * row.odds
            band = 'ev_le1' if ev <= 1 else 'ev_1_12' if ev <= 1.2 else 'ev_gt12'
            c = frames[ca][key]
            d = math.log(row.p/masses['full'][str(row.race_id)]) - math.log(c.p/masses[ca][str(row.race_id)])
            for period in periods(int(row.year)):
                entries[period][band].append(d)
        out = {}
        for period, groups in entries.items():
            count = sum(len(v) for v in groups.values())
            out[period] = dict(total_races=count,
                delta_winner_nll=math.fsum(math.fsum(v) for v in groups.values())/count,
                groups={k: dict(winners=len(v), mean_nll_delta=math.fsum(v)/len(v),
                    contribution_to_overall_nll=math.fsum(v)/count) for k,v in groups.items()})
        checked(out, target, 'fixedNLL/'+ca)
    for name, digest in report['source_sha256'].items():
        assert sha(ROOT/name) == digest
    result = dict(status='passed', blockers=[], new_fits=0, new_policies_scored=0,
        numeric_values_checked=CHECKS, analysis_sha256=sha(REPORT),
        independent_script_sha256=sha(Path(__file__)),
        frozen_input_source_sha256_count=len(hashes), source_sha256_count=len(report['source_sha256']),
        checks=['Six contrasts: full/year/pre2026/2026 added/removed/common allocation accounting',
                'Known race exclusion and top10 positive/negative race difference values',
                'Six existing selection sets: predicted/observed wins, payout, ROI, profit by period',
                'Two models fixed-reference winner-NLL EV strata contributions and aggregate loss',
                'Both protocols, independent final receipts, and all frozen source/input hashes'],
        caveat='Descriptive saved-bet accounting only; no adoption or new-policy evaluation.')
    (HERE/'independent_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
