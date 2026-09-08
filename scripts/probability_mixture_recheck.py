"""120: fixed finite mixtures of completed forecasts; no training or recalibration."""
from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as dt
import gc
from pathlib import Path
import statistics
import time

import numpy as np
import anchor_gap_recheck as a
import gap_seed_summary as stats
from horseracing_eval.predictor import Prediction

ROOT = a.ROOT
SPEC = ROOT / 'specs/120-probability-mixture'
WORK = ROOT / 'artifacts/120-probability-mixture'
SEEDS = (42, 43, 44)
MEMBERS = {
    'pruning3': [('pruning', s) for s in SEEDS],
    'anchor3': [('anchor', s) for s in SEEDS],
    'mixed6': [('pruning', s) for s in SEEDS] + [('anchor', s) for s in SEEDS],
}
COMPARISONS = [dict(id=f'{c}_vs_{b}', candidate=c, baseline=b)
              for c in MEMBERS for b in ('retained42', 'anchor42')]
COMPARISONS += [dict(id=f'{c}_vs_pruning3', candidate=c, baseline='pruning3') for c in ('anchor3', 'mixed6')]
GATE_KEYS = ('evaluation_contract_version', 'primary_metric', 'min_effect_delta', 'delta_derivation_ref',
             'seed_noise', 'bootstrap', 'eval_window', 'recent_guard', 'top_noninferior', 'calibration', 'subgroup_guard')
write_json = a.write_json


def load_config():
    cfg = a.s.read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    old = a.load_config()
    if a.s.p.gate_config_hash(cfg) != expected or any(cfg[k] != old[k] for k in GATE_KEYS):
        raise ValueError('120 registered gate changed')
    a.s.p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    a.s.p.assert_delta_provenance(cfg, root=ROOT)
    if (cfg['comparisons'] != COMPARISONS or cfg['members'] != {k: [list(x) for x in v] for k, v in MEMBERS.items()}
        or cfg['aggregation'] != 'float64_arithmetic_mean_all_three_heads_no_postprocess'
        or cfg['additional_fits'] != 0 or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('120 fixed mixture scope changed')
    return cfg


def source_hash():
    return a.s.p.stable_hash({'method': a.s.p.digest(__file__), '118_source': a.source_hash()})


def source_state():
    cfg, frozen = a.verify()
    if (a.WORK / 'running.lock').exists() or not all(a.completed(j) for j in frozen['jobs']):
        raise ValueError('118 completed frozen sources required')
    paths = [a.WORK / 'run-freeze.json', a.SPEC / 'verdict.json', a.SPEC / 'evidence/independent-review.json',
             a.SPEC / 'evidence/independent-review.py']
    review = a.s.read_json(paths[2])
    if (review['status'] != 'PASS' or review['method_sha256'] != a.s.p.digest(paths[3])
        or review['summary_sha256'] != a.s.p.digest(paths[1]) or review['run_freeze_sha256'] != a.s.p.digest(paths[0])):
        raise ValueError('118 independent review mismatch')
    for seed in SEEDS:
        paths.extend([a.seed_area(seed) / 'coefficients.json', a.seed_area(seed) / 'coefficients-receipt.json'])
        for c in a.CONTRASTS:
            if not a.verified_result(seed, c, cfg, frozen):
                raise ValueError('118 complete member evidence required')
            p = a.result_path(seed, c['id']); r = a.s.read_json(p)
            hashes = {'report_sha256': a.s.p.digest(p), 'evidence_sha256': a.s.p.digest(r['evidence_path'])}
            if review['report_hashes'][str(seed)][c['id']] != hashes:
                raise ValueError('118 reviewed member report changed')
            paths.extend([p, Path(r['evidence_path']), a.seed_area(seed) / f"{c['id']}-receipt.json"])
    for j in frozen['jobs']:
        paths.extend([a.receipt_path(j['key']), a.WORK / 'cache' / f"{j['key']}.pkl"])
    return {'files': {str(p): a.s.p.digest(p) for p in paths}, 'population': frozen['sources']['population']}


def verify():
    cfg = load_config(); frozen = a.s.read_json(WORK / 'run-freeze.json')
    if (frozen['config_hash'] != a.s.p.gate_config_hash(cfg) or frozen['source_hash'] != source_hash()
        or frozen['runtime'] != a.s.runtime() or frozen['sources'] != source_state()):
        raise ValueError('120 frozen source/config/runtime changed')
    return cfg, frozen


def matrix_values(predictions, ids):
    if set(predictions) != set(ids) or len(ids) != len(set(ids)) or not ids:
        raise ValueError('Missing/extra/duplicate mixture horse')
    x = np.array([[predictions[h].win, predictions[h].top2, predictions[h].top3] for h in ids], dtype=np.float64)
    if (not np.isfinite(x).all() or (x < -1e-10).any() or (x > 1 + 1e-10).any()
        or (np.diff(x, axis=1) < -1e-10).any() or (x[:, 0] <= 0).any()
        or not np.allclose(x.sum(0), [min(k, len(ids)) for k in (1, 2, 3)], rtol=0, atol=1e-8)):
        raise ValueError('Invalid mixture member/head probabilities')
    return x


def average_predictions(members, ids, expected_count):
    if len(members) != expected_count or expected_count not in (3, 6):
        raise ValueError('All registered members required; no partial average')
    x = np.stack([matrix_values(p, ids) for p in members])
    averaged = x.mean(axis=0, dtype=np.float64)
    result = {h: Prediction(*map(float, averaged[i])) for i, h in enumerate(ids)}
    matrix_values(result, ids)
    # Jensen is an implementation check against the member mean, not the best seed.
    excess = float(np.max(-np.log(averaged[:, 0]) - (-np.log(x[:, :, 0])).mean(0)))
    if excess > 1e-12:
        raise ValueError('Arithmetic-mixture Jensen invariant failed')
    return result, excess


class MixturePredictor:
    is_leaky_reference = False

    def __init__(self, predictors, count, audit):
        self.predictors, self.count, self.audit = predictors, count, audit

    def predict_race(self, context):
        ids = [h.horse_id for h in context.started_horses]
        p, excess = average_predictions([x.predict_race(context) for x in self.predictors], ids, self.count)
        self.audit[context.race_id] = excess
        return p


class MixtureFactory:
    def __init__(self, name, members):
        if name not in MEMBERS or len(members) != len(MEMBERS[name]):
            raise ValueError('Unknown/incomplete registered mixture')
        self.members, self.name, self.audit = members, name, {}
        self.expected_columns = sorted({c for m in members for c in m.expected_columns})
        self.recipe_meta = {'method': 'finite_mixture_of_completed_forecasts', 'name': name,
            'ordered_members': [{'kind': k, 'seed': s, 'recipe_hash': m.recipe_hash,
                                 'recipe_meta': m.recipe_meta, 'weight': 1 / len(members)}
                                for (k, s), m in zip(MEMBERS[name], members, strict=True)],
            'dtype': 'float64', 'heads': ['win', 'top2', 'top3'], 'aggregation': 'arithmetic_mean',
            'post_aggregation_processing': None, 'recalibration': None}
        self.recipe_hash = a.s.p.stable_hash(self.recipe_meta)

    def fit(self, train_races, *, num_threads=None):
        return MixturePredictor([m.fit(train_races, num_threads=1) for m in self.members], len(self.members), self.audit)


def build_factory(name, matrix, races, lookup):
    cfg = a.load_config(); frozen = a.s.read_json(a.WORK / 'run-freeze.json')
    def member(kind, seed):
        if kind == 'pruning':
            return a.old_tilt(matrix, races, lookup, seed)
        return a.g.TiltFactory(a.AnchorFactory(cfg, frozen, matrix, races, seed), lookup, a.verified_coefficients(seed, frozen))
    if name == 'retained42':
        return member('pruning', 42)
    if name == 'anchor42':
        return a.AnchorFactory(cfg, frozen, matrix, races, 42)
    if name not in MEMBERS:
        raise ValueError('Unregistered mixture or baseline')
    return MixtureFactory(name, [member(k, s) for k, s in MEMBERS[name]])


def result_path(name):
    if name not in {c['id'] for c in COMPARISONS}:
        raise ValueError('Unregistered comparison')
    return SPEC / 'evidence' / f'{name}.json'


def validate_population(reports, evidence):
    old_report = a.s.read_json(a.d.result_path(42, 'anchor'))
    old = a.s.read_json(old_report['evidence_path'])['rows']
    identity = [(r['race_id'], r['race_day']) for r in old]
    candidate_rows = {}
    race_hash = old_report['race_id_set_hash']
    for c in COMPARISONS:
        name = c['id']; r = reports[name]; rows = evidence[name]['rows']
        if (len(rows) != 22990 or len({x['race_id'] for x in rows}) != 22990
            or len({x['race_day'] for x in rows}) != 715 or r['n_races'] != 23030 or r['n_eligible'] != 22990
            or [(x['race_id'], x['race_day']) for x in rows] != identity):
            raise ValueError('120 population differs from frozen member baseline')
        if not race_hash or r.get('race_id_set_hash') != race_hash:
            raise ValueError('120 declared race set hashes differ')
        if c['candidate'] in candidate_rows and any(x['candidate_winner_nll'] != y['candidate_winner_nll']
                for x, y in zip(rows, candidate_rows[c['candidate']], strict=True)):
            raise ValueError('Same mixture predictions differ across contrasts')
        candidate_rows[c['candidate']] = rows
        if c['baseline'] in ('anchor42', 'retained42'):
            field = 'active_winner_nll' if c['baseline'] == 'anchor42' else 'candidate_winner_nll'
            if any(x['active_winner_nll'] != y[field] for x, y in zip(rows, old, strict=True)):
                raise ValueError('Fixed single-seed baseline differs from original116')
        for x in rows:
            stats.close(x['candidate_winner_nll'] - x['active_winner_nll'], x['diff'])
        for role, field in [('candidate', 'candidate_winner_nll'), ('active', 'active_winner_nll'), ('diff', 'diff')]:
            stats.close(statistics.mean(x[field] for x in rows), r['periods']['all'][role])
    for c in COMPARISONS:
        if c['baseline'] == 'pruning3' and any(x['active_winner_nll'] != y['candidate_winner_nll']
                for x, y in zip(evidence[c['id']]['rows'], candidate_rows['pruning3'], strict=True)):
            raise ValueError('Mixture baseline differs from its own candidate evidence')
    return {'n_races': 23030, 'n_eligible': 22990, 'n_days': 715, 'same_ordered_population': True,
            'old_single_seed_baselines_exact': True, 'mixture_identity_across_comparisons_exact': True}


def report_disposition(r):
    if r.get('can_adopt') is not False or r.get('eligible_for_verdict') is not False:
        raise ValueError('Research-only evidence required')
    if any(type(r.get('gate', {}).get(k)) is not bool for k in ('recent_guard', 'top_noninferior', 'calibration')):
        raise ValueError('Explicit quality flags required')
    critical = r.get('subgroups', {}).get('critical')
    if not isinstance(critical, list) or len(critical) != 3 or set(critical) != {'canonical', 'nk', 'recent_year_only'}:
        raise ValueError('Fixed critical groups required')
    disp = a.s.research.assess_research(r)
    invalid = [x for x in disp['blocking_reasons'] if not x.startswith(
        ('quality_guard_not_passed:', 'critical_subgroup_fail:', 'subgroup_guard_fail'))]
    if invalid or disp != r.get('research_disposition'):
        raise ValueError('Incomplete/changed research evidence')
    for period in ('all', 'recent_3y', 'recent_5y'):
        p = r['periods'][period]
        stats.close(stats.finite(p['candidate']) - stats.finite(p['active']), p['diff'])
    return disp


def numeric_quality(r):
    q = r['gate']['reasons']
    return (q['top2_diff'] <= .0005 and q['top3_diff'] <= .0005
            and q['cand_ece'] - q['act_ece'] <= .001 and q['cand_ece'] < .05)


def summarize_reports(reports):
    if set(reports) != {x['id'] for x in COMPARISONS}:
        raise ValueError('All eight comparisons required')
    dispositions = {k: report_disposition(r) for k, r in reports.items()}
    retained = [c for c in MEMBERS if all(dispositions[f'{c}_vs_{b}']['state'].startswith('RETAIN_')
                and numeric_quality(reports[f'{c}_vs_{b}'])
                for b in ('retained42', 'anchor42'))]
    eligible = [c for c in retained if c == 'pruning3' or 'pruning3' not in retained
                or (dispositions[f'{c}_vs_pruning3']['state'].startswith('RETAIN_') and numeric_quality(reports[f'{c}_vs_pruning3']))]
    preferred = min(eligible, key=lambda c: reports[f'{c}_vs_retained42']['periods']['all']['candidate']) if eligible else 'RETAINED_SINGLE_SEED42'
    return {'artifact_kind': 'finite_probability_mixture_research_summary', 'can_adopt': False,
        'eligible_for_verdict': False, 'retained_candidates': retained, 'preferred_research_configuration': preferred,
        'bundle_replicates': 1, 'new_model_fits': 0, 'dispositions': dispositions,
        'comparisons': {k: {'periods': r['periods'], 'total_ci': r['total_ci'], 'quality': r['gate'],
                           'subgroups': r['subgroups']} for k, r in reports.items()},
        'limitations': load_config()['limitations']}


def verified_result(c, cfg, frozen):
    p = result_path(c['id'])
    if not p.exists():
        return False
    r = a.s.read_json(p); evidence = WORK / f"{c['id']}-evidence.json"
    receipt = a.s.read_json(WORK / f"{c['id']}-receipt.json")
    if (receipt != {'report_sha256': a.s.p.digest(p), 'evidence_sha256': a.s.p.digest(evidence),
                    'run_freeze_sha256': a.s.p.digest(WORK / 'run-freeze.json')}
        or r.get('artifact_kind') != 'finite_probability_mixture_research_report'
        or r.get('comparison') != c or r.get('study_config_hash') != a.s.p.gate_config_hash(cfg)
        or r.get('run_freeze_sha256') != a.s.p.digest(WORK / 'run-freeze.json')
        or r.get('evidence_path') != str(evidence) or r.get('evidence_sha256') != a.s.p.digest(evidence)):
        raise ValueError('120 existing report provenance differs')
    report_disposition(r)
    return True


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify(); return
    cfg = load_config(); before = source_hash(); sources = source_state()
    if before != source_hash():
        raise ValueError('Method changed during preparation')
    write_json(WORK / 'run-freeze.json', {'source_hash': before, 'config_hash': a.s.p.gate_config_hash(cfg),
        'runtime': a.s.runtime(), 'sources': sources, 'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'can_adopt': False, 'eligible_for_verdict': False, 'additional_fits': 0})
    print('PREPARE PASS three fixed mixtures/eight comparisons', flush=True)


def evaluate():
    cfg, frozen = verify()
    matrix, races, folds, lookup, audit = a.g.load_inputs(a.s.load_config())
    if audit != frozen['sources']['population']:
        raise ValueError('Frozen input population changed')
    for c in COMPARISONS:
        if verified_result(c, cfg, frozen):
            continue
        ep = WORK / f"{c['id']}-evidence.json"
        if ep.exists():
            raise ValueError('Preserve orphan120 evidence for diagnosis')
        candidate = build_factory(c['candidate'], matrix, races, lookup)
        baseline = build_factory(c['baseline'], matrix, races, lookup)
        t0 = time.monotonic()
        report = a.s.p.paired_eval(candidate, baseline, races, gate_config=cfg, first_valid_year=2020,
            valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1,
            snapshot={'run_freeze_sha256': a.s.p.digest(WORK / 'run-freeze.json'), 'comparison': c,
                      'evidence_regime': 'historical_fixed_completed_predictor_mixture'})
        if report.n_races != 23030 or report.n_eligible != 22990 or len(candidate.audit) != 23030:
            raise ValueError('Scored mixture population incomplete')
        result = report.to_dict(); result.pop('evidence', None); result.pop('diffs_by_day', None)
        result['gate_readout'] = result.pop('decision')
        result.update(artifact_kind='finite_probability_mixture_research_report', can_adopt=False, eligible_for_verdict=False,
            comparison=c, study_config_hash=a.s.p.gate_config_hash(cfg),
            run_freeze_sha256=a.s.p.digest(WORK / 'run-freeze.json'), elapsed_seconds=time.monotonic()-t0,
            mixture_audit={'n_races': len(candidate.audit), 'max_jensen_excess': max(candidate.audit.values()),
                           'no_post_average_processing': True}, limitations=cfg['limitations'])
        result['research_disposition'] = a.s.research.assess_research(result)
        verify()
        write_json(ep, report.evidence.to_dict())
        result.update(evidence_path=str(ep), evidence_sha256=a.s.p.digest(ep))
        out = result_path(c['id']); write_json(out, result)
        write_json(WORK / f"{c['id']}-receipt.json", {'report_sha256': a.s.p.digest(out),
            'evidence_sha256': a.s.p.digest(ep), 'run_freeze_sha256': a.s.p.digest(WORK / 'run-freeze.json')})
        print(f"RESULT {c['id']} {result['periods']['all']['diff']:+.8f} {result['research_disposition']['state']}", flush=True)
        del candidate, baseline, report; gc.collect()


def summarize():
    cfg, frozen = verify(); reports, evidence, hashes = {}, {}, {}
    for c in COMPARISONS:
        if not verified_result(c, cfg, frozen):
            raise ValueError('All eight completed comparisons required')
        p = result_path(c['id']); r = reports[c['id']] = a.s.read_json(p)
        evidence[c['id']] = a.s.read_json(r['evidence_path'])
        hashes[c['id']] = {'report_sha256': a.s.p.digest(p), 'evidence_sha256': a.s.p.digest(r['evidence_path'])}
    result = summarize_reports(reports)
    result['population'] = validate_population(reports, evidence)
    attrs = a.s.read_json(a.diagnostic.WORK / 'race-diagnostic.json')['rows']
    identity = [(x['race_id'], x['race_day']) for x in attrs]
    if identity != [(x['race_id'], x['race_day']) for x in next(iter(evidence.values()))['rows']]:
        raise ValueError('Fixed117 diagnostic attributes differ')
    rules = {'2026_all': lambda x: x['year'] == 2026, '2026_nakayama': lambda x: x['year'] == 2026 and x['venue'] == '06',
             '2026_partial_relative': lambda x: x['year'] == 2026 and x['relative_coverage'] == '(.5,1)'}
    result['fixed_diagnostics'] = {}
    for name, rule in rules.items():
        idx = [i for i, x in enumerate(attrs) if rule(x)]
        result['fixed_diagnostics'][name] = {'n_races': len(idx), 'n_days': len({attrs[i]['race_day'] for i in idx}),
            'new_ci': False, 'extra_gate': False, 'mean_diffs': {k: statistics.mean(e['rows'][i]['diff'] for i in idx) for k, e in evidence.items()}}
    result.update(sources=hashes, run_freeze_sha256=a.s.p.digest(WORK / 'run-freeze.json'), method_sha256=a.s.p.digest(__file__))
    verify(); path = SPEC / 'verdict.json'
    if path.exists():
        if a.s.read_json(path) != result:
            raise ValueError('Preserve existing changed120 summary')
    else:
        write_json(path, result)
    print({k: result[k] for k in ('retained_candidates', 'preferred_research_configuration')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'evaluate', 'summarize'])
    globals()[parser.parse_args().action]()
