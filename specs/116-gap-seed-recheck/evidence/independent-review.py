"""Read-only numerical reproduction of 116, using saved coefficients; no fits."""
from __future__ import annotations

import gc
import json
import math
from pathlib import Path
import statistics
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import gap_seed_recheck as d
import gap_seed_summary as summary_method
from horseracing_eval.dataset import population_masks
from horseracing_eval.paired import _score_arm
from horseracing_training.calib_split import assemble_predictions


def close(a, b, tolerance=1e-12):
    if not math.isclose(float(a), float(b), rel_tol=0, abs_tol=tolerance):
        raise ValueError(f'Independent numerical mismatch: {a} != {b}')


def independent_ci(rows):
    by_day = {}
    for row in rows:
        by_day.setdefault(row['race_day'], []).append(row['diff'])
    days = sorted(by_day)
    sums = np.array([sum(by_day[day]) for day in days])
    counts = np.array([len(by_day[day]) for day in days])
    rng = np.random.default_rng(20260907)
    values = []
    for _ in range(4000):
        pick = rng.integers(0, len(days), size=len(days))
        values.append(sums[pick].sum() / counts[pick].sum())
    point = sums.sum() / counts.sum()
    lo, hi = np.percentile(values, [.625, 99.375])
    pad = statistics.NormalDist().inv_cdf(.99375) * .001816 / math.sqrt(7)
    return [point - math.hypot(point - lo, pad), point + math.hypot(hi - point, pad)]


def main():
    output = Path(__file__).with_name('independent-review.json')
    if output.exists():
        raise FileExistsError('Preserve the existing independent review')
    cfg, frozen = d.verify()
    assert all(d.completed(job) for job in frozen['jobs'])
    assert not (d.WORK / 'running.lock').exists()
    state, cfg113, frozen113, frozen115 = d.sources()
    reports, evidence, source_hashes = {}, {}, {}
    for seed in d.SEEDS:
        reports[seed], evidence[seed], source_hashes[str(seed)] = {}, {}, {}
        for contrast in d.CONTRASTS:
            name = contrast['id']
            assert d.verified_result(seed, contrast, cfg, frozen)
            path = d.result_path(seed, name)
            report = reports[seed][name] = d.s.read_json(path)
            evidence[seed][name] = d.s.read_json(report['evidence_path'])
            source_hashes[str(seed)][name] = d.s.p.digest(path)
    population = summary_method.validate_population(reports, evidence)
    matrix, races, folds, lookup, audit = d.g.load_inputs(cfg113)
    del matrix, races
    gc.collect()
    assert audit == frozen['sources']['population']
    records = []
    for seed in d.SEEDS:
        coefficients = (d.g.verified_coefficients(frozen115) if seed == 42
                        else d.verified_coefficients(seed, frozen))
        raw_rows = {name: {r['race_id']: r for r in evidence[seed][name]['rows']}
                    for name in summary_method.CONTRAST_IDS}
        predictions = {arm: {} for arm in ('candidate', 'pruning', 'anchor')}
        valid, prior, coefficient_checks = [], [], []
        max_error, below_clip, rounding = 0.0, 0, 0.0
        for year, fold in sorted(folds.items()):
            caches = {}
            for arm in ('pruning', 'anchor'):
                if year == 2019 and arm == 'anchor':
                    continue
                if seed == 42:
                    row = next(r for r in frozen113['source_caches']
                               if r['arm'] == arm and r['year'] == year)
                    d.s.validate_receipt(row)
                    path = Path(row['path'])
                else:
                    job = next(j for j in frozen['jobs'] if j['seed'] == seed
                               and j['arm'] == arm and j['year'] == year)
                    assert d.completed(job)
                    path = d.WORK / 'cache' / f"{job['key']}.pkl"
                caches[arm] = d.s.reuse.load(path)
            gamma = coefficients['gammas'].get(str(year))
            block = []
            for er in fold.valid:
                context = er.context
                ids = [horse.horse_id for horse in context.started_horses]
                base = caches['pruning']['predictions'][context.race_id]
                p = np.array([base[horse].win for horse in ids])
                h = np.array([lookup[(context.race_id, horse)][1] for horse in ids])
                h = np.nan_to_num(h, nan=0.0)
                pop = population_masks(er)
                if pop.eligible:
                    block.append((str(context.race_date), p, h, ids.index(pop.winner_horse_id)))
                if year == 2019:
                    continue
                z = h * gamma
                weighted = p * np.exp(z - z.max())
                q = weighted / weighted.sum()
                assert np.isfinite(q).all() and (q > 0).all()
                cand = assemble_predictions(ids, q, eps=0.0)
                x = np.array([[cand[i].win, cand[i].top2, cand[i].top3] for i in ids])
                assert np.isfinite(x).all() and (x >= -1e-10).all() and (x <= 1 + 1e-10).all()
                assert (np.diff(x, axis=1) >= -1e-12).all()
                assert np.allclose(x.sum(0), [min(k, len(ids)) for k in (1, 2, 3)], atol=1e-8, rtol=0)
                below_clip += int((q < d.g.DEFAULT_CLIP).sum())
                rounding = max(rounding, float(np.max(np.abs(x[:, 0] - q))))
                predictions['candidate'][context.race_id] = cand
                valid.append(er)
                for arm in ('pruning', 'anchor'):
                    predictions[arm][context.race_id] = caches[arm]['predictions'][context.race_id]
                assert (context.race_id in raw_rows['anchor']) == pop.eligible
                if pop.eligible:
                    losses = {arm: -math.log(ps[context.race_id][pop.winner_horse_id].win)
                              for arm, ps in predictions.items()}
                    for name, base_arm in [('increment', 'pruning'), ('anchor', 'anchor')]:
                        row = raw_rows[name][context.race_id]
                        for field, value in [('candidate_winner_nll', losses['candidate']),
                                             ('active_winner_nll', losses[base_arm]),
                                             ('diff', losses['candidate'] - losses[base_arm])]:
                            close(row[field], value)
                            max_error = max(max_error, abs(row[field] - value))
            if year > 2019:
                diag = coefficients['fit_diagnostics'][year - 2020]
                assert diag['fit_races'] == len(prior)
                assert diag['fit_last_day'] == max(r[0] for r in prior)
                assert diag['eval_first_day'] == min(r[0] for r in block)
                assert diag['fit_last_day'] < diag['eval_first_day']
                losses, gradient = [], 0.0
                for day, p, h, winner in prior:
                    p = p / p.sum()
                    z = h * gamma
                    z -= z.max()
                    weighted = p * np.exp(z)
                    losses.append(float(-z[winner] + np.log(weighted.sum())))
                    gradient += (weighted / weighted.sum()) @ h - h[winner]
                objective = float(np.mean(losses) + .5e-6 * gamma * gamma)
                gradient_norm = abs(gradient / len(prior) + 1e-6 * gamma)
                close(objective, diag['regularized_fit_objective'])
                assert objective <= 1e-8 and gradient_norm <= 1e-5
                coefficient_checks.append({'year': year, 'gamma': gamma,
                                           'objective': objective, 'gradient_norm': gradient_norm})
            prior.extend(block)
            del caches
        scores = {arm: _score_arm(valid, preds, band_edges=[0, .1, .2, .5, 1])
                  for arm, preds in predictions.items()}
        for name, base_arm in [('increment', 'pruning'), ('anchor', 'anchor')]:
            report = reports[seed][name]
            cs, bs = scores['candidate'], scores[base_arm]
            reasons = report['gate']['reasons']
            for actual, expected in [(cs.winner_nll, report['periods']['all']['candidate']),
                                     (bs.winner_nll, report['periods']['all']['active']),
                                     (cs.top2_logloss - bs.top2_logloss, reasons['top2_diff']),
                                     (cs.top3_logloss - bs.top3_logloss, reasons['top3_diff']),
                                     (cs.ece_equal_width_like['ece'], reasons['cand_ece']),
                                     (bs.ece_equal_width_like['ece'], reasons['act_ece'])]:
                close(actual, expected)
            ci = independent_ci(evidence[seed][name]['rows'])
            close(ci[0], report['total_ci']['ci_low'])
            close(ci[1], report['total_ci']['ci_high'])
            assert report['research_disposition'] == d.s.research.assess_research(report)
            assert below_clip == report['assembly_audit']['below_legacy_clip_horses']
            close(rounding, report['assembly_audit']['max_post_assembly_win_change'])
        records.append({'seed': seed, 'races': len(valid), 'eligible': len(raw_rows['anchor']),
                        'max_per_race_nll_error': max_error, 'below_legacy_clip_horses': below_clip,
                        'max_assembly_rounding': rounding, 'coefficient_checks': coefficient_checks,
                        'primary_top2_top3_ece_and_total_ci_match': True})
        print(f'REVIEW PASS seed={seed}; no fitting', flush=True)
        del predictions, prior, valid, scores
        gc.collect()
    computed = summary_method.summarize_reports(reports)
    stored = d.s.read_json(d.SPEC / 'verdict.json')
    for key, value in computed.items():
        assert stored[key] == value
    assert stored['population'] == population
    for name in summary_method.CONTRAST_IDS:
        mean = sum(reports[seed][name]['periods']['all']['diff'] for seed in d.SEEDS) / 3
        close(mean, stored['contrasts'][name]['equal_seed_mean_periods']['all']['diff']['mean'])
    d.verify()
    result = {'artifact_kind': 'gap_seed_independent_review', 'status': 'PASS',
              'can_adopt': False, 'eligible_for_verdict': False,
              'method_sha256': d.s.p.digest(__file__), 'run_freeze_sha256': d.s.p.digest(d.WORK / 'run-freeze.json'),
              'summary_sha256': d.s.p.digest(d.SPEC / 'verdict.json'), 'report_hashes': source_hashes,
              'new_cache_receipts_verified': 30, 'population': population, 'seeds': records,
              'research_decision': stored['research_decision'], 'additional_fits': 0,
              'notes': ['All saved gamma coefficients evaluated without refitting.',
                        'NLL from original cached probabilities and labels; top2/top3 use the registered Harville assembly.',
                        'Bootstrap independently recomputed using sampled day sums and counts; 7-fold transferred noise is unchanged.',
                        'Per-seed recent/subgroup records remain uncertain where declared; no mean CI or production verdict.']}
    d.write_json(output, result)
    print(json.dumps({'status': 'PASS', 'output': str(output), 'decision': stored['research_decision']}), flush=True)


if __name__ == '__main__':
    main()
