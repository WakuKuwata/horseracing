"""Independent scalar replay of already saved purchase sets and mass statistics."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import math
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ART = ROOT / 'artifacts/roi-next-proposals-20261025'
FILES = {
    'baseline': ('143-existing-feature-roi/weighted_comparison', 'baseline'),
    'weighted_baseline': ('143-existing-feature-roi/weighted_comparison', 'weighted_baseline'),
    'pace': ('143-existing-feature-roi/features_comparison', 'pace'),
    'within_race': ('144-race-normalized-weight/comparison', 'within_race'),
    'race_only': ('144-race-normalized-weight/comparison', 'race_only'),
    'weighted_pace': ('145-pace-weight-combination/comparison', 'weighted_pace'),
    'weighted_backfill': ('146-old-oof-backfill/comparison', 'weighted_backfill'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b, tol=1e-8):
    if a is None or b is None:
        assert a is b
    else:
        assert math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol), (a, b)


def main():
    attribution_path, mass_path = ART/'attribution.json', ART/'probability_mass.json'
    expected = json.loads(attribution_path.read_text())
    masses_expected = json.loads(mass_path.read_text())
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in [attribution_path, mass_path, Path(__file__)]}
    for source, digest in expected['source_sha256'].items():
        assert sha(ROOT/source) == digest
    for source, digest in masses_expected['input_sha256'].items():
        assert sha(ROOT/source) == digest
    predictions, stakes, mass_results = {}, {}, {}
    for arm, (directory, prefix) in FILES.items():
        pred_path = ROOT/'artifacts'/directory/f'{prefix}_ensemble.parquet'
        bet_path = ROOT/'artifacts'/directory/f'{prefix}_bets.parquet'
        hashes.update({str(p.relative_to(ROOT)): sha(p) for p in [pred_path, bet_path]})
        pred, bets = pd.read_parquet(pred_path), pd.read_parquet(bet_path)
        predictions[arm] = pred
        keys = list(zip(pred.race_id, pred.horse_id))
        assert len(set(keys)) == 343883
        stakes[arm] = {(r.race_id, r.horse_id): float(r.stake_yen) for r in bets.itertuples()}
        by_race = defaultdict(list)
        selected_races = []
        for row in pred.itertuples():
            by_race[row.race_id].append(float(row.p))
            if row.p*row.odds > 1.2:
                selected_races.append(row.race_id)
        race_sum = {k: math.fsum(v) for k,v in by_race.items()}
        values = np.array(list(race_sum.values()))
        selected_mass = np.array([race_sum[k] for k in selected_races])
        got = dict(races=len(values), mean=float(values.mean()), median=float(np.median(values)),
            p01=float(np.quantile(values,.01)), p99=float(np.quantile(values,.99)),
            fraction_outside_0_95_1_05=float(np.mean((values<.95)|(values>1.05))),
            selected_horses_in_mass_above_one_fraction=float(np.mean(selected_mass>1)),
            selected_mean_mass=float(selected_mass.mean()))
        for k,v in got.items():
            close(v, masses_expected['results'][arm][k], 1e-14)
        mass_results[arm] = got
    verified = {}
    for contrast, record in expected['contrasts'].items():
        new_name, old_name = contrast.split('_minus_')
        new, old = predictions[new_name], predictions[old_name]
        pd.testing.assert_frame_equal(new.drop(columns='p'), old.drop(columns='p'), check_exact=True)
        groups, annual = {}, defaultdict(list)
        per_row_delta = []
        for row in new.itertuples():
            key = row.race_id, row.horse_id
            ns, os = stakes[new_name].get(key, 0.), stakes[old_name].get(key, 0.)
            if ns and not os:
                label = 'added'
            elif os and not ns:
                label = 'removed'
            elif ns and os:
                label = 'common_same_stake' if ns == os else 'common_stake_changed'
            else:
                label = 'neither'
            g = groups.setdefault(label, dict(n=0,wins=0,stake_new=[],stake_old=[],profit_new=[],profit_old=[]))
            unit_return = float(row.won)*row.odds-1
            g['n'] += 1
            g['wins'] += int(row.won)
            for k,v in [('stake_new',ns),('stake_old',os),('profit_new',ns*unit_return),('profit_old',os*unit_return)]:
                g[k].append(v)
            delta = (ns-os)*unit_return
            annual[str(row.year)].append(delta)
            per_row_delta.append((row.race_id,row.year,delta))
        for label,g in groups.items():
            for k in ['stake_new','stake_old','profit_new','profit_old']:
                g[k] = math.fsum(g[k])
            g['group'] = label
            g['delta_profit'] = g['profit_new']-g['profit_old']
            g['roi_new'] = (g['profit_new']+g['stake_new'])/g['stake_new'] if g['stake_new'] else None
            g['roi_old'] = (g['profit_old']+g['stake_old'])/g['stake_old'] if g['stake_old'] else None
        for g in record['groups']:
            for k,v in g.items():
                if k != 'group':
                    close(groups[g['group']][k],v)
        close(math.fsum(x[2] for x in per_row_delta),record['total_delta_yen'])
        close(math.fsum(x[2] for x in per_row_delta if x[1]<2026),record['before2026_delta_yen'])
        close(math.fsum(x[2] for x in per_row_delta if x[0]!='202608030111'),record['without_known_race_delta_yen'])
        for year,values in annual.items():
            close(math.fsum(values),record['annual'][year])
        delta_by_group = {k:g['delta_profit'] for k,g in groups.items()}
        close(math.fsum(delta_by_group.values()),record['total_delta_yen'])
        verified[contrast] = dict(total_delta_yen=record['total_delta_yen'],delta_by_group=delta_by_group)
    for name,digest in hashes.items():
        assert sha(ROOT/name) == digest
    result = dict(status='passed',blockers=[],new_fits=0,new_policies_scored=0,
        checked=['all seven fixed purchase contrasts: every group n/wins/stake/profit/delta/ROI; annual/sensitivity/total sums',
                 'seven-arm independent race probability mass: all reported means/quantiles/fractions',
                 'root attribution and probability mass source hashes'],
        contrasts=verified,mass_results=mass_results,input_sha256=hashes)
    (HERE/'independent_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(status='passed', contrasts=len(verified), mass_arms=len(mass_results))))


if __name__ == '__main__':
    main()
