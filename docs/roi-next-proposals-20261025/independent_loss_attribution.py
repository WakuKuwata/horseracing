"""Read-only decomposition of saved forecast losses; no new policies or fits."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name('independent_loss_attribution.json')
FILES = {
    'baseline': '143-existing-feature-roi/weighted_comparison/baseline_ensemble.parquet',
    'weighted_baseline': '143-existing-feature-roi/weighted_comparison/weighted_baseline_ensemble.parquet',
    'within_race': '144-race-normalized-weight/comparison/within_race_ensemble.parquet',
    'race_only': '144-race-normalized-weight/comparison/race_only_ensemble.parquet',
    'pace': '145-pace-weight-combination/comparison/pace_ensemble.parquet',
    'weighted_pace': '145-pace-weight-combination/comparison/weighted_pace_ensemble.parquet',
    'weighted_backfill': '146-old-oof-backfill/comparison/weighted_backfill_ensemble.parquet',
}


def main():
    frames = {a: pd.read_parquet(ROOT / 'artifacts' / f) for a, f in FILES.items()}
    base = frames['baseline']
    meta = base.drop(columns='p')
    assert len(base) == 343883 and base.race_id.nunique() == 24855
    assert base.race_date.nunique() == 807 and int(base.won.sum()) == 24855
    for x in frames.values():
        pd.testing.assert_frame_equal(x.drop(columns='p'), meta, check_exact=True)
    won = base.won.to_numpy(dtype=bool)
    base_ev = base.p.to_numpy() * base.odds.to_numpy()
    odds = base.odds.to_numpy()
    partitions = {
        'baseline_raw_ev': {
            'le_1': base_ev <= 1,
            'gt_1_le_1p2': (base_ev > 1) & (base_ev <= 1.2),
            'gt_1p2': base_ev > 1.2,
        },
        'odds': {
            'lt_5': odds < 5,
            'ge_5_lt_20': (odds >= 5) & (odds < 20),
            'ge_20_lt_100': (odds >= 20) & (odds < 100),
            'ge_100': odds >= 100,
        },
    }
    losses = {}
    for a, f in frames.items():
        p = f.p.to_numpy()
        assert np.isfinite(p).all() and ((p > 0) & (p < 1)).all()
        pn = p / f.groupby('race_id').p.transform('sum').to_numpy()
        losses[a] = {
            'winner_nll': -np.log(pn),
            'binary_logloss': -np.where(won, np.log(p), np.log1p(-p)),
        }
    contrasts = [
        ('weighted_baseline', 'baseline'),
        ('within_race', 'baseline'),
        ('within_race', 'weighted_baseline'),
        ('pace', 'baseline'),
        ('weighted_pace', 'baseline'),
        ('weighted_pace', 'pace'),
        ('weighted_backfill', 'baseline'),
        ('weighted_backfill', 'weighted_baseline'),
    ]
    result = {}
    for a, b in contrasts:
        details = {}
        for metric in ['winner_nll', 'binary_logloss']:
            eligible = won if metric == 'winner_nll' else np.ones(len(base), dtype=bool)
            denominator = int(eligible.sum())
            delta = losses[a][metric] - losses[b][metric]
            total = float(delta[eligible].mean())
            groups = {}
            for partition_name, masks in partitions.items():
                assert all(sum(m.astype(int) for m in masks.values()) == 1)
                records = {}
                for band, mask in masks.items():
                    selected = mask & eligible
                    n = int(selected.sum())
                    records[band] = dict(
                        n=n,
                        mean_delta=float(delta[selected].mean()) if n else None,
                        contribution_to_full_mean=float(delta[selected].sum()/denominator),
                    )
                assert abs(sum(x['contribution_to_full_mean'] for x in records.values()) - total) < 1e-14
                groups[partition_name] = records
            details[metric] = dict(full_mean_delta=total, denominator=denominator, partitions=groups)
        result[f'{a}_minus_{b}'] = details
    artifact = dict(
        purpose='Descriptive fixed saved-prediction loss attribution. No new forecast, fit, policy, threshold sweep, or ROI comparison.',
        population=dict(races=24855, horses=343883, days=807),
        partitions='Common baseline raw EV and fixed odds bins. Outcome-selected winners only for winner NLL. Negative delta means improvement.',
        caution='Post-result mechanism diagnosis, not independent validation or a tradable subset rule; no causal attribution.',
        sha256={a: hashlib.sha256((ROOT/'artifacts'/f).read_bytes()).hexdigest() for a, f in FILES.items()},
        contrasts=result,
    )
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v['winner_nll'] for k,v in result.items()}, indent=2))


if __name__ == '__main__':
    main()
