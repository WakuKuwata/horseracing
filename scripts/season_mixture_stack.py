"""123: fixed seasonal correction and probability mixture stack; no new fitting."""
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
import season_gap_recheck as n
import probability_mixture_recheck as m
import gap_seed_summary as stats
from horseracing_eval.predictor import Prediction

ROOT = a.ROOT
SPEC = ROOT / 'specs/123-season-mixture-stack'
WORK = ROOT / 'artifacts/123-season-mixture-stack'
SEEDS = (42, 43, 44)
MEMBERS = {
    'joint_pruning3': [('joint_pruning', s) for s in SEEDS],
    'joint_mixed6': [('joint_pruning', s) for s in SEEDS] + [('anchor_gap', s) for s in SEEDS],
}
COMPARISONS = [dict(id=f'{c}_vs_{b}', candidate=c, baseline=b)
              for c in MEMBERS for b in ('pruning3', 'mixed6', 'anchor42')]
SELECTION_RULE = 'Retain joint_pruning3 against pruning3 and anchor42; prioritize only with improvement and quality against mixed6 too. Retain and prioritize joint_mixed6 with improvement against mixed6 and anchor42 plus quality on all three comparisons. Select lowest NLL among qualifying candidates, ties by registered member order; otherwise keep mixed6. Preserve blocked comparison residuals even for an alternative retained on its own branch.'
GATE_KEYS = ('evaluation_contract_version', 'primary_metric', 'min_effect_delta', 'delta_derivation_ref',
             'seed_noise', 'bootstrap', 'eval_window', 'recent_guard', 'top_noninferior', 'calibration', 'subgroup_guard')
write_json = a.write_json


def load_config():
    cfg = a.s.read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    old = m.load_config()
    if a.s.p.gate_config_hash(cfg) != expected or any(cfg[k] != old[k] for k in GATE_KEYS):
        raise ValueError('123 registered gate changed')
    a.s.p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    a.s.p.assert_delta_provenance(cfg, root=ROOT)
    if (cfg['comparisons'] != COMPARISONS or cfg['members'] != {k: [list(x) for x in v] for k, v in MEMBERS.items()}
        or cfg['aggregation'] != 'float64_arithmetic_mean_all_three_heads_no_postprocess'
        or cfg['selection_rule'] != SELECTION_RULE or cfg['additional_fits'] != 0 or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('123 fixed mixture scope changed')
    return cfg


def source_hash():
    return a.s.p.stable_hash({'method': a.s.p.digest(__file__), '119_source': n.source_hash(), '120_source': m.source_hash()})


def reviewed_study(module, hashes):
    review_path = module.SPEC / 'evidence/independent-review.json'
    paths = [module.WORK / 'run-freeze.json', module.SPEC / 'verdict.json',
             review_path, module.SPEC / 'evidence/independent-review.py']
    review = a.s.read_json(review_path)
    if (review.get('status') != 'PASS' or review.get('can_adopt') is not False
        or review.get('eligible_for_verdict') is not False
        or review.get('method_sha256') != a.s.p.digest(paths[3])
        or review.get('summary_sha256') != a.s.p.digest(paths[1])
        or review.get('run_freeze_sha256') != a.s.p.digest(paths[0])
        or review.get('report_hashes') != hashes):
        raise ValueError('Upstream completed independent review binding differs')
    return paths


def source_state():
    cfg119, f119 = n.verify(); cfg120, f120 = m.verify()
    v119 = a.s.read_json(n.SPEC / 'verdict.json'); v120 = a.s.read_json(m.SPEC / 'verdict.json')
    if v119.get('research_decision') != 'SEASON_GAP_RETAINED' or 'mixed6' not in v120.get('retained_candidates', []):
        raise ValueError('123 requires retained119 season and120 mixed6 before proceeding')
    paths, h119, h120 = [n.WORK / 'season-input-audit.json'], {}, {}
    for seed in SEEDS:
        n.verified_coefficients(seed, f119)
        paths.extend([n.seed_area(seed) / 'coefficients.json', n.seed_area(seed) / 'coefficients-receipt.json'])
        h119[str(seed)] = {}
        for c in n.CONTRASTS:
            if not n.verified_result(seed, c, cfg119, f119):
                raise ValueError('Incomplete119 upstream evidence')
            p = n.result_path(seed, c['id']); r = a.s.read_json(p)
            h119[str(seed)][c['id']] = {'report_sha256': a.s.p.digest(p), 'evidence_sha256': a.s.p.digest(r['evidence_path'])}
            paths.extend([p, Path(r['evidence_path']), n.seed_area(seed) / f"{c['id']}-receipt.json"])
    for c in m.COMPARISONS:
        if not m.verified_result(c, cfg120, f120):
            raise ValueError('Incomplete120 upstream evidence')
        p = m.result_path(c['id']); r = a.s.read_json(p)
        h120[c['id']] = {'report_sha256': a.s.p.digest(p), 'evidence_sha256': a.s.p.digest(r['evidence_path'])}
        paths.extend([p, Path(r['evidence_path']), m.WORK / f"{c['id']}-receipt.json"])
    paths.extend(reviewed_study(n, h119)); paths.extend(reviewed_study(m, h120))
    if f119['sources']['population'] != f120['sources']['population']:
        raise ValueError('Upstream populations differ')
    return {'files': {str(p): a.s.p.digest(p) for p in paths}, 'population': f119['sources']['population']}


def verify():
    cfg = load_config(); frozen = a.s.read_json(WORK / 'run-freeze.json')
    if (frozen['config_hash'] != a.s.p.gate_config_hash(cfg) or frozen['source_hash'] != source_hash()
        or frozen['runtime'] != a.s.runtime() or frozen['sources'] != source_state()):
        raise ValueError('123 frozen source/config/runtime changed')
    return cfg, frozen


matrix_values = m.matrix_values
average_predictions = m.average_predictions
MixturePredictor = m.MixturePredictor


class MixtureFactory:
    def __init__(self, name, members):
        if name not in MEMBERS or len(members) != len(MEMBERS[name]):
            raise ValueError('Unknown/incomplete registered mixture')
        self.members, self.name, self.audit = members, name, {}
        self.expected_columns = sorted({c for m in members for c in m.expected_columns})
        self.recipe_meta = {'method': 'finite_mixture_of_completed_seasonal_and_gap_forecasts', 'name': name,
            'ordered_members': [{'kind': k, 'seed': s, 'recipe_hash': m.recipe_hash,
                                 'recipe_meta': m.recipe_meta, 'weight': 1 / len(members)}
                                for (k, s), m in zip(MEMBERS[name], members, strict=True)],
            'dtype': 'float64', 'heads': ['win', 'top2', 'top3'], 'aggregation': 'arithmetic_mean',
            'post_aggregation_processing': None, 'recalibration': None}
        self.recipe_hash = a.s.p.stable_hash(self.recipe_meta)

    def fit(self, train_races, *, num_threads=None):
        return MixturePredictor([m.fit(train_races, num_threads=1) for m in self.members], len(self.members), self.audit)


def build_factory(name, matrix, races, lookup):
    scalar = {k: (day, values[0]) for k, (day, values) in lookup.items()}
    if name in ('pruning3', 'mixed6', 'anchor42'):
        return m.build_factory(name, matrix, races, scalar)
    if name not in MEMBERS:
        raise ValueError('Unregistered123 mixture or baseline')
    f119 = a.s.read_json(n.WORK / 'run-freeze.json')
    joint = [n.JointFactory(a.retained_factory(matrix, races, seed), lookup, n.verified_coefficients(seed, f119))
             for seed in SEEDS]
    if name == 'joint_mixed6':
        joint.extend(m.build_factory('anchor3', matrix, races, scalar).members)
    return MixtureFactory(name, joint)


def result_path(name):
    if name not in {c['id'] for c in COMPARISONS}:
        raise ValueError('Unregistered comparison')
    return SPEC / 'evidence' / f'{name}.json'


def validate_population(reports, evidence):
    old_report = a.s.read_json(a.d.result_path(42, 'anchor'))
    old = a.s.read_json(old_report['evidence_path'])['rows']
    identity = [(r['race_id'], r['race_day']) for r in old]
    baseline_rows = {'anchor42': [(x['race_id'], x['race_day'], x['active_winner_nll']) for x in old]}
    for name in ('pruning3', 'mixed6'):
        report = a.s.read_json(m.result_path(f'{name}_vs_anchor42'))
        rows = a.s.read_json(report['evidence_path'])['rows']
        baseline_rows[name] = [(x['race_id'], x['race_day'], x['candidate_winner_nll']) for x in rows]
    candidate_rows = {}
    for c in COMPARISONS:
        name = c['id']; r = reports[name]; rows = evidence[name]['rows']
        if (len(rows) != 22990 or len({x['race_id'] for x in rows}) != 22990
            or len({x['race_day'] for x in rows}) != 715 or r['n_races'] != 23030 or r['n_eligible'] != 22990
            or [(x['race_id'], x['race_day']) for x in rows] != identity):
            raise ValueError('123 population differs from frozen member baseline')
        if not old_report.get('race_id_set_hash') or r.get('race_id_set_hash') != old_report['race_id_set_hash']:
            raise ValueError('123 declared race set hashes differ')
        if c['candidate'] in candidate_rows and any(x['candidate_winner_nll'] != y['candidate_winner_nll']
                for x, y in zip(rows, candidate_rows[c['candidate']], strict=True)):
            raise ValueError('Same123 mixture predictions differ across contrasts')
        candidate_rows[c['candidate']] = rows
        if [(x['race_id'], x['race_day'], x['active_winner_nll']) for x in rows] != baseline_rows[c['baseline']]:
            raise ValueError('Frozen116/120 baseline differs from original evidence')
        for x in rows:
            stats.close(x['candidate_winner_nll'] - x['active_winner_nll'], x['diff'])
        for role, field in [('candidate', 'candidate_winner_nll'), ('active', 'active_winner_nll'), ('diff', 'diff')]:
            stats.close(statistics.mean(x[field] for x in rows), r['periods']['all'][role])
    return {'n_races': 23030, 'n_eligible': 22990, 'n_days': 715, 'same_ordered_population': True,
            'old116_and120_baselines_exact': True, 'mixture_identity_across_comparisons_exact': True}


report_disposition = m.report_disposition
numeric_quality = m.numeric_quality


def summarize_reports(reports):
    if set(reports) != {x['id'] for x in COMPARISONS}:
        raise ValueError('All six comparisons required')
    dispositions = {k: report_disposition(r) for k, r in reports.items()}
    quality = {k: dispositions[k]['state'] != 'BLOCKED' and numeric_quality(r) for k, r in reports.items()}
    def improves(candidate, baseline):
        key = f'{candidate}_vs_{baseline}'
        return quality[key] and dispositions[key]['state'].startswith('RETAIN_')
    retained = []
    if all(improves('joint_pruning3', b) for b in ('pruning3', 'anchor42')):
        retained.append('joint_pruning3')
    if (all(improves('joint_mixed6', b) for b in ('mixed6', 'anchor42'))
        and all(quality[f'joint_mixed6_vs_{b}'] for b in ('pruning3', 'mixed6', 'anchor42'))):
        retained.append('joint_mixed6')
    eligible = [c for c in retained if improves(c, 'mixed6') and all(quality[f'{c}_vs_{b}'] for b in ('pruning3', 'mixed6', 'anchor42'))]
    preferred = min(eligible, key=lambda c: reports[f'{c}_vs_anchor42']['periods']['all']['candidate']) if eligible else 'mixed6'
    return {'artifact_kind': 'season_probability_mixture_research_summary', 'can_adopt': False,
        'eligible_for_verdict': False, 'retained_candidates': retained, 'preferred_research_configuration': preferred,
        'bundle_replicates': 1, 'new_model_fits': 0, 'new_coefficient_fits': 0, 'dispositions': dispositions,
        'quality_by_comparison': quality, 'blocked_comparisons': [k for k, ok in quality.items() if not ok],
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
        or r.get('artifact_kind') != 'season_probability_mixture_research_report'
        or r.get('comparison') != c or r.get('study_config_hash') != a.s.p.gate_config_hash(cfg)
        or r.get('run_freeze_sha256') != a.s.p.digest(WORK / 'run-freeze.json')
        or r.get('evidence_path') != str(evidence) or r.get('evidence_sha256') != a.s.p.digest(evidence)):
        raise ValueError('123 existing report provenance differs')
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
    print('PREPARE PASS two fixed seasonal mixtures/six comparisons', flush=True)


def evaluate():
    cfg, frozen = verify()
    matrix, races, folds, lookup, audit, season_audit = n.load_inputs()
    if season_audit != a.s.read_json(n.WORK / 'season-input-audit.json'):
        raise ValueError('119 seasonal inputs changed')
    if audit != frozen['sources']['population']:
        raise ValueError('Frozen input population changed')
    for c in COMPARISONS:
        if verified_result(c, cfg, frozen):
            continue
        ep = WORK / f"{c['id']}-evidence.json"
        if ep.exists():
            raise ValueError('Preserve orphan123 evidence for diagnosis')
        candidate = build_factory(c['candidate'], matrix, races, lookup)
        baseline = build_factory(c['baseline'], matrix, races, lookup)
        t0 = time.monotonic()
        report = a.s.p.paired_eval(candidate, baseline, races, gate_config=cfg, first_valid_year=2020,
            valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1,
            snapshot={'run_freeze_sha256': a.s.p.digest(WORK / 'run-freeze.json'), 'comparison': c,
                      'evidence_regime': 'historical_fixed_seasonal_mixture_stack'})
        if report.n_races != 23030 or report.n_eligible != 22990 or len(candidate.audit) != 23030:
            raise ValueError('Scored mixture population incomplete')
        result = report.to_dict(); result.pop('evidence', None); result.pop('diffs_by_day', None)
        result['gate_readout'] = result.pop('decision')
        result.update(artifact_kind='season_probability_mixture_research_report', can_adopt=False, eligible_for_verdict=False,
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
            raise ValueError('All six completed comparisons required')
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
            raise ValueError('Preserve existing changed123 summary')
    else:
        write_json(path, result)
    print({k: result[k] for k in ('retained_candidates', 'preferred_research_configuration')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'evaluate', 'summarize'])
    globals()[parser.parse_args().action]()
