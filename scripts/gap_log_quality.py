"""115: paired quality checks for a prespecified selected-model gap-log correction."""
from __future__ import annotations

import argparse
import datetime as dt
import gc
from pathlib import Path
import time

import numpy as np
import gap_season_recheck as screen
import small_gain_stack as s
from horseracing_eval.dataset import population_masks
from horseracing_eval.residual_probe import RaceProbe, fit_gamma
from horseracing_eval.splits import expanding_folds
from horseracing_training.calib_split import assemble_predictions
from horseracing_training.predictor import DEFAULT_CLIP

ROOT = s.ROOT
SPEC = ROOT / 'specs/115-gap-log-quality'
WORK = ROOT / 'artifacts/115-gap-log-quality'
RETAINED = {'RETAIN_UNCERTAIN', 'RETAIN_SUPPORTED'}
CONTRASTS = [{'id': 'increment', 'baseline': 'selected'}, {'id': 'anchor', 'baseline': 'anchor'}]


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    h = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg) != h:
        raise ValueError('115 config hash mismatch')
    s.p.assert_confirmatory(cfg, expected_hash=h, eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg, root=ROOT)
    if (cfg['eval_window'] != {'from': '2020-01-01', 'to': '2026-08-23', 'min_eval_days': 400}
        or cfg['contrasts'] != CONTRASTS or cfg['min_effect_delta'] != 0
        or cfg['bootstrap'] != {'b': 4000, 'seed': 20260907, 'alpha': .0125, 'block': 'race_day'}
        or cfg['gamma_fit'] != {'first_year': 2019, 'k': 1, 'ridge': 1e-6, 'max_iter': 50, 'tol': 1e-9}
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('115 fixed scope changed')
    return cfg


def select_arm(reports):
    if set(reports) != {'increment', 'anchor'}:
        raise ValueError('Both 113 contrasts required')
    return 'stack' if all(r['research_disposition']['state'] in RETAINED for r in reports.values()) else 'pruning'


def research_progression(reports):
    if set(reports) != {'increment', 'anchor'}:
        raise ValueError('Both 115 contrasts required')
    keep = all(r['research_disposition']['state'] in RETAINED for r in reports.values())
    return 'CORRECTION_RETAINED' if keep else 'CORRECTION_DEFERRED_BASE_RETAINED'


def verify_summary(summary, reports, cfg113, frozen113):
    selected = select_arm(reports)
    expected = 'STACK_RETAINED' if selected == 'stack' else 'STACK_DEFERRED_PRUNING_RETAINED'
    if (summary.get('artifact_kind') != 'small_gain_stack_summary'
        or summary.get('can_adopt') is not False or summary.get('eligible_for_verdict') is not False
        or summary.get('research_decision') != expected
        or summary.get('config_hash') != s.p.gate_config_hash(cfg113)
        or summary.get('run_freeze_sha256') != s.p.digest(s.WORK / 'run-freeze.json')
        or summary.get('summarizer_sha256') != s.p.digest(s.SPEC / 'evidence/summarize.py')):
        raise ValueError('113 summary identity/selection mismatch')
    if set(summary.get('contrasts', {})) != set(reports):
        raise ValueError('113 summary contrast scope mismatch')
    for name, report in reports.items():
        row = summary['contrasts'][name]
        expected_row = {k: report.get(k) for k in ['periods', 'total_ci', 'gate_readout', 'research_disposition', 'n_races', 'n_eligible', 'subgroups']}
        expected_row['report_sha256'] = s.p.digest(s.SPEC / 'evidence' / f'full-{name}.json')
        if row != expected_row:
            raise ValueError('113 summary report differs')
    jobs = summary.get('new_fit_jobs', [])
    if len(jobs) != 8 or sorted(r['year'] for r in jobs) != list(range(2019, 2027)):
        raise ValueError('113 summary needs eight unique completed jobs')
    for job in frozen113['jobs']:
        row = next(r for r in jobs if r['year'] == job['year'])
        receipt_path = s.receipt_path(job['key'])
        receipt = s.read_json(receipt_path)
        if row['receipt_sha256'] != s.p.digest(receipt_path) or row['cache_sha256'] != receipt['cache_sha256']:
            raise ValueError('113 summary cache receipt differs')
    return selected


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '113_source': s.source_hash(),
                           '114_source': screen.source_hash(), 'research_method': s.p.digest(s.research.__file__)})


def source_state():
    cfg113, frozen113 = s.verify()
    if (s.WORK / 'running.lock').exists() or not all(s.completed(j) for j in frozen113['jobs']):
        raise ValueError('Wait for all 113 training and receipts')
    reports = {}
    paths = [s.WORK / 'run-freeze.json', s.SPEC / 'verdict.json', s.SPEC / 'evidence/summarize.py']
    for contrast in cfg113['contrasts']:
        path = s.SPEC / 'evidence' / f"full-{contrast['id']}.json"
        if not s.verified_result(path, cfg113, contrast):
            raise ValueError('113 full comparison required before 115 preparation')
        reports[contrast['id']] = s.read_json(path)
        paths.extend([path, Path(reports[contrast['id']]['evidence_path'])])
    selected = verify_summary(s.read_json(s.SPEC / 'verdict.json'), reports, cfg113, frozen113)
    for job in frozen113['jobs']:
        paths.extend([s.receipt_path(job['key']), s.WORK / 'cache' / f"{job['key']}.pkl"])
    # The registered follow-up exists because of the completed 114 gap-log screen.
    _, freeze114, _ = screen.verify()
    report114_path = screen.SPEC / 'evidence/residual-screen.json'
    report114 = s.read_json(report114_path)
    receipt114 = s.read_json(screen.WORK / 'result-receipt.json')
    if (freeze114['source_hash'] != screen.source_hash()
        or freeze114['config_hash'] != s.p.gate_config_hash(screen.load_config())
        or report114.get('run_freeze_sha256') != s.p.digest(screen.WORK / 'run-freeze.json')
        or receipt114 != {'output_sha256': s.p.digest(report114_path), 'freeze_sha256': s.p.digest(screen.WORK / 'run-freeze.json')}
        or report114.get('can_adopt') is not False or report114.get('eligible_for_verdict') is not False
        or next(r for r in report114['candidates'] if r['candidate_id'] == 'gap_log')['state'] != 'POINT_IMPROVEMENT'):
        raise ValueError('114 registered gap-log follow-up evidence mismatch')
    paths.extend([screen.WORK / 'run-freeze.json', screen.WORK / 'result-receipt.json', report114_path,
                  screen.SPEC / 'gate-config.json', screen.SPEC / 'gate-config.hash.txt'])
    files = {str(path): s.p.digest(path) for path in paths}
    state = {'selected_arm': selected, 'files': files, 'snapshot_sha256': frozen113['snapshot_sha256'],
             'selected_columns': 127 if selected == 'stack' else 125,
             'source113_states': {k: r['research_disposition']['state'] for k, r in reports.items()}}
    return cfg113, frozen113, state


def load_inputs(cfg113):
    matrix, races, folds = s.inputs(cfg113)
    if any(r.n_result_rows is None for r in races):
        raise ValueError('Unknown result-row coverage')
    frame = matrix.frame[['race_id', 'horse_id', 'race_date', 'days_since_last', 'sex']]
    # Reuse 114 validation and gap function; only column zero is used by this study.
    h = screen.candidate_matrix(frame)[:, 0]
    lookup = {(rid, hid): (date, float(v)) for rid, hid, date, v in zip(frame.race_id, frame.horse_id, frame.race_date, h, strict=True)}
    horse_sets = frame.groupby('race_id', sort=False)['horse_id'].agg(set).to_dict()
    for race in races:
        if horse_sets.get(race.context.race_id) != {h.horse_id for h in race.context.started_horses}:
            raise ValueError('Snapshot started-horse population mismatch')
        keys = [(race.context.race_id, horse.horse_id) for horse in race.context.started_horses]
        if any(k not in lookup or lookup[k][0] != race.context.race_date for k in keys):
            raise ValueError('Snapshot input identity/date mismatch')
    audit = {str(y): {'all_races': len(f.valid), 'eligible_races': sum(population_masks(r).eligible for r in f.valid),
                     'days': len({r.context.race_date for r in f.valid})} for y, f in folds.items()}
    if sorted(folds) != list(range(2019, 2027)):
        raise ValueError('Expected eight source folds including warmup')
    return matrix, races, folds, lookup, audit


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify()
        print('PREPARE verified existing freeze; no rewrite', flush=True)
        return
    cfg = load_config()
    before = source_hash()
    cfg113, frozen113, state = source_state()
    matrix, races, folds, lookup, audit = load_inputs(cfg113)
    del matrix, races, folds, lookup
    gc.collect()
    if before != source_hash() or state != source_state()[2]:
        raise ValueError('Sources changed during preparation')
    screen.write_json(WORK / 'run-freeze.json', {'artifact_kind': 'gap_log_quality_freeze',
        'can_adopt': False, 'eligible_for_verdict': False, 'source_hash': before,
        'config_hash': s.p.gate_config_hash(cfg), 'runtime': s.runtime(), 'sources': state, 'population': audit,
        'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    print(f"PREPARE selected={state['selected_arm']}; no coefficient fit yet", flush=True)


def verify():
    cfg = load_config()
    cfg113, frozen113, state = source_state()
    frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != s.runtime() or frozen['sources'] != state):
        raise ValueError('115 frozen source/config/runtime/input changed')
    return cfg, frozen, cfg113, frozen113


def gap_values(context, lookup):
    keys = [(context.race_id, horse.horse_id) for horse in context.started_horses]
    if any(k not in lookup or lookup[k][0] != context.race_date for k in keys):
        raise ValueError('Gap lookup population/date mismatch')
    h = np.array([lookup[k][1] for k in keys], dtype=float)
    if np.isinf(h).any():
        raise ValueError('Infinite gap transform')
    return h[:, None]


def fit_coefficients(factory, folds, lookup):
    probes, gammas = [], []
    for year, fold in sorted(folds.items()):
        predictor = factory.fit([r.context for r in fold.train], num_threads=1)
        block = []
        for race in fold.valid:
            pop = population_masks(race)
            pred = predictor.predict_race(race.context)
            p = screen.validate_probabilities(pred, pop.started_horse_ids)
            h = gap_values(race.context, lookup)
            if pop.eligible:
                block.append(RaceProbe(str(race.context.race_date), p, h, pop.started_horse_ids.index(pop.winner_horse_id)))
        if not block:
            raise ValueError('Empty coefficient fold')
        if probes:
            prior = [r for f in probes for r in f]
            if max(r.day for r in prior) >= min(r.day for r in block):
                raise ValueError('Coefficient lookahead')
            gamma = fit_gamma(prior, k=1, ridge=1e-6, max_iter=50, tol=1e-9)
            if not np.isfinite(gamma).all():
                raise ValueError('Nonfinite gamma')
            gammas.append(gamma.tolist())
        probes.append(block)
        del predictor
    diagnostics = screen.fit_diagnostics(probes, gammas)
    return {'artifact_kind': 'gap_log_coefficients', 'can_adopt': False, 'eligible_for_verdict': False,
            'gammas': {str(y): value[0] for y, value in zip(range(2020, 2027), gammas, strict=True)},
            'fit_diagnostics': diagnostics, 'warmup_eligible_races': len(probes[0]),
            'evaluated_eligible_races': sum(map(len, probes[1:]))}


def tilt_predictions(base, context, lookup, gamma):
    ids = [h.horse_id for h in context.started_horses]
    p = screen.validate_probabilities(base, ids)
    h = gap_values(context, lookup)[:, 0]
    if not np.isfinite(gamma):
        raise ValueError('Nonfinite frozen gamma')
    z = np.nan_to_num(h, nan=0.) * gamma
    z -= z.max()
    q = p * np.exp(z)
    if not np.isfinite(q).all() or q.sum() <= 0:
        raise ValueError('Invalid tilted probabilities')
    q /= q.sum()
    if (q <= 0).any() or (len(ids) > 1 and (q >= 1).any()) or not np.isclose(q.sum(), 1., atol=1e-12, rtol=0):
        raise ValueError('Tilt must stay finite, interior and normalized; no endpoint repair')
    assembled = assemble_predictions(ids, q, eps=0.0)
    x = np.array([[assembled[i].win, assembled[i].top2, assembled[i].top3] for i in ids])
    if (not np.isfinite(x).all() or (x < -1e-10).any() or (x > 1 + 1e-10).any()
        or (np.diff(x, axis=1) < -1e-12).any()
        or not np.allclose(x.sum(axis=0), [min(k, len(ids)) for k in (1, 2, 3)], atol=1e-8, rtol=0)):
        raise ValueError('Tilted probability/top-k consistency failed')
    return assembled, {'below_legacy_clip_horses': int((q < DEFAULT_CLIP).sum()),
                       'max_post_assembly_win_change': float(np.max(np.abs(x[:, 0] - q)))}


class TiltPredictor:
    is_leaky_reference = False

    def __init__(self, base, lookup, year, gamma, audit):
        self.base, self.lookup, self.year, self.gamma, self.audit = base, lookup, year, gamma, audit

    def predict_race(self, context):
        if context.race_date.year != self.year:
            raise ValueError('Frozen gamma applied to wrong year')
        pred, audit = tilt_predictions(self.base.predict_race(context), context, self.lookup, self.gamma)
        self.audit[context.race_id] = audit
        return pred


class TiltFactory:
    def __init__(self, base, lookup, coefficients):
        self.base, self.lookup, self.coefficients = base, lookup, coefficients
        self.expected_columns = base.expected_columns
        self.recipe_meta = {'base_recipe': base.recipe_meta, 'correction': {
            'kind': 'strict_prior_year_gap_log_probability_tilt', 'h': 'log1p(days_since_last)',
            'missing_exponent_offset': 0.0, 'warmup_year': 2019, 'ridge': 1e-6,
            'max_iter': 50, 'tol': 1e-9, 'assembly_eps': 0.0,
            'topk': 'Harville from normalized corrected win',
            'coefficient_hash': s.p.stable_hash(coefficients)}}
        self.recipe_hash = s.p.stable_hash(self.recipe_meta)
        self.audit = {}

    def fit(self, train_races, *, num_threads=None):
        year = max(r.race_date.year for r in train_races) + 1
        gamma = self.coefficients['gammas'][str(year)]
        return TiltPredictor(self.base.fit(train_races, num_threads=1), self.lookup, year, gamma, self.audit)


def verified_coefficients(frozen):
    path = WORK / 'coefficients.json'
    receipt = s.read_json(WORK / 'coefficients-receipt.json')
    if receipt != {'sha256': s.p.digest(path), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json')}:
        raise ValueError('Stored coefficient evidence changed')
    coefficients = s.read_json(path)
    if (coefficients.get('artifact_kind') != 'gap_log_coefficients'
        or coefficients.get('can_adopt') is not False or coefficients.get('eligible_for_verdict') is not False
        or set(coefficients.get('gammas', {})) != {str(y) for y in range(2020, 2027)}
        or not all(np.isfinite(v) for v in coefficients['gammas'].values())
        or coefficients.get('warmup_eligible_races') != frozen['population']['2019']['eligible_races']
        or coefficients.get('evaluated_eligible_races') != sum(frozen['population'][str(y)]['eligible_races'] for y in range(2020, 2027))):
        raise ValueError('Stored coefficient scope/population changed')
    return coefficients


def verified_output(path, contrast, frozen):
    if not path.exists():
        return False
    result = s.read_json(path)
    receipt = s.read_json(WORK / f"{contrast['id']}-receipt.json")
    evidence = WORK / f"{contrast['id']}-evidence.json"
    if (receipt != {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(evidence),
                    'freeze_sha256': s.p.digest(WORK / 'run-freeze.json')}
        or result.get('artifact_kind') != 'gap_log_quality_research_report'
        or result.get('can_adopt') is not False or result.get('eligible_for_verdict') is not False
        or result.get('contrast') != contrast or result.get('selected_arm') != frozen['sources']['selected_arm']
        or result.get('study_config_hash') != frozen['config_hash']
        or result.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or result.get('evidence_path') != str(evidence) or result.get('evidence_sha256') != s.p.digest(evidence)
        or result.get('coefficient_sha256') != s.p.digest(WORK / 'coefficients.json')
        or result.get('research_disposition') != s.research.assess_research(result)):
        raise ValueError('Existing 115 result/receipt changed; preserve for diagnosis')
    verified_coefficients(frozen)
    return True


def summarize(frozen):
    reports = {}
    hashes = {}
    for contrast in CONTRASTS:
        path = SPEC / 'evidence' / f"full-{contrast['id']}.json"
        if not verified_output(path, contrast, frozen):
            raise ValueError('Both completed comparisons required for summary')
        reports[contrast['id']] = s.read_json(path)
        hashes[contrast['id']] = s.p.digest(path)
    summary = {'artifact_kind': 'gap_log_quality_research_summary', 'can_adopt': False,
        'eligible_for_verdict': False, 'selected_arm': frozen['sources']['selected_arm'],
        'research_decision': research_progression(reports), 'report_sha256': hashes,
        'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json')}
    path = SPEC / 'verdict.json'
    if path.exists():
        if s.read_json(path) != summary:
            raise ValueError('115 existing summary changed')
    else:
        screen.write_json(path, summary)
    return summary


def evaluate():
    cfg, frozen, cfg113, frozen113 = verify()
    if all(verified_output(SPEC / 'evidence' / f"full-{c['id']}.json", c, frozen) for c in CONTRASTS):
        summarize(frozen)
        print('EVALUATE verified existing results; no rerun', flush=True)
        return
    matrix, races, folds, lookup, audit = load_inputs(cfg113)
    if audit != frozen['population']:
        raise ValueError('115 population changed')
    selected = frozen['sources']['selected_arm']
    coefficient_path = WORK / 'coefficients.json'
    coefficient_receipt = WORK / 'coefficients-receipt.json'
    if coefficient_path.exists():
        coefficients = verified_coefficients(frozen)
    else:
        if coefficient_receipt.exists():
            raise ValueError('Orphan coefficient receipt')
        factory = s.ReadOnlyFactory(cfg113, frozen113, matrix, races, selected)
        coefficients = fit_coefficients(factory, folds, lookup)
        verify()
        screen.write_json(coefficient_path, coefficients)
        screen.write_json(coefficient_receipt, {'sha256': s.p.digest(coefficient_path), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json')})
        del factory
        gc.collect()
    for contrast in CONTRASTS:
        output = SPEC / 'evidence' / f"full-{contrast['id']}.json"
        if verified_output(output, contrast, frozen):
            continue
        evidence = WORK / f"{contrast['id']}-evidence.json"
        if evidence.exists():
            raise ValueError('Orphan evidence; preserve for diagnosis')
        selected_base = s.ReadOnlyFactory(cfg113, frozen113, matrix, races, selected)
        candidate = TiltFactory(selected_base, lookup, coefficients)
        baseline = s.ReadOnlyFactory(cfg113, frozen113, matrix, races, selected if contrast['baseline'] == 'selected' else 'anchor')
        t0 = time.monotonic()
        report = s.p.paired_eval(candidate, baseline, races, gate_config=cfg, first_valid_year=2020,
            valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1,
            snapshot={'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'contrast': contrast,
                      'coefficient_sha256': s.p.digest(coefficient_path), 'evidence_regime': 'historical_development_full_information'})
        if report.n_eligible != coefficients['evaluated_eligible_races']:
            raise ValueError('Coefficient/scored eligibility mismatch')
        result = report.to_dict()
        result.pop('evidence', None)
        result.pop('diffs_by_day', None)
        result['gate_readout'] = result.pop('decision')
        result.update(artifact_kind='gap_log_quality_research_report', can_adopt=False, eligible_for_verdict=False,
            contrast=contrast, selected_arm=selected, study_config_hash=s.p.gate_config_hash(cfg),
            run_freeze_sha256=s.p.digest(WORK / 'run-freeze.json'), coefficient_sha256=s.p.digest(coefficient_path),
            candidate_columns=candidate.expected_columns, baseline_columns=baseline.expected_columns,
            correction='Annual strict-prior-year gap-log win-probability tilt; eps=0 assembly with no additional clip, Harville top2/top3',
            assembly_audit={'races': len(candidate.audit), 'assembly_eps': 0.0, 'below_legacy_clip_horses': sum(a['below_legacy_clip_horses'] for a in candidate.audit.values()),
                            'max_post_assembly_win_change': max(a['max_post_assembly_win_change'] for a in candidate.audit.values())},
            evidence_regime='historical_development_full_information', limitations=cfg['limitations'], elapsed_seconds=time.monotonic() - t0)
        result['research_disposition'] = s.research.assess_research(result)
        verify()
        screen.write_json(evidence, report.evidence.to_dict())
        result.update(evidence_path=str(evidence), evidence_sha256=s.p.digest(evidence))
        screen.write_json(output, result)
        screen.write_json(WORK / f"{contrast['id']}-receipt.json", {'report_sha256': s.p.digest(output), 'evidence_sha256': s.p.digest(evidence), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json')})
        print(f"RESULT {contrast['id']} {result['periods']['all']['diff']:+.8f} {result['research_disposition']['state']}", flush=True)
        del candidate, selected_base, baseline, report
        gc.collect()
    summarize(frozen)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'evaluate'])
    args = parser.parse_args()
    {'prepare': prepare, 'evaluate': evaluate}[args.action]()
