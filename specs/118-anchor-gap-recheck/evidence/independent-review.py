"""118 read-only numerical audit; run only after all six reports and summary exist.

No booster or gamma fit is called. Only this script's new JSON output is written.
Registered Harville assembly and scoring primitives are reused; probability tilt,
coefficient objective/gradient, bootstrap, means and fixed groups are recomputed.
"""
from __future__ import annotations

from collections import defaultdict
import datetime as dt
import gc
import math
from pathlib import Path
import statistics
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import anchor_gap_recheck as a
import anchor_gap_summary as summary_method
from horseracing_eval.dataset import population_masks
from horseracing_eval.gates import _window_start
from horseracing_eval.paired import _score_arm
from horseracing_training.calib_split import assemble_predictions

SEEDS = (42, 43, 44)
CONTRASTS = ('anchor', 'retained')
MAX_ERROR = 0.0
CHECKS = 0


def close(actual, expected):
    global MAX_ERROR, CHECKS
    CHECKS += 1
    actual, expected = float(actual), float(expected)
    assert math.isfinite(actual) and math.isfinite(expected)
    error = abs(actual - expected)
    MAX_ERROR = max(MAX_ERROR, error)
    assert error <= 1e-12, (actual, expected, error)


def mean(values):
    return math.fsum(values) / len(values)


def nll(probability):
    assert math.isfinite(probability) and 0 <= probability <= 1
    return -math.log(min(max(probability, 1e-15), 1 - 1e-15))


def check_probabilities(predictions, ids):
    assert set(predictions) == set(ids) and len(ids) == len(set(ids))
    values = np.array([[predictions[h].win, predictions[h].top2, predictions[h].top3] for h in ids])
    assert np.isfinite(values).all() and (values >= -1e-10).all() and (values <= 1 + 1e-10).all()
    assert (np.diff(values, axis=1) >= -1e-10).all()
    assert np.allclose(values.sum(0), [min(k, len(ids)) for k in (1, 2, 3)], atol=1e-8, rtol=0)
    assert (values[:, 0] > 0).all()
    return values[:, 0]


def tilt(base, ids, h, gamma):
    p = check_probabilities(base, ids)
    assert np.isfinite(h).all() and math.isfinite(gamma)
    exponent = gamma * h
    weighted = p * np.exp(exponent - exponent.max())
    q = weighted / weighted.sum()
    assert np.isfinite(q).all() and (q > 0).all()
    assert len(ids) == 1 or (q < 1).all()
    close(q.sum(), 1.)
    result = assemble_predictions(ids, q, eps=0.0)
    assembled = check_probabilities(result, ids)
    return result, int((q < 1e-6).sum()), float(np.max(np.abs(q - assembled)))


def bootstrap(rows):
    days = defaultdict(list)
    for row in rows:
        days[row['race_day']].append(row['diff'])
    order = sorted(days)
    sums = np.array([sum(days[day]) for day in order])
    counts = np.array([len(days[day]) for day in order])
    rng = np.random.default_rng(20260907)
    samples = []
    for _ in range(4000):
        indices = rng.integers(0, len(order), size=len(order))
        samples.append(sums[indices].sum() / counts[indices].sum())
    point = float(sums.sum() / counts.sum())
    lo, hi = np.percentile(samples, [.625, 99.375])
    padding = statistics.NormalDist().inv_cdf(.99375) * .001816 / math.sqrt(7)
    return {'point': point, 'sample': [float(lo), float(hi)],
            'total': [point - math.hypot(point - lo, padding), point + math.hypot(hi - point, padding)],
            'days': len(order)}


def cache_path(seed, arm, year, frozen, old116, old113):
    if seed == 42:
        record = next(r for r in old113['source_caches'] if r['arm'] == arm and r['year'] == year)
        a.s.validate_receipt(record)
        return Path(record['path'])
    if year == 2019 and arm == 'anchor':
        job = next(j for j in frozen['jobs'] if j['seed'] == seed)
        assert a.completed(job)
        return a.WORK / 'cache' / f"{job['key']}.pkl"
    job = next(j for j in old116['jobs'] if j['seed'] == seed and j['arm'] == arm and j['year'] == year)
    assert a.d.completed(job)
    return a.d.WORK / 'cache' / f"{job['key']}.pkl"


def check_coefficient(prior, block, gamma, year, stored):
    assert stored['eval_year'] == year and stored['fit_races'] == len(prior)
    assert stored['fit_last_day'] == max(r[0] for r in prior)
    assert stored['eval_first_day'] == min(r[0] for r in block)
    assert stored['fit_last_day'] < stored['eval_first_day']
    assert all(int(r[0][:4]) < year for r in prior)
    losses, gradient = [], 0.0
    for day, p, h, winner in prior:
        p = p / p.sum()
        z = gamma * h
        z -= z.max()
        weights = p * np.exp(z)
        losses.append(float(-z[winner] + np.log(weights.sum())))
        gradient += (weights / weights.sum()) @ h - h[winner]
    objective = float(np.mean(losses) + .5e-6 * gamma * gamma)
    norm = abs(gradient / len(prior) + 1e-6 * gamma)
    close(objective, stored['regularized_fit_objective'])
    close(norm, stored['gradient_inf'])
    assert objective <= 1e-8 and norm <= 1e-5
    return {'eval_year': year, 'gamma': gamma, 'fit_races': len(prior),
            'fit_last_day': stored['fit_last_day'], 'eval_first_day': stored['eval_first_day'],
            'regularized_fit_objective': objective, 'gradient_inf': norm}


def main():
    output = Path(__file__).with_suffix('.json')
    assert not output.exists(), 'Preserve previous review'
    assert (a.SPEC / 'verdict.json').exists(), 'Wait for parent summary completion'
    assert all(a.result_path(s, c).exists() for s in SEEDS for c in CONTRASTS), 'Wait for all six reports'
    cfg, frozen = a.verify()
    assert not (a.WORK / 'running.lock').exists()
    assert {(j['seed'], j['arm'], j['year']) for j in frozen['jobs']} == {(43, 'anchor', 2019), (44, 'anchor', 2019)}
    assert len(frozen['jobs']) == 2 and all(a.completed(j) for j in frozen['jobs'])
    old116 = a.s.read_json(a.d.WORK / 'run-freeze.json')
    old113 = a.s.read_json(a.s.WORK / 'run-freeze.json')
    reports, evidence, old_evidence, hashes = {}, {}, {}, {}
    for seed in SEEDS:
        reports[seed], evidence[seed], hashes[str(seed)] = {}, {}, {}
        previous = a.s.read_json(a.d.result_path(seed, 'anchor'))
        old_evidence[seed] = a.s.read_json(previous['evidence_path'])
        for name in CONTRASTS:
            assert a.verified_result(seed, name, cfg, frozen)
            path = a.result_path(seed, name)
            reports[seed][name] = a.s.read_json(path)
            evidence[seed][name] = a.s.read_json(reports[seed][name]['evidence_path'])
            hashes[str(seed)][name] = {'report_sha256': a.s.p.digest(path),
                'evidence_sha256': a.s.p.digest(reports[seed][name]['evidence_path'])}
    population = summary_method.validate_population(reports, evidence, old_evidence)
    matrix, races, folds, lookup, audit = a.g.load_inputs(a.s.load_config())
    assert audit == frozen['sources']['population']
    new_cache_records = []
    for job in frozen['jobs']:
        factory = a.factory(cfg, frozen, matrix, races, job['seed'])
        assert a.job_for(factory, folds[2019], job['seed']) == job
        path = a.WORK / 'cache' / f"{job['key']}.pkl"
        payload = a.s.reuse.load(path)
        a.s.reuse.check_payload(payload, job['key'], job['train_hash'], factory, list(folds[2019].valid))
        new_cache_records.append({'seed': job['seed'], 'year': 2019, 'columns': len(payload['feature_columns']),
            'key': job['key'], 'train_hash': job['train_hash'], 'recipe_hash': factory.recipe_hash,
            'cache_sha256': a.s.p.digest(path), 'receipt_sha256': a.s.p.digest(a.receipt_path(job['key'])),
            'oof_sufficient': payload['oof_info']['sufficient']})
        del payload, factory
    # Recompute the transformation from the stored gap input; do not fit anything.
    valid_ids = {r.context.race_id for f in folds.values() for r in f.valid}
    gap_frame = matrix.frame.loc[matrix.frame.race_id.isin(valid_ids), ['race_id', 'horse_id', 'days_since_last']]
    for rid, hid, value in gap_frame.itertuples(index=False, name=None):
        supplied = lookup[(rid, hid)][1]
        if np.isnan(value):
            assert np.isnan(supplied)
        else:
            assert np.isfinite(value) and value > 0 and float(value).is_integer()
            close(float(np.log1p(value)), supplied)
    del matrix, races, gap_frame
    gc.collect()
    records, computed_losses = [], {}
    for seed in SEEDS:
        coefficients = a.verified_coefficients(seed, frozen)
        retained_coefficients = a.old_coefficients(seed)
        raw = {name: {r['race_id']: r for r in evidence[seed][name]['rows']} for name in CONTRASTS}
        old_rows = {r['race_id']: r for r in old_evidence[seed]['rows']}
        predictions = {name: {} for name in ('candidate', 'anchor', 'retained')}
        computed_losses[seed] = {}
        prior, valid, coefficient_checks = [], [], []
        max_nll_error, below_clip, assembly_rounding = 0.0, 0, 0.0
        for year, fold in sorted(folds.items()):
            caches = {name: a.s.reuse.load(cache_path(seed, name, year, frozen, old116, old113))
                      for name in (('anchor',) if year == 2019 else ('anchor', 'pruning'))}
            expected_races = {r.context.race_id for r in fold.valid}
            assert all(set(c['predictions']) == expected_races for c in caches.values())
            block = []
            for race in fold.valid:
                context, pop = race.context, population_masks(race)
                ids = [h.horse_id for h in context.started_horses]
                anchor = caches['anchor']['predictions'][context.race_id]
                p = check_probabilities(anchor, ids)
                h = np.array([lookup[(context.race_id, horse)][1] for horse in ids])
                assert not np.isinf(h).any()
                h = np.nan_to_num(h, nan=0.)
                if pop.eligible:
                    block.append((str(context.race_date), p, h, ids.index(pop.winner_horse_id)))
                if year == 2019:
                    continue
                candidate, low, rounding = tilt(anchor, ids, h, coefficients['gammas'][str(year)])
                retained, _, _ = tilt(caches['pruning']['predictions'][context.race_id], ids, h,
                                      retained_coefficients['gammas'][str(year)])
                below_clip += low
                assembly_rounding = max(assembly_rounding, rounding)
                for arm, value in [('candidate', candidate), ('anchor', anchor), ('retained', retained)]:
                    predictions[arm][context.race_id] = value
                valid.append(race)
                assert all((context.race_id in raw[name]) == pop.eligible for name in CONTRASTS)
                if not pop.eligible:
                    continue
                losses = {arm: nll(preds[context.race_id][pop.winner_horse_id].win) for arm, preds in predictions.items()}
                computed_losses[seed][context.race_id] = losses
                old = old_rows[context.race_id]
                close(losses['anchor'], old['active_winner_nll'])
                close(losses['retained'], old['candidate_winner_nll'])
                for name in CONTRASTS:
                    row = raw[name][context.race_id]
                    assert row['race_day'] == str(context.race_date)
                    for field, value in [('candidate_winner_nll', losses['candidate']),
                                         ('active_winner_nll', losses[name]), ('diff', losses['candidate'] - losses[name])]:
                        close(value, row[field])
                        max_nll_error = max(max_nll_error, abs(value - row[field]))
            if year > 2019:
                coefficient_checks.append(check_coefficient(prior, block, coefficients['gammas'][str(year)],
                    year, coefficients['fit_diagnostics'][year - 2020]))
            else:
                assert len(block) == coefficients['warmup_eligible_races'] == 3447
            prior.extend(block)
            del caches
        assert len(valid) == 23030 and len(computed_losses[seed]) == 22990
        scores = {arm: _score_arm(valid, preds, band_edges=[.05, .15, .30]) for arm, preds in predictions.items()}
        ci_records = {}
        for name in CONTRASTS:
            report = reports[seed][name]
            candidate, baseline = scores['candidate'], scores[name]
            q = report['gate']['reasons']
            for measured, stored in [(candidate.winner_nll, report['periods']['all']['candidate']),
                (baseline.winner_nll, report['periods']['all']['active']),
                (candidate.top2_logloss - baseline.top2_logloss, q['top2_diff']),
                (candidate.top3_logloss - baseline.top3_logloss, q['top3_diff']),
                (candidate.ece_equal_width_like['ece'], q['cand_ece']), (baseline.ece_equal_width_like['ece'], q['act_ece'])]:
                close(measured, stored)
            for years in (3, 5):
                cutoff = str(_window_start(dt.date(2026, 8, 23), years))
                selected = [r for r in raw[name].values() if r['race_day'] >= cutoff]
                period = report['periods'][f'recent_{years}y']
                for role, field in [('candidate', 'candidate'), ('active', name)]:
                    close(mean([computed_losses[seed][r['race_id']][field] for r in selected]), period[role])
                close(period['candidate'] - period['active'], period['diff'])
            ci = bootstrap(evidence[seed][name]['rows'])
            close(ci['point'], report['total_ci']['point'])
            assert ci['days'] == report['total_ci']['n_days'] == 715
            for key, bounds in [('bootstrap_ci', ci['sample']), ('total_ci', ci['total'])]:
                close(bounds[0], report[key]['ci_low'])
                close(bounds[1], report[key]['ci_high'])
            ci_records[name] = ci
            assert report['research_disposition'] == a.s.research.assess_research(report)
            assert report['assembly_audit']['races'] == 23030
            assert report['assembly_audit']['assembly_eps'] == 0.
            assert report['assembly_audit']['below_legacy_clip_horses'] == below_clip
            close(assembly_rounding, report['assembly_audit']['max_post_assembly_win_change'])
        records.append({'seed': seed, 'races': len(valid), 'eligible': len(computed_losses[seed]),
            'max_per_race_nll_error': max_nll_error, 'coefficient_checks': coefficient_checks,
            'new_coefficient_sha256': a.s.p.digest(a.seed_area(seed) / 'coefficients.json'),
            'retained_coefficient_sha256': a.s.p.digest(a.old_coefficient_path(seed)),
            'below_legacy_clip_horses': below_clip, 'max_assembly_rounding': assembly_rounding,
            'top2_top3_ece_recent_and_ci_match': True, 'ci': ci_records})
        print(f'PASS seed={seed} saved gamma, all probabilities and loss metrics; no fits', flush=True)
        del predictions, scores, valid, prior
        gc.collect()
    stored = a.s.read_json(a.SPEC / 'verdict.json')
    for key, value in summary_method.summarize_reports(reports).items():
        assert stored[key] == value
    assert stored['population'] == population
    states = {}
    for name in CONTRASTS:
        values = [mean([v['candidate'] - v[name] for v in computed_losses[seed].values()]) for seed in SEEDS]
        close(mean(values), stored['contrasts'][name]['equal_seed_mean_periods']['all']['diff']['mean'])
        q = stored['contrasts'][name]['mean_quality']
        quality = q['top2_diff'] <= .0005 and q['top3_diff'] <= .0005 and q['ece_diff'] <= .001 and q['cand_ece'] < .05
        blocked = bool(stored['contrasts'][name]['blocked_seeds'])
        states[name] = 'REVIEW_REQUIRED' if blocked or not quality else 'IMPROVED' if mean(values) < 0 else 'NOT_IMPROVED'
    if 'REVIEW_REQUIRED' in states.values():
        expected = ('REVIEW_REQUIRED', 'REVIEW_REQUIRED', 'UNCHANGED_PENDING_REVIEW')
    elif states['anchor'] != 'IMPROVED':
        expected = ('ANCHOR_GAP_DEFERRED', 'DEFER', 'PRUNING_GAP')
    elif states['retained'] == 'IMPROVED':
        expected = ('ANCHOR_GAP_PREFERRED', 'RETAIN', 'ANCHOR_GAP')
    else:
        expected = ('ANCHOR_GAP_ALTERNATIVE_RETAINED', 'RETAIN', 'PRUNING_GAP')
    assert expected == tuple(stored[k] for k in ('research_decision', 'candidate_retention', 'preferred_research_configuration'))
    attrs_path = ROOT / 'artifacts/117-pruning-2026-diagnostic/race-diagnostic.json'
    assert stored['attributes117_sha256'] == a.s.p.digest(attrs_path)
    attributes = a.s.read_json(attrs_path)['rows']
    assert [(r['race_id'], r['race_day']) for r in attributes] == [(r['race_id'], r['race_day']) for r in evidence[42]['anchor']['rows']]
    group_rules = {'2026_all': lambda r: r['year'] == 2026,
        '2026_nakayama': lambda r: r['year'] == 2026 and r['venue'] == '06',
        '2026_partial_relative': lambda r: r['year'] == 2026 and r['relative_coverage'] == '(.5,1)'}
    counts = {'2026_all': (2296, 70), '2026_nakayama': (300, 25), '2026_partial_relative': (1538, 70)}
    for group, select in group_rules.items():
        selected = [r for r in attributes if select(r)]
        diagnostic = stored['diagnostics']['groups'][group]
        assert (len(selected), len({r['race_day'] for r in selected})) == counts[group]
        assert (diagnostic['n_races'], diagnostic['n_days']) == counts[group]
        for name in CONTRASTS:
            by_seed = {}
            for seed in SEEDS:
                candidate = mean([computed_losses[seed][r['race_id']]['candidate'] for r in selected])
                baseline = mean([computed_losses[seed][r['race_id']][name] for r in selected])
                by_seed[seed] = {'candidate': candidate, 'active': baseline, 'diff': candidate - baseline}
                for key, value in by_seed[seed].items():
                    close(value, diagnostic['contrasts'][name]['by_seed'][str(seed)][key])
            for key in ('candidate', 'active', 'diff'):
                values = [by_seed[seed][key] for seed in SEEDS]
                for metric, value in [('mean', mean(values)), ('sample_sd_descriptive', statistics.stdev(values)), ('min', min(values)), ('max', max(values))]:
                    close(value, diagnostic['contrasts'][name]['equal_seed_means'][key][metric])
    assert a.verify() == (cfg, frozen)
    assert all(a.completed(j) for j in frozen['jobs']) and not (a.WORK / 'running.lock').exists()
    for seed in SEEDS:
        for name in CONTRASTS:
            assert a.verified_result(seed, name, cfg, frozen)
            assert hashes[str(seed)][name]['report_sha256'] == a.s.p.digest(a.result_path(seed, name))
    result = {'artifact_kind': 'anchor_gap_independent_review', 'status': 'PASS',
        'can_adopt': False, 'eligible_for_verdict': False, 'additional_fits': 0,
        'method_sha256': a.s.p.digest(__file__), 'run_freeze_sha256': a.s.p.digest(a.WORK / 'run-freeze.json'),
        'summary_sha256': a.s.p.digest(a.SPEC / 'verdict.json'), 'report_hashes': hashes,
        'new_cache_receipts': new_cache_records, 'population': population, 'seeds': records,
        'checks': CHECKS, 'max_numerical_error': MAX_ERROR, 'fixed117_diagnostic_groups_recomputed': True,
        'research_decision': stored['research_decision'], 'candidate_retention': stored['candidate_retention'],
        'preferred_research_configuration': stored['preferred_research_configuration'],
        'notes': ['All21 new gamma objectives, gradients and strict-prior-year populations checked without refitting.',
            'Both candidate and retained probabilities reconstructed from original cache and separate saved coefficients.',
            'Registered Harville assembly and scoring primitives reused; tilt, bootstrap, means and fixed groups independently calculated.',
            'Per-seed CIs retain transferred uncertainty assumptions; this audit creates no mean CI or production assurance.']}
    a.write_json(output, result)
    print(f'PASS {output}; no fits', flush=True)


if __name__ == '__main__':
    main()
