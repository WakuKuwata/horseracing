"""Independent119 saved-vector audit. No booster/parameter fitting or upstream writes."""
from __future__ import annotations
import calendar
import datetime as dt
import gc
import math
from pathlib import Path
import statistics
import sys
import types

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import season_gap_recheck as driver
import season_gap_summary as aggregation
from horseracing_eval.dataset import population_masks
from horseracing_eval.gates import _window_start
from horseracing_eval.paired import _score_arm
from horseracing_training.calib_split import assemble_predictions

# Reuse the already reviewed numerical primitives, without executing its main.
HELPER = ROOT / 'specs/118-anchor-gap-recheck/evidence/independent-review.py'
common = types.ModuleType('audit118_primitives')
common.__file__ = str(HELPER)
exec(compile(HELPER.read_text(), str(HELPER), 'exec'), common.__dict__)
close, mean = common.close, common.mean
SEEDS, CONTRASTS = (42, 43, 44), ('anchor', 'retained')


def vector_tilt(base, ids, h, gamma):
    p = common.check_probabilities(base, ids)
    assert np.shape(gamma) == (3,) and np.isfinite(gamma).all()
    # Scalar sum supplies a separate implementation from the driver's matrix product.
    z = np.array([math.fsum(float(x) * float(g) for x, g in zip(row, gamma, strict=True)) for row in h])
    weights = p * np.exp(z - z.max())
    q = weights / weights.sum()
    assert np.isfinite(q).all() and (q > 0).all() and (len(q) == 1 or (q < 1).all())
    close(q.sum(), 1.)
    result = assemble_predictions(ids, q, eps=0.)
    x = common.check_probabilities(result, ids)
    return result, int((q < 1e-6).sum()), float(np.max(np.abs(x - q)))


def main():
    output = Path(__file__).with_suffix('.json')
    assert not output.exists(), 'Preserve existing independent review'
    assert (driver.SPEC / 'verdict.json').exists(), 'Wait for parent119 summary completion'
    cfg, frozen = driver.verify()
    reports, evidence, legacy, hashes, legacy_hashes = {}, {}, {}, {}, {}
    for seed in SEEDS:
        reports[seed], evidence[seed], hashes[str(seed)] = {}, {}, {}
        original = driver.s.read_json(driver.d.result_path(seed, 'anchor'))
        legacy[seed] = driver.s.read_json(original['evidence_path'])
        legacy_hashes[seed] = original['race_id_set_hash']
        for name in CONTRASTS:
            assert driver.verified_result(seed, name, cfg, frozen)
            path = driver.result_path(seed, name)
            report = reports[seed][name] = driver.s.read_json(path)
            evidence[seed][name] = driver.s.read_json(report['evidence_path'])
            hashes[str(seed)][name] = {'report_sha256': driver.s.p.digest(path), 'evidence_sha256': driver.s.p.digest(report['evidence_path'])}
    population = aggregation.validate_population(reports, evidence, legacy, legacy_hashes)
    matrix, races, folds, old_lookup, audit_population = driver.g.load_inputs(driver.s.load_config())
    del old_lookup
    assert audit_population == frozen['sources']['population']
    ids = {r.context.race_id for f in folds.values() for r in f.valid}
    frame = matrix.frame.loc[matrix.frame.race_id.isin(ids), ['race_id', 'horse_id', 'race_date', 'days_since_last', 'sex']]
    h_lookup, sexes = {}, {}
    for rid, hid, day, gap, sex in frame.itertuples(index=False, name=None):
        if pd.isna(gap):
            gap_value = 0.
        else:
            assert math.isfinite(gap) and gap > 0 and float(gap).is_integer()
            gap_value = float(np.log1p(gap))
        assert pd.isna(sex) or sex in ('牡', '牝', 'セ')
        theta = 2 * math.pi * ((day.timetuple().tm_yday - 1) / (366 if calendar.isleap(day.year) else 365))
        female = 0. if pd.isna(sex) or sex != '牝' else 1.
        h_lookup[(rid, hid)] = (str(day), [gap_value, female * math.sin(theta), female * math.cos(theta)])
        sexes[(rid, hid)] = sex
    sex_attrs = []
    for year, fold in sorted(folds.items()):
        if year < 2020:
            continue
        for race in fold.valid:
            c = race.context
            values = [sexes[(c.race_id, h.horse_id)] for h in c.started_horses]
            group = ('missing_sex' if any(pd.isna(x) for x in values) else
                     'all_female' if all(x == '牝' for x in values) else
                     'no_female' if all(x != '牝' for x in values) else 'mixed')
            sex_attrs.append({'race_id': c.race_id, 'race_day': str(c.race_date), 'year': year,
                              'sex_group': group, 'eligible': population_masks(race).eligible})
    input_audit = driver.s.read_json(driver.WORK / 'season-input-audit.json')
    assert sex_attrs == input_audit['race_attributes']
    del matrix, races, frame, sexes
    gc.collect()
    old113 = driver.s.read_json(driver.s.WORK / 'run-freeze.json')
    old116 = driver.s.read_json(driver.d.WORK / 'run-freeze.json')
    values_by_seed, records, qualities = {}, [], {}
    for seed in SEEDS:
        coef = driver.verified_coefficients(seed, frozen)
        old_coef = driver.a.old_coefficients(seed)
        assert coef['coefficient_order'] == ['gap_log', 'female_sin', 'female_cos']
        raw = {name: {r['race_id']: r for r in evidence[seed][name]['rows']} for name in CONTRASTS}
        pred = {name: {} for name in ('candidate', 'anchor', 'retained')}
        losses, prior, valid, fit_checks = {}, [], [], []
        maximum, below, rounding = 0., 0, 0.
        for year, fold in sorted(folds.items()):
            cache = {}
            for arm in (('pruning',) if year == 2019 else ('pruning', 'anchor')):
                path = common.cache_path(seed, arm, year, {}, old116, old113)
                cache[arm] = driver.s.reuse.load(path)
                assert set(cache[arm]['predictions']) == {r.context.race_id for r in fold.valid}
            block = []
            gamma = np.asarray(coef['gammas'][str(year)], dtype=float) if year > 2019 else None
            for race in fold.valid:
                c, pop = race.context, population_masks(race)
                horse_ids = [h.horse_id for h in c.started_horses]
                assert all(h_lookup[(c.race_id, h)][0] == str(c.race_date) for h in horse_ids)
                h = np.array([h_lookup[(c.race_id, horse)][1] for horse in horse_ids])
                base = cache['pruning']['predictions'][c.race_id]
                p = common.check_probabilities(base, horse_ids)
                if pop.eligible:
                    block.append((str(c.race_date), p, h, horse_ids.index(pop.winner_horse_id)))
                if year == 2019:
                    continue
                candidate, low, error = vector_tilt(base, horse_ids, h, gamma)
                retained, _, _ = common.tilt(base, horse_ids, h[:, 0], old_coef['gammas'][str(year)])
                anchor = cache['anchor']['predictions'][c.race_id]
                common.check_probabilities(anchor, horse_ids)
                below += low; rounding = max(rounding, error)
                for name, pvalue in [('candidate', candidate), ('anchor', anchor), ('retained', retained)]:
                    pred[name][c.race_id] = pvalue
                valid.append(race)
                assert all((c.race_id in raw[name]) == pop.eligible for name in CONTRASTS)
                if pop.eligible:
                    v = losses[c.race_id] = {name: common.nll(ps[c.race_id][pop.winner_horse_id].win) for name, ps in pred.items()}
                    for name in CONTRASTS:
                        for field, value in [('candidate_winner_nll', v['candidate']), ('active_winner_nll', v[name]), ('diff', v['candidate'] - v[name])]:
                            close(value, raw[name][c.race_id][field])
                            maximum = max(maximum, abs(value - raw[name][c.race_id][field]))
            if year > 2019:
                saved = coef['fit_diagnostics'][year - 2020]
                assert saved['eval_year'] == year and saved['fit_races'] == len(prior)
                assert saved['fit_last_day'] == max(x[0] for x in prior) < min(x[0] for x in block) == saved['eval_first_day']
                assert all(int(x[0][:4]) < year for x in prior)
                objective_terms, gradient = [], np.zeros(3)
                for day, p, h, winner in prior:
                    p = p / p.sum(); z = h @ gamma; z -= z.max()
                    w = p * np.exp(z)
                    objective_terms.append(float(-z[winner] + np.log(w.sum())))
                    gradient += (w / w.sum()) @ h - h[winner]
                objective = float(np.mean(objective_terms) + .5e-6 * (gamma @ gamma))
                norm = float(np.max(np.abs(gradient / len(prior) + 1e-6 * gamma)))
                close(objective, saved['regularized_fit_objective']); close(norm, saved['gradient_inf'])
                assert objective <= 1e-8 and norm <= 1e-5
                fit_checks.append({'year': year, 'gamma': gamma.tolist(), 'fit_races': len(prior), 'objective': objective, 'gradient_inf': norm})
            else:
                assert len(block) == coef['warmup_eligible_races'] == 3447
            prior.extend(block); del cache
        assert len(valid) == 23030 and len(losses) == 22990
        scores = {name: _score_arm(valid, ps, band_edges=[.05, .15, .30]) for name, ps in pred.items()}
        qualities[seed] = {}
        for name in CONTRASTS:
            report = reports[seed][name]; q = report['gate']['reasons']
            cs, bs = scores['candidate'], scores[name]
            qualities[seed][name] = [cs.top2_logloss-bs.top2_logloss, cs.top3_logloss-bs.top3_logloss,
                                    cs.ece_equal_width_like['ece'], bs.ece_equal_width_like['ece']]
            for value, expected in [(cs.winner_nll, report['periods']['all']['candidate']), (bs.winner_nll, report['periods']['all']['active']),
                (cs.top2_logloss-bs.top2_logloss, q['top2_diff']), (cs.top3_logloss-bs.top3_logloss, q['top3_diff']),
                (cs.ece_equal_width_like['ece'], q['cand_ece']), (bs.ece_equal_width_like['ece'], q['act_ece'])]:
                close(value, expected)
            ci = common.bootstrap(evidence[seed][name]['rows'])
            for key, limits in [('bootstrap_ci', ci['sample']), ('total_ci', ci['total'])]:
                close(limits[0], report[key]['ci_low']); close(limits[1], report[key]['ci_high'])
            for years in (3, 5):
                cutoff = str(_window_start(dt.date(2026, 8, 23), years))
                subset = [x for x in raw[name].values() if x['race_day'] >= cutoff]
                period = report['periods'][f'recent_{years}y']
                close(mean([losses[x['race_id']]['candidate'] for x in subset]), period['candidate'])
                close(mean([losses[x['race_id']][name] for x in subset]), period['active'])
            assert report['assembly_audit']['below_legacy_clip_horses'] == below
            close(rounding, report['assembly_audit']['max_post_assembly_win_change'])
        values_by_seed[seed] = losses
        records.append({'seed': seed, 'all_races': len(valid), 'eligible': len(losses), 'max_nll_error': maximum,
                        'vector_fit_checks': fit_checks, 'quality_recent_and_primary_cis_match': True})
        del pred, scores, prior, valid; gc.collect()
        print(f'119 independent PASS seed={seed}; no fit', flush=True)
    stored = driver.s.read_json(driver.SPEC / 'verdict.json')
    assert stored['population'] == population
    for key, value in aggregation.summarize_reports(reports).items():
        assert stored[key] == value
    for name in CONTRASTS:
        m = mean([mean([r['candidate'] - r[name] for r in values_by_seed[seed].values()]) for seed in SEEDS])
        close(m, stored['contrasts'][name]['equal_seed_mean_periods']['all']['diff']['mean'])
    def quality_pass(q):
        return q[0] <= .0005 and q[1] <= .0005 and q[2] - q[3] <= .001 and q[2] < .05
    review = any(reports[s][n]['research_disposition']['state'] == 'BLOCKED' or not quality_pass(qualities[s][n])
                 for s in SEEDS for n in CONTRASTS)
    review |= any(not quality_pass([mean([qualities[s][n][k] for s in SEEDS]) for k in range(4)]) for n in CONTRASTS)
    improves = all(mean([mean([r['candidate']-r[n] for r in values_by_seed[s].values()]) for s in SEEDS]) < 0 for n in CONTRASTS)
    independent_decision = 'QUALITY_REVIEW_REQUIRED' if review else 'SEASON_GAP_RETAINED' if improves else 'SEASON_GAP_DEFERRED'
    assert stored['research_decision'] == independent_decision
    group_selections = {}
    for period in ('all', '2026'):
        for group in ('no_female', 'mixed', 'all_female', 'missing_sex'):
            selected = [r for r in sex_attrs if r['eligible'] and r['sex_group'] == group and (period == 'all' or r['year'] == 2026)]
            saved = stored['seasonal_sex_diagnostics']['groups'][period][group]
            assert (len(selected), len({r['race_day'] for r in selected})) == (saved['n_eligible'], saved['n_days'])
            group_selections[f'{period}:{group}'] = len(selected)
            for name in CONTRASTS:
                means = {k: [] for k in ('candidate', 'active', 'diff')}
                for seed in SEEDS:
                    for key, field in [('candidate', 'candidate'), ('active', name), ('diff', None)]:
                        v = None if not selected else mean([values_by_seed[seed][r['race_id']][field] if field else
                            values_by_seed[seed][r['race_id']]['candidate']-values_by_seed[seed][r['race_id']][name] for r in selected])
                        expected = saved['contrasts'][name]['by_seed'][str(seed)][key]
                        if v is None: assert expected is None
                        else: close(v, expected); means[key].append(v)
                for key, v in means.items():
                    expected = saved['contrasts'][name]['equal_seed_mean'][key]
                    if not v: assert expected is None
                    else: close(mean(v), expected)
    attrs = driver.s.read_json(ROOT / 'artifacts/117-pruning-2026-diagnostic/race-diagnostic.json')['rows']
    for group, select in {'2026_all': lambda r:r['year']==2026,
        '2026_nakayama': lambda r:r['year']==2026 and r['venue']=='06',
        '2026_partial_relative': lambda r:r['year']==2026 and r['relative_coverage']=='(.5,1)'}.items():
        selected = [r for r in attrs if select(r)]
        for name in CONTRASTS:
            for seed in SEEDS:
                v = stored['fixed117_diagnostics']['groups'][group]['contrasts'][name]['by_seed'][str(seed)]
                c = mean([values_by_seed[seed][r['race_id']]['candidate'] for r in selected])
                b = mean([values_by_seed[seed][r['race_id']][name] for r in selected])
                close(c, v['candidate']); close(b, v['active']); close(c-b, v['diff'])
    assert driver.verify() == (cfg, frozen)
    for seed in SEEDS:
        for name in CONTRASTS: assert driver.verified_result(seed, name, cfg, frozen)
    driver.write_json(output, {'status':'PASS', 'artifact_kind':'season_gap_independent_review', 'can_adopt':False,
        'eligible_for_verdict':False, 'additional_fits':0, 'method_sha256':driver.s.p.digest(__file__),
        'helper_method_sha256':driver.s.p.digest(HELPER), 'run_freeze_sha256':driver.s.p.digest(driver.WORK/'run-freeze.json'),
        'summary_sha256':driver.s.p.digest(driver.SPEC/'verdict.json'), 'report_hashes':hashes, 'population':population,
        'seeds':records, 'checks':common.CHECKS, 'max_numerical_error':common.MAX_ERROR,
        'sex_group_sizes':group_selections, 'independent_season_and_sex_assignment':True,
        'research_decision':stored['research_decision'], 'notes':['No booster or gamma fit;63 saved coefficient components evaluated.',
            'Registered Harville and scoring primitives reused; seasonal inputs, vector tilt, objectives, gradients and groups reconstructed.',
            'Primary sample/total CI and means checked; no new confidence or production assertion.']})
    print(f'119 independent PASS {output}', flush=True)


if __name__ == '__main__': main()
