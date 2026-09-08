"""Independent saved-forecast finite-mixture audit; no parameter fitting."""
from __future__ import annotations
import datetime as dt
import gc
import math
from pathlib import Path
import sys
import types

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import probability_mixture_recheck as driver
from horseracing_eval.dataset import population_masks
from horseracing_eval.gates import _window_start
from horseracing_eval.paired import _score_arm
from horseracing_eval.predictor import Prediction
from horseracing_training.calib_split import assemble_predictions

HELPER = ROOT / 'specs/118-anchor-gap-recheck/evidence/independent-review.py'
common = types.ModuleType('audit118_primitives')
common.__file__ = str(HELPER)
exec(compile(HELPER.read_text(), str(HELPER), 'exec'), common.__dict__)
close, mean = common.close, common.mean
SEEDS = (42, 43, 44)
MEMBERS = {'pruning3': [('pruning', s) for s in SEEDS],
           'anchor3': [('anchor', s) for s in SEEDS],
           'mixed6': [(a, s) for a in ('pruning', 'anchor') for s in SEEDS]}


def scalar_tilt(base, ids, gaps, gamma):
    p = common.check_probabilities(base, ids)
    z = [float(gamma) * (0. if math.isnan(h) else float(h)) for h in gaps]
    peak = max(z)
    weight = [float(v) * math.exp(t - peak) for v, t in zip(p, z, strict=True)]
    denominator = math.fsum(weight)
    q = [v / denominator for v in weight]
    assert all(math.isfinite(v) and v > 0 for v in q)
    close(math.fsum(q), 1.)
    result = assemble_predictions(ids, q, eps=0.)
    common.check_probabilities(result, ids)
    return result


def scalar_mixture(members, ids):
    for member in members:
        common.check_probabilities(member, ids)
    result = {h: Prediction(*(math.fsum(getattr(m[h], head) for m in members) / len(members)
                             for head in ('win', 'top2', 'top3'))) for h in ids}
    common.check_probabilities(result, ids)
    excess = max(-math.log(result[h].win) - mean([-math.log(m[h].win) for m in members]) for h in ids)
    assert excess <= 1e-12
    return result, excess


def main():
    output = Path(__file__).with_suffix('.json')
    assert not output.exists(), 'Preserve existing independent review'
    assert (driver.SPEC / 'verdict.json').exists(), 'Wait for completed parent120 summary'
    cfg, frozen = driver.verify()
    assert driver.MEMBERS == MEMBERS
    reports, evidence, hashes = {}, {}, {}
    for c in driver.COMPARISONS:
        assert driver.verified_result(c, cfg, frozen)
        path = driver.result_path(c['id'])
        r = reports[c['id']] = driver.a.s.read_json(path)
        evidence[c['id']] = driver.a.s.read_json(r['evidence_path'])
        hashes[c['id']] = {'report_sha256': driver.a.s.p.digest(path), 'evidence_sha256': driver.a.s.p.digest(r['evidence_path'])}
    population = driver.validate_population(reports, evidence)
    matrix, races, folds, lookup, population_audit = driver.a.g.load_inputs(driver.a.s.load_config())
    assert population_audit == frozen['sources']['population']
    valid_ids = {r.context.race_id for f in folds.values() for r in f.valid}
    frame = matrix.frame.loc[matrix.frame.race_id.isin(valid_ids), ['race_id', 'horse_id', 'days_since_last']]
    for rid, hid, gap in frame.itertuples(index=False, name=None):
        supplied = lookup[(rid, hid)][1]
        if np.isnan(gap):
            assert np.isnan(supplied)
        else:
            assert np.isfinite(gap) and gap > 0 and float(gap).is_integer()
            close(float(np.log1p(gap)), supplied)
    del matrix, races, frame
    gc.collect()
    old113 = driver.a.s.read_json(driver.a.s.WORK / 'run-freeze.json')
    old116 = driver.a.s.read_json(driver.a.d.WORK / 'run-freeze.json')
    old118 = driver.a.s.read_json(driver.a.WORK / 'run-freeze.json')
    coefficients = {(arm, seed): (driver.a.old_coefficients(seed) if arm == 'pruning'
                    else driver.a.verified_coefficients(seed, old118)) for arm in ('pruning', 'anchor') for seed in SEEDS}
    predictions = {name: {} for name in (*MEMBERS, 'retained42', 'anchor42')}
    loss, valid, jensen = {}, [], {name: -math.inf for name in MEMBERS}
    maximum = 0.
    raw = {name: {r['race_id']: r for r in e['rows']} for name, e in evidence.items()}
    for year, fold in sorted(folds.items()):
        if year < 2020:
            continue
        caches = {(arm, seed): driver.a.s.reuse.load(common.cache_path(seed, arm, year, old118, old116, old113))
                  for arm in ('pruning', 'anchor') for seed in SEEDS}
        for cache in caches.values():
            assert set(cache['predictions']) == {r.context.race_id for r in fold.valid}
        for race in fold.valid:
            context, pop = race.context, population_masks(race)
            ids = [h.horse_id for h in context.started_horses]
            gaps = []
            for h in ids:
                day, gap = lookup[(context.race_id, h)]
                assert str(day) == str(context.race_date)
                gaps.append(gap)
            completed = {key: scalar_tilt(cache['predictions'][context.race_id], ids, gaps,
                         coefficients[key]['gammas'][str(year)]) for key, cache in caches.items()}
            current = {'retained42': completed[('pruning', 42)],
                       'anchor42': caches[('anchor', 42)]['predictions'][context.race_id]}
            for name, keys in MEMBERS.items():
                current[name], excess = scalar_mixture([completed[key] for key in keys], ids)
                jensen[name] = max(jensen[name], excess)
            for name, ps in current.items():
                predictions[name][context.race_id] = ps
            valid.append(race)
            assert all((context.race_id in x) == pop.eligible for x in raw.values())
            if pop.eligible:
                values = loss[context.race_id] = {name: common.nll(ps[pop.winner_horse_id].win) for name, ps in current.items()}
                for c in driver.COMPARISONS:
                    row = raw[c['id']][context.race_id]
                    for field, value in [('candidate_winner_nll', values[c['candidate']]),
                        ('active_winner_nll', values[c['baseline']]), ('diff', values[c['candidate']] - values[c['baseline']])]:
                        close(value, row[field]); maximum = max(maximum, abs(value-row[field]))
        del caches
        print(f'120 independent probabilities PASS year={year}; no fit', flush=True)
    assert len(valid) == 23030 and len(loss) == 22990
    scores = {name: _score_arm(valid, ps, band_edges=[.05, .15, .30]) for name, ps in predictions.items()}
    for c in driver.COMPARISONS:
        r = reports[c['id']]; q = r['gate']['reasons']
        cs, bs = scores[c['candidate']], scores[c['baseline']]
        for value, expected in [(cs.winner_nll, r['periods']['all']['candidate']), (bs.winner_nll, r['periods']['all']['active']),
            (cs.top2_logloss-bs.top2_logloss, q['top2_diff']), (cs.top3_logloss-bs.top3_logloss, q['top3_diff']),
            (cs.ece_equal_width_like['ece'], q['cand_ece']), (bs.ece_equal_width_like['ece'], q['act_ece'])]:
            close(value, expected)
        ci = common.bootstrap(evidence[c['id']]['rows'])
        for key, limits in [('bootstrap_ci', ci['sample']), ('total_ci', ci['total'])]:
            close(limits[0], r[key]['ci_low']); close(limits[1], r[key]['ci_high'])
        for years in (3, 5):
            cutoff = str(_window_start(dt.date(2026, 8, 23), years))
            subset = [x for x in raw[c['id']].values() if x['race_day'] >= cutoff]
            period = r['periods'][f'recent_{years}y']
            close(mean([loss[x['race_id']][c['candidate']] for x in subset]), period['candidate'])
            close(mean([loss[x['race_id']][c['baseline']] for x in subset]), period['active'])
        assert r['mixture_audit']['n_races'] == 23030 and r['mixture_audit']['no_post_average_processing'] is True
        close(jensen[c['candidate']], r['mixture_audit']['max_jensen_excess'])
    stored = driver.a.s.read_json(driver.SPEC / 'verdict.json')
    assert stored['population'] == population
    for key, value in driver.summarize_reports(reports).items():
        assert stored[key] == value
    def retained_comparison(name, baseline):
        r = reports[f'{name}_vs_{baseline}']
        cs, bs = scores[name], scores[baseline]
        quality = (cs.top2_logloss-bs.top2_logloss <= .0005 and cs.top3_logloss-bs.top3_logloss <= .0005
                   and cs.ece_equal_width_like['ece'] - bs.ece_equal_width_like['ece'] <= .001
                   and cs.ece_equal_width_like['ece'] < .05)
        negative = mean([v[name] - v[baseline] for v in loss.values()]) < 0
        unblocked = r['research_disposition']['state'] != 'BLOCKED'
        return quality and negative and unblocked
    retained = [name for name in MEMBERS if all(retained_comparison(name, b) for b in ('retained42', 'anchor42'))]
    preferred_pool = [name for name in retained if name == 'pruning3' or 'pruning3' not in retained or retained_comparison(name, 'pruning3')]
    preferred = min(preferred_pool, key=lambda n: scores[n].winner_nll) if preferred_pool else 'RETAINED_SINGLE_SEED42'
    assert retained == stored['retained_candidates'] and preferred == stored['preferred_research_configuration']
    attrs = driver.a.s.read_json(driver.a.diagnostic.WORK / 'race-diagnostic.json')['rows']
    assert [(r['race_id'], r['race_day']) for r in attrs] == [(r['race_id'], r['race_day']) for r in next(iter(evidence.values()))['rows']]
    groups = {'2026_all': lambda x: x['year'] == 2026,
              '2026_nakayama': lambda x: x['year'] == 2026 and x['venue'] == '06',
              '2026_partial_relative': lambda x: x['year'] == 2026 and x['relative_coverage'] == '(.5,1)'}
    diagnostics = {}
    for name, rule in groups.items():
        selected = [r for r in attrs if rule(r)]
        saved = stored['fixed_diagnostics'][name]
        assert (len(selected), len({r['race_day'] for r in selected})) == (saved['n_races'], saved['n_days'])
        assert saved['new_ci'] is False and saved['extra_gate'] is False
        diagnostics[name] = {}
        for c in driver.COMPARISONS:
            value = mean([loss[r['race_id']][c['candidate']] - loss[r['race_id']][c['baseline']] for r in selected])
            close(value, saved['mean_diffs'][c['id']]); diagnostics[name][c['id']] = value
    driver.verify()
    assert all(driver.verified_result(c, cfg, frozen) for c in driver.COMPARISONS)
    result = {'status': 'PASS', 'artifact_kind': 'independent_fixed_mixture_review', 'can_adopt': False,
        'eligible_for_verdict': False, 'additional_fits': 0, 'method_sha256': driver.a.s.p.digest(__file__),
        'helper_method_sha256': driver.a.s.p.digest(HELPER), 'run_freeze_sha256': driver.a.s.p.digest(driver.WORK / 'run-freeze.json'),
        'summary_sha256': driver.a.s.p.digest(driver.SPEC / 'verdict.json'), 'report_hashes': hashes,
        'population': population, 'member_reconstruction': 'independent scalar exp/fsum, registered eps0 Harville',
        'mixture_reconstruction': 'independent scalar math.fsum for all three heads; no postprocess',
        'checks': common.CHECKS, 'max_numeric_error': common.MAX_ERROR, 'max_race_nll_error': maximum,
        'max_jensen_excess': jensen, 'full_quality_recent_and_primary_cis_match': True,
        'fixed_diagnostics': diagnostics, 'retained_candidates': stored['retained_candidates'],
        'preferred_research_configuration': stored['preferred_research_configuration']}
    driver.write_json(output, result)
    print({'status': 'PASS', 'checks': common.CHECKS, 'max_error': common.MAX_ERROR, 'output': str(output)}, flush=True)


if __name__ == '__main__':
    main()
