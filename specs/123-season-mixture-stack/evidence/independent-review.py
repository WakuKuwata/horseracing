"""123 saved-forecast numerical audit. No booster or coefficient fitting."""
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
import season_mixture_stack as driver
from horseracing_eval.dataset import population_masks
from horseracing_eval.gates import _window_start
from horseracing_eval.paired import _score_arm


def helper(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
    return module


HELPERS = {n: ROOT / f'specs/{path}/evidence/independent-review.py' for n, path in
           [('season', '119-season-gap-recheck'), ('mixture', '120-probability-mixture')]}
season = helper('audit119_primitives', HELPERS['season'])
mixture = helper('audit120_primitives', HELPERS['mixture'])
common = mixture.common
close, mean = common.close, common.mean
SEEDS = (42, 43, 44)
MEMBERS = {'joint_pruning3': [('joint_pruning', s) for s in SEEDS],
           'joint_mixed6': [('joint_pruning', s) for s in SEEDS] + [('anchor_gap', s) for s in SEEDS]}


def main():
    output = Path(__file__).with_suffix('.json')
    assert not output.exists(), 'Preserve existing independent review'
    assert (driver.SPEC / 'verdict.json').exists(), 'Wait for parent123 summary completion'
    cfg, frozen = driver.verify()
    assert MEMBERS == driver.MEMBERS
    reports, evidence, hashes = {}, {}, {}
    for c in driver.COMPARISONS:
        assert driver.verified_result(c, cfg, frozen)
        path = driver.result_path(c['id'])
        r = reports[c['id']] = driver.a.s.read_json(path)
        evidence[c['id']] = driver.a.s.read_json(r['evidence_path'])
        hashes[c['id']] = {'report_sha256': driver.a.s.p.digest(path), 'evidence_sha256': driver.a.s.p.digest(r['evidence_path'])}
    population = driver.validate_population(reports, evidence)
    matrix, races, folds, lookup, input_population, sex_audit = driver.n.load_inputs()
    assert input_population == frozen['sources']['population']
    assert sex_audit == driver.a.s.read_json(driver.n.WORK / 'season-input-audit.json')
    # Method/input formulas were separately audited in119; check that the same
    # frozen input population is used here, without refitting any coefficient.
    del matrix, races
    gc.collect()
    old113 = driver.a.s.read_json(driver.a.s.WORK / 'run-freeze.json')
    old116 = driver.a.s.read_json(driver.a.d.WORK / 'run-freeze.json')
    old118 = driver.a.s.read_json(driver.a.WORK / 'run-freeze.json')
    old119 = driver.a.s.read_json(driver.n.WORK / 'run-freeze.json')
    coefficients = {
        ('joint_pruning', s): driver.n.verified_coefficients(s, old119) for s in SEEDS}
    coefficients.update({('pruning', s): driver.a.old_coefficients(s) for s in SEEDS})
    coefficients.update({('anchor_gap', s): driver.a.verified_coefficients(s, old118) for s in SEEDS})
    names = [*MEMBERS, 'pruning3', 'mixed6', 'anchor42']
    predictions = {name: {} for name in names}
    losses, valid = {}, []
    jensen = {name: -math.inf for name in MEMBERS}
    raw = {name: {r['race_id']: r for r in e['rows']} for name, e in evidence.items()}
    maximum = 0.
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
            h = []
            for horse in ids:
                day, vector = lookup[(context.race_id, horse)]
                assert str(day) == str(context.race_date) and len(vector) == 3
                h.append([0. if math.isnan(v) else v for v in vector])
            h = np.asarray(h, dtype=float)
            assert np.isfinite(h).all()
            completed = {}
            for seed in SEEDS:
                base = caches[('pruning', seed)]['predictions'][context.race_id]
                completed[('joint_pruning', seed)], _, _ = season.vector_tilt(base, ids, h,
                    np.asarray(coefficients[('joint_pruning', seed)]['gammas'][str(year)]))
                completed[('pruning', seed)] = mixture.scalar_tilt(base, ids, h[:, 0],
                    coefficients[('pruning', seed)]['gammas'][str(year)])
                completed[('anchor_gap', seed)] = mixture.scalar_tilt(caches[('anchor', seed)]['predictions'][context.race_id],
                    ids, h[:, 0], coefficients[('anchor_gap', seed)]['gammas'][str(year)])
            current = {'anchor42': caches[('anchor', 42)]['predictions'][context.race_id]}
            current['pruning3'], _ = mixture.scalar_mixture([completed[('pruning', s)] for s in SEEDS], ids)
            current['mixed6'], _ = mixture.scalar_mixture([completed[(k, s)] for k in ('pruning', 'anchor_gap') for s in SEEDS], ids)
            for name, keys in MEMBERS.items():
                current[name], excess = mixture.scalar_mixture([completed[key] for key in keys], ids)
                jensen[name] = max(jensen[name], excess)
            for name, ps in current.items():
                predictions[name][context.race_id] = ps
            valid.append(race)
            assert all((context.race_id in x) == pop.eligible for x in raw.values())
            if pop.eligible:
                values = losses[context.race_id] = {name: common.nll(ps[pop.winner_horse_id].win) for name, ps in current.items()}
                for c in driver.COMPARISONS:
                    row = raw[c['id']][context.race_id]
                    for field, value in [('candidate_winner_nll', values[c['candidate']]),
                        ('active_winner_nll', values[c['baseline']]), ('diff', values[c['candidate']] - values[c['baseline']])]:
                        close(value, row[field]); maximum = max(maximum, abs(value-row[field]))
        del caches
        print(f'123 independent probabilities PASS year={year}; no fit', flush=True)
    assert len(valid) == 23030 and len(losses) == 22990
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
            close(mean([losses[x['race_id']][c['candidate']] for x in subset]), period['candidate'])
            close(mean([losses[x['race_id']][c['baseline']] for x in subset]), period['active'])
        assert r['mixture_audit']['n_races'] == 23030 and r['mixture_audit']['no_post_average_processing'] is True
        close(jensen[c['candidate']], r['mixture_audit']['max_jensen_excess'])
    stored = driver.a.s.read_json(driver.SPEC / 'verdict.json')
    assert stored['population'] == population
    for key, value in driver.summarize_reports(reports).items():
        assert stored[key] == value
    quality = {}
    for c in driver.COMPARISONS:
        cs, bs = scores[c['candidate']], scores[c['baseline']]
        quality[c['id']] = (cs.top2_logloss-bs.top2_logloss <= .0005 and cs.top3_logloss-bs.top3_logloss <= .0005
            and cs.ece_equal_width_like['ece']-bs.ece_equal_width_like['ece'] <= .001 and cs.ece_equal_width_like['ece'] < .05
            and reports[c['id']]['research_disposition']['state'] != 'BLOCKED')
    def improves(name, baseline):
        return quality[f'{name}_vs_{baseline}'] and mean([v[name]-v[baseline] for v in losses.values()]) < 0
    retained = []
    if all(improves('joint_pruning3', b) for b in ('pruning3', 'anchor42')):
        retained.append('joint_pruning3')
    if all(improves('joint_mixed6', b) for b in ('mixed6', 'anchor42')) and all(quality[f'joint_mixed6_vs_{b}'] for b in ('pruning3','mixed6','anchor42')):
        retained.append('joint_mixed6')
    eligible = [name for name in retained if improves(name, 'mixed6') and all(quality[f'{name}_vs_{b}'] for b in ('pruning3','mixed6','anchor42'))]
    preferred = min(eligible, key=lambda name:scores[name].winner_nll) if eligible else 'mixed6'
    assert retained == stored['retained_candidates'] and preferred == stored['preferred_research_configuration']
    assert quality == stored['quality_by_comparison']
    attrs = driver.a.s.read_json(driver.a.diagnostic.WORK / 'race-diagnostic.json')['rows']
    assert [(x['race_id'],x['race_day']) for x in attrs] == [(x['race_id'],x['race_day']) for x in next(iter(evidence.values()))['rows']]
    groups = {'2026_all': lambda x:x['year']==2026,
              '2026_nakayama': lambda x:x['year']==2026 and x['venue']=='06',
              '2026_partial_relative': lambda x:x['year']==2026 and x['relative_coverage']=='(.5,1)'}
    for name, rule in groups.items():
        selected = [x for x in attrs if rule(x)]
        saved = stored['fixed_diagnostics'][name]
        assert (len(selected),len({x['race_day'] for x in selected})) == (saved['n_races'],saved['n_days'])
        assert saved['new_ci'] is False and saved['extra_gate'] is False
        for c in driver.COMPARISONS:
            close(mean([losses[x['race_id']][c['candidate']]-losses[x['race_id']][c['baseline']] for x in selected]), saved['mean_diffs'][c['id']])
    driver.verify()
    assert all(driver.verified_result(c,cfg,frozen) for c in driver.COMPARISONS)
    driver.write_json(output, {'status':'PASS','artifact_kind':'independent_season_mixture_review','can_adopt':False,
        'eligible_for_verdict':False,'additional_fits':0,'method_sha256':driver.a.s.p.digest(__file__),
        'helper_method_sha256':{name:driver.a.s.p.digest(path) for name,path in HELPERS.items()},
        'run_freeze_sha256':driver.a.s.p.digest(driver.WORK/'run-freeze.json'),
        'summary_sha256':driver.a.s.p.digest(driver.SPEC/'verdict.json'),'report_hashes':hashes,'population':population,
        'checks':common.CHECKS+season.common.CHECKS,'max_numerical_error':max(common.MAX_ERROR,season.common.MAX_ERROR),
        'max_race_nll_error':maximum,'full_quality_recent_and_primary_cis_match':True,'fixed117_groups_match':True,
        'saved_coefficients_reused_without_refit':True,'max_jensen_excess':jensen,
        'retained_candidates':retained,'preferred_research_configuration':preferred})
    print(f'123 independent PASS {output}', flush=True)


if __name__ == '__main__':
    main()
