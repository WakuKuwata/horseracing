"""Independent numerical audit of completed135 Step2 artifacts; never fits or queries DB."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pickle

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'artifacts/135-mixture-reproducibility/repro'
OUT = ROOT / 'specs/135-mixture-reproducibility/evidence/repro-independent-review.json'
HEADS = ('win', 'top2', 'top3')
METRICS = ('winner_nll', 'top2_logloss', 'top3_logloss', 'top2_brier', 'top3_brier')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def sealed_pickle(path, expected):
    assert sha(path) == expected, path
    with Path(path).open('rb') as f:
        return pickle.load(f)


def equal_mass_ece(p, y, bins=10):
    order = np.argsort(p, kind='stable')
    p, y = p[order], y[order]
    cuts = np.linspace(0, len(p), bins + 1).astype(int)
    right_edges = sorted(set(int(np.searchsorted(p, p[c-1], side='right')) for c in cuts[1:] if c > 0))
    start, total = 0, 0.
    for end in right_edges:
        total += abs(float(p[start:end].sum() - y[start:end].sum()))
        start = end
    return total / len(p)


def calculate(records, predictions, *, through='2026-08-23'):
    days, all_p, all_y, nll = {}, [], [], []
    complete = 0
    for r in records:
        assert r.race_date.isoformat() <= through
        ids = tuple(h.horse_id for h in r.er.context.started_horses)
        assert r.ids == ids
        p = np.asarray(predictions[r.race_id])
        assert p.shape == (len(ids), 3) and np.isfinite(p).all()
        assert (p > 0).all() and (p <= 1 + 1e-12).all()
        assert (p[:, 1:] + 1e-12 >= p[:, :-1]).all()
        assert np.allclose(p.sum(axis=0), np.minimum([1, 2, 3], len(ids)), rtol=0, atol=1e-8)
        labels = {x.horse_id: (x.win, x.top2, x.top3) for x in r.er.labels}
        y = np.array([labels.get(h, (0, 0, 0)) for h in ids])
        assert r.er.n_result_rows is not None
        if r.er.n_result_rows < len(ids):
            continue
        complete += 1
        all_p.append(p); all_y.append(y)
        day = r.race_date.isoformat()
        block = days.setdefault(day, {m: [0., 0] for m in METRICS})
        winners = np.flatnonzero(y[:, 0])
        if len(winners) == 1:
            v = -float(np.log(p[winners[0], 0]))
            nll.append(v)
            block['winner_nll'][0] += v
            block['winner_nll'][1] += 1
        clipped = np.clip(p, 1e-15, 1 - 1e-15)
        ll = -(y * np.log(clipped) + (1-y) * np.log1p(-clipped))
        for j, h in enumerate(HEADS[1:], 1):
            for name, value in ((h+'_logloss', ll[:, j].sum()), (h+'_brier', ((p[:, j]-y[:, j])**2).sum())):
                block[name][0] += float(value)
                block[name][1] += len(ids)
    summary = {'n_races': len(records), 'n_complete_races': complete, 'n_eligible_races': len(nll),
               'winner_nll': float(np.mean(nll)) if nll else None, 'heads': {}}
    if not all_p:
        return summary, days
    p, y = np.concatenate(all_p), np.concatenate(all_y)
    clipped = np.clip(p, 1e-15, 1-1e-15)
    losses = -(y*np.log(clipped)+(1-y)*np.log1p(-clipped))
    for j, h in enumerate(HEADS):
        summary['heads'][h] = {'logloss': float(losses[:, j].mean()),
                               'brier': float(((p[:, j]-y[:, j])**2).mean()),
                               'ece': equal_mass_ece(p[:, j], y[:, j]), 'n_rows': len(p)}
    return summary, days


def day_pair(base, candidates, cfg):
    out = {}
    for metric in METRICS:
        ds = sorted(d for d in base if base[d][metric][1])
        denominator = np.array([base[d][metric][1] for d in ds])
        if not ds:
            assert all(not any(c[d][metric][1] for d in c) for c in candidates)
            out[metric] = {'point': None, 'ci': None, 'n_days': 0, 'n_rows': 0,
                           'seed_points': [None] * len(candidates)}
            continue
        for c in candidates:
            assert ds == sorted(d for d in c if c[d][metric][1])
            assert np.array_equal(denominator, [c[d][metric][1] for d in ds])
        differences = np.array([[c[d][metric][0]-base[d][metric][0] for d in ds] for c in candidates])
        numerator = differences.mean(axis=0)
        # One independent matrix draw, same fixed day-cluster random stream.
        sample = np.random.default_rng(cfg['seed']).integers(len(ds), size=(cfg['b'], len(ds)))
        draws = numerator[sample].sum(axis=1)/denominator[sample].sum(axis=1)
        out[metric] = {'point': float(numerator.sum()/denominator.sum()),
                       'ci': np.quantile(draws, [cfg['alpha']/2, 1-cfg['alpha']/2]).tolist(),
                       'n_days': len(ds), 'n_rows': int(denominator.sum()),
                       'seed_points': (differences.sum(axis=1)/denominator.sum()).tolist()}
    return out


def run():
    assert not OUT.exists(), 'Never replace an independent verdict'
    freeze, report = read(WORK/'run-freeze.json'), read(WORK/'summary.json')
    assert report['status'] == 'RESEARCH_COMPLETE'
    assert report['freeze_sha256'] == sha(WORK/'run-freeze.json')
    assert report['data_through'] == '2026-08-23' and not report['can_adopt'] and not report['eligible_for_verdict']
    for path, expected in freeze['inputs'].items():
        assert sha(path) == expected, path
    records = sealed_pickle(WORK/'records.pkl', freeze['records_sha256'])
    values = sealed_pickle(WORK/'mixture-predictions.pkl', report['prediction_sha256'])
    assert set(values['baseline']) == set(values['candidates']) == set(records) == {'full', 'preweight'}
    for regime, rr in records.items():
        ids = {r.race_id for r in rr}
        assert set(values['baseline'][regime]) == ids
        assert set(values['candidates'][regime]) == {42, 43, 44}
        assert all(set(v) == ids for v in values['candidates'][regime].values())
    checks = 0
    def check(got, expected):
        nonlocal checks
        assert np.allclose(got, expected, rtol=0, atol=2e-12), (got, expected)
        checks += 1
    def scores(got, expected):
        for k in ('n_races', 'n_complete_races', 'n_eligible_races', 'winner_nll'):
            check(got[k], expected[k])
        for h in HEADS:
            for k in ('logloss', 'brier', 'ece', 'n_rows'):
                check(got['heads'][h][k], expected['heads'][h][k])
    def paired(got, expected):
        for m in METRICS:
            for k in ('point', 'ci', 'n_days', 'n_rows'):
                check(got[m][k], expected[m][k])
    for regime in ('full', 'preweight'):
        rr = records[regime]
        assert len({r.race_id for r in rr}) == len(rr)
        for name in ('2024', '2026', 'pooled'):
            cohort = rr if name == 'pooled' else [r for r in rr if r.year == int(name)]
            block = report['reports'][regime][name]
            base_score, base_days = calculate(cohort, values['baseline'][regime])
            scores(base_score, block['baseline'])
            cd = []
            for seed in (42, 43, 44):
                cs, ds = calculate(cohort, values['candidates'][regime][seed]); cd.append(ds)
                saved = block['seeds'][str(seed)]
                scores(cs, saved['scores'])
                paired(day_pair(base_days, [ds], freeze['config']['bootstrap']), saved['paired'])
            actual = day_pair(base_days, cd, freeze['config']['bootstrap'])
            paired(actual, block['seed_mean_loss_difference'])
            for m in METRICS:
                check(actual[m]['seed_points'], block['seed_mean_loss_difference'][m]['seed_points'])
    outer, boosters, model_files = 0, 0, 0
    for job in freeze['jobs']:
        area = WORK/'jobs'/f"seed-{job['seed']}-{job['year']}"
        receipt = read(area/'receipt.json')
        assert receipt['freeze_sha256'] == sha(WORK/'run-freeze.json')
        assert receipt['roundtrip']['status'] == 'PASS' and receipt['roundtrip']['max_abs_diff'] == 0
        assert receipt['booster_fits'] == 8 and receipt['model_threads'] == 1
        assert receipt['prediction_sha256'] == sha(area/'predictions.pkl')
        if job['seed'] == 42:
            assert receipt['native126_parity']['status'] == 'PASS'
        for name, expected in receipt['model_files'].items():
            assert sha(area/'model'/name) == expected
            model_files += 1
        outer += 1; boosters += receipt['booster_fits']
    assert (outer, boosters, model_files) == (6, 48, 24)
    assert report['native132_seed42_full_parity']['status'] == 'PASS'
    result = {'status': 'PASS', 'freeze_sha256': sha(WORK/'run-freeze.json'),
              'summary_sha256': sha(WORK/'summary.json'), 'auditor_sha256': sha(__file__),
              'checks': checks, 'audited_outer_fits': outer, 'audited_booster_fits': boosters,
              'independently_recomputed': ['NLL', 'head logloss', 'Brier', 'tie-safe ECE', 'per-seed day CI', 'seed-loss-mean day CI'],
              'can_adopt': False, 'eligible_for_verdict': False}
    with OUT.open('x') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    run()
