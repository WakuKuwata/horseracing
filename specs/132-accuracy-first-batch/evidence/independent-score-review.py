"""Parent review of saved 132 predictions, without using the experiment scorers."""
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / 'artifacts/132-accuracy-first-batch'
HEADS = ('win', 'top2', 'top3')
CHECKS = []


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def close(actual, expected, name):
    assert np.allclose(actual, expected, rtol=0, atol=2e-12), (name, actual, expected)
    CHECKS.append(name)


def load_predictions(path, expected_sha):
    assert sha(path) == expected_sha
    with path.open('rb') as fh:
        return pickle.load(fh)


def outcomes(row):
    ids = row['ids']
    assert len(set(ids)) == len(ids)
    assert '2007-01-01' <= row['race_date'] <= '2026-09-06'
    labels = {sl[0]: sl[1:] for sl in row['labels']}
    y = np.array([labels.get(h, (0, 0, 0)) for h in ids], dtype=float)
    assert row['n_result_rows'] is not None
    complete = row['n_result_rows'] >= len(ids)
    winners = np.flatnonzero(y[:, 0])
    winner = int(winners[0]) if complete and len(winners) == 1 else None
    return y, complete, winner


def stats(rows, predictions):
    assert set(predictions) == {r['race_id'] for r in rows}
    loss_sum, brier_sum = np.zeros(3), np.zeros(3)
    nll_sum = n_eligible = n_complete = n_horses = 0
    by_day = {}
    for row in rows:
        p = np.asarray(predictions[row['race_id']])
        y, complete, winner = outcomes(row)
        assert p.shape == y.shape and np.isfinite(p).all()
        assert (p >= 0).all() and (p <= 1 + 1e-10).all()
        assert (np.diff(p, axis=1) >= -1e-10).all()
        assert np.allclose(p.sum(0), np.minimum([1, 2, 3], len(p)), atol=1e-8, rtol=0)
        day = by_day.setdefault(row['race_date'], np.zeros((5, 2)))
        if winner is not None:
            loss = -np.log(p[winner, 0])
            nll_sum += loss
            n_eligible += 1
            day[0] += (loss, 1)
        if complete:
            q = np.clip(p, 1e-15, 1 - 1e-15)
            losses = -np.where(y == 1, np.log(q), np.log1p(-q))
            briers = np.square(p - y)
            loss_sum += losses.sum(0)
            brier_sum += briers.sum(0)
            n_horses += len(p)
            n_complete += 1
            day[1:, 0] += [losses[:, 1].sum(), losses[:, 2].sum(), briers[:, 1].sum(), briers[:, 2].sum()]
            day[1:, 1] += len(p)
    return {'winner_nll': nll_sum / n_eligible, 'n_eligible_races': n_eligible,
            'n_complete_races': n_complete, 'n_races': len(rows), 'n_days': len(by_day),
            'heads': {h: {'logloss': loss_sum[j] / n_horses, 'brier': brier_sum[j] / n_horses,
                          'n_rows': n_horses} for j, h in enumerate(HEADS)}}, by_day


def check_score(score, expected, name):
    for key in ('winner_nll', 'n_eligible_races', 'n_complete_races', 'n_races', 'n_days'):
        close(score[key], expected[key], f'{name}:{key}')
    for head in HEADS:
        for metric in ('logloss', 'brier', 'n_rows'):
            close(score['heads'][head][metric], expected['heads'][head][metric], f'{name}:{head}:{metric}')


def check_paired(base, candidate, expected, name):
    assert base.keys() == candidate.keys()
    for j, metric in enumerate(('winner_nll', 'top2_logloss', 'top3_logloss', 'top2_brier', 'top3_brier')):
        days = [d for d in sorted(base) if base[d][j, 1] > 0]
        a = np.array([base[d][j] for d in days])
        b = np.array([candidate[d][j] for d in days])
        assert np.array_equal(a[:, 1], b[:, 1])
        difference, count = b[:, 0] - a[:, 0], a[:, 1]
        # Sample day multiplicities and reweight sufficient statistics. This is
        # independently implemented from the scorer's array-of-selected-rows sum.
        rng = np.random.default_rng(20260907)
        boot = []
        for _ in range(4000):
            multiplicity = np.bincount(rng.integers(0, len(days), len(days)), minlength=len(days))
            boot.append(float(multiplicity @ difference / (multiplicity @ count)))
        close(difference.sum() / count.sum(), expected[metric]['point'], f'{name}:{metric}:point')
        close(np.quantile(boot, [.00625, .99375]), expected[metric]['ci'], f'{name}:{metric}:CI')


def run():
    topk = read(WORK / 'topk/summary.json')
    colsample = read(WORK / 'colsample/summary.json')
    assert topk['can_adopt'] is False and colsample['can_adopt'] is False
    source_hashes = {str(WORK / f'{part}/summary.json'): sha(WORK / f'{part}/summary.json')
                     for part in ('topk', 'colsample')}
    full_rows = None
    for regime, report in topk['regimes'].items():
        path = WORK / 'topk' / f'{regime}-predictions.pkl'
        rows = load_predictions(path, report['predictions_sha256'])
        source_hashes[str(path)] = report['predictions_sha256']
        assert len({r['race_id'] for r in rows}) == len(rows)
        for fit in report['annual_stage_fits']:
            if fit['fit_through'] is not None:
                assert fit['fit_through'] < f"{fit['year']}-01-01"
        for row in rows:
            wins = [x[:, 0] for x in row['arms'].values()]
            assert all(np.array_equal(wins[0], p) for p in wins[1:])
        for cohort, subset in [('primary_2021_onward', [r for r in rows if r['race_date'] >= '2021-01-01']),
                               ('auxiliary_2020_onward', rows)]:
            saved_stats = {}
            for arm in report[cohort]['arms']:
                predictions = {r['race_id']: r['arms'][arm] for r in subset}
                score, days = stats(subset, predictions)
                check_score(score, report[cohort]['arms'][arm], f'{regime}:{cohort}:{arm}')
                saved_stats[arm] = days
            if cohort == 'primary_2021_onward':
                for contrast, a, b in [('reh_arville_minus_head_mean', 'head_mean', 'mean_win_harville'),
                    ('prior_stage_minus_reharville', 'mean_win_harville', 'annual_stage_discount'),
                    ('prior_stage_minus_head_mean', 'head_mean', 'annual_stage_discount')]:
                    check_paired(saved_stats[a], saved_stats[b], report[cohort]['contrasts'][contrast], f'{regime}:{contrast}')
        if regime == 'full':
            full_rows = rows
    assert full_rows is not None
    baseline, baseline_days = stats(full_rows, {r['race_id']: r['arms']['head_mean'] for r in full_rows})
    check_score(baseline, colsample['baseline'], 'colsample:baseline')
    for arm, report in colsample['reports'].items():
        path = Path(report['prediction_path'])
        payload = load_predictions(path, report['prediction_sha256'])
        assert payload['race_ids'] == [r['race_id'] for r in full_rows]
        assert all(tuple(payload['horse_ids'][r['race_id']]) == tuple(r['ids']) for r in full_rows)
        score, days = stats(full_rows, payload['predictions'])
        check_score(score, report['scores'], f'colsample:{arm}')
        check_paired(baseline_days, days, report['paired'], f'colsample:{arm}')
        source_hashes[str(path)] = report['prediction_sha256']
    output = {'status': 'PASS', 'method_sha256': sha(Path(__file__)), 'checks': CHECKS,
              'n_checks': len(CHECKS), 'source_hashes': source_hashes,
              'independent_of': ['accuracy_batch_common.py', 'accuracy_topk_recheck.py',
                                 'accuracy_colsample_recheck.py', 'population_masks'],
              'validated': ['winner NLL', 'started-all head logloss/Brier', 'race/horse populations',
                            'topk primary and auxiliary scores', 'all candidate scores',
                            'primary topk and all colsample paired day bootstrap CIs',
                            'win invariance and annual stage date bounds'],
              'not_independently_recomputed': ['ECE', 'stage lambda optimization', 'booster training'],
              'allowed_data_through': '2026-09-06', 'actual_evaluation_through': '2026-08-23',
              'can_adopt': False, 'eligible_for_verdict': False}
    with Path(__file__).with_suffix('.json').open('x') as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    print(json.dumps({'status': 'PASS', 'n_checks': len(CHECKS)}))


if __name__ == '__main__':
    run()
