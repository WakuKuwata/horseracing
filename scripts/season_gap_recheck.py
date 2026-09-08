"""119 joint gap/seasonal-sex correction; no booster training, historical research only."""
from __future__ import annotations
import argparse
from copy import deepcopy
import datetime as dt
import gc
from pathlib import Path
import time

import numpy as np
import pandas as pd
import anchor_gap_recheck as a
import anchor_gap_summary as previous_summary
import gap_seed_recheck as d
import gap_log_quality as g
import small_gain_stack as s
from horseracing_eval.dataset import population_masks
from horseracing_eval.residual_probe import RaceProbe, fit_gamma

ROOT = d.ROOT
SPEC = ROOT / 'specs/119-season-gap-recheck'
WORK = ROOT / 'artifacts/119-season-gap-recheck'
SEEDS = [42, 43, 44]
CONTRASTS = [{'id': 'anchor', 'baseline': 'anchor'}, {'id': 'retained', 'baseline': 'retained'}]
ORDER = ['gap_log', 'female_sin', 'female_cos']
GROUPS = ['no_female', 'mixed', 'all_female', 'missing_sex']
write_json = d.write_json


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg) != expected:
        raise ValueError('119 config hash changed')
    s.p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg, root=ROOT)
    old = d.load_config()
    for key in ('arms', 'eval_window', 'bootstrap', 'seed_noise', 'min_effect_delta',
                'recent_guard', 'top_noninferior', 'calibration', 'subgroup_guard'):
        if cfg[key] != old[key]:
            raise ValueError(f'119 fixed recipe/gate changed: {key}')
    if (cfg['seeds'] != SEEDS or cfg['deployment_seed'] != 42 or cfg['new_fit_jobs'] != 0
        or cfg['fresh_seeds'] != [] or cfg['fresh_fit_years'] != []
        or cfg['selected_arm'] != 'pruning' or cfg['selected_columns'] != 125
        or cfg['contrasts'] != CONTRASTS or cfg['coefficient_order'] != ORDER
        or cfg['gamma_fit'] != {'first_year': 2019, 'k': 3, 'ridge': 1e-6, 'max_iter': 50, 'tol': 1e-9}
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False
        or cfg['numerical_checks'] != {'regularized_fit_objective_max': 1e-8, 'gradient_infinity_norm_max': 1e-5, 'auto_retune': False}):
        raise ValueError('119 joint-vector scope changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '118_source': a.source_hash(),
        'summary': s.p.digest(ROOT / 'scripts/season_gap_summary.py')})


def source_state():
    cfg118, frozen118 = a.verify()
    if (a.WORK / 'running.lock').exists() or not all(a.completed(j) for j in frozen118['jobs']):
        raise ValueError('Completed118 source required')
    paths = [a.WORK / 'run-freeze.json', a.SPEC / 'verdict.json',
             a.SPEC / 'evidence/independent-review.py', a.SPEC / 'evidence/independent-review.json']
    reports, hashes = {}, {}
    for seed in SEEDS:
        reports[seed] = {}
        hashes[str(seed)] = {}
        a.verified_coefficients(seed, frozen118)
        paths.extend([a.seed_area(seed) / 'coefficients.json', a.seed_area(seed) / 'coefficients-receipt.json'])
        for c in CONTRASTS:
            if not a.verified_result(seed, c, cfg118, frozen118):
                raise ValueError('All118 comparisons required')
            path = a.result_path(seed, c['id'])
            reports[seed][c['id']] = r = s.read_json(path)
            hashes[str(seed)][c['id']] = {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(r['evidence_path'])}
            paths.extend([path, Path(r['evidence_path']), a.seed_area(seed) / f"{c['id']}-receipt.json"])
    stored = s.read_json(a.SPEC / 'verdict.json')
    if any(stored.get(k) != v for k, v in previous_summary.summarize_reports(reports).items()):
        raise ValueError('118 completed summary mismatch')
    if stored.get('preferred_research_configuration') != 'PRUNING_GAP':
        raise ValueError('119 was registered for the retained pruning+gap branch')
    review = s.read_json(a.SPEC / 'evidence/independent-review.json')
    if (review.get('status') != 'PASS' or review.get('can_adopt') is not False or review.get('eligible_for_verdict') is not False
        or review.get('method_sha256') != s.p.digest(a.SPEC / 'evidence/independent-review.py')
        or review.get('run_freeze_sha256') != s.p.digest(a.WORK / 'run-freeze.json')
        or review.get('summary_sha256') != s.p.digest(a.SPEC / 'verdict.json') or review.get('report_hashes') != hashes):
        raise ValueError('118 independent-review method/freeze/summary/report binding changed')
    return {'files': {str(p): s.p.digest(p) for p in paths},
        'snapshot_sha256': frozen118['sources']['snapshot_sha256'],
        'population': frozen118['sources']['population'],
        'retained_coefficient_sha256': {str(seed): s.p.digest(a.old_coefficient_path(seed)) for seed in SEEDS}}


def verify():
    cfg = load_config()
    frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != s.runtime() or frozen['sources'] != source_state()
        or frozen['season_audit_sha256'] != s.p.digest(WORK / 'season-input-audit.json')):
        raise ValueError('119 frozen source/config/runtime/upstream/input audit changed')
    return cfg, frozen


def result_path(seed, name):
    if seed not in SEEDS or name not in ('anchor', 'retained'):
        raise ValueError('Unknown119 result scope')
    return SPEC / 'evidence' / f'seed-{seed}-{name}.json'


def seed_area(seed):
    if seed not in SEEDS:
        raise ValueError('Unknown119 seed')
    return WORK / f'seed-{seed}'


def seed_config(cfg, seed):
    return a.seed_config(cfg, seed)


def candidate_matrix(frame):
    # Exact114 calendar/sex/gap definition, narrowed to the prespecified3 terms.
    return g.screen.candidate_matrix(frame)[:, [0, 3, 4]]


def sex_group(values):
    values = list(values)
    if any(pd.isna(v) for v in values):
        return 'missing_sex'
    if not set(values) <= {'牡', '牝', 'セ'} or not values:
        raise ValueError('Invalid started sex population')
    females = sum(v == '牝' for v in values)
    return 'no_female' if females == 0 else 'all_female' if females == len(values) else 'mixed'


def load_inputs():
    matrix, races, folds, legacy_lookup, population = g.load_inputs(s.load_config())
    del legacy_lookup
    frame = matrix.frame[['race_id', 'horse_id', 'race_date', 'days_since_last', 'sex']]
    values = candidate_matrix(frame)
    lookup = {(rid, hid): (day, tuple(float(x) for x in row))
              for rid, hid, day, row in zip(frame.race_id, frame.horse_id, frame.race_date, values, strict=True)}
    groups = frame.groupby('race_id', sort=False).sex.agg(sex_group).to_dict()
    attrs, years = [], {}
    for year, fold in sorted(folds.items()):
        masks = [population_masks(r) for r in fold.valid]
        rows = []
        variation = np.zeros(3, dtype=int)
        for race, pop in zip(fold.valid, masks, strict=True):
            h = h_values(race.context, lookup)
            variation += np.ptp(np.nan_to_num(h, nan=0.), axis=0) > 0
            ctx = race.context
            row = {'race_id': ctx.race_id, 'race_day': str(ctx.race_date), 'year': year,
                   'sex_group': groups[ctx.race_id], 'eligible': pop.eligible}
            rows.append(row)
            if year >= 2020:
                attrs.append(row)
        ids = {r.context.race_id for r in fold.valid}
        sub = frame.loc[frame.race_id.isin(ids)]
        years[str(year)] = {'all_races': len(rows), 'eligible_races': sum(r['eligible'] for r in rows),
            'horse_rows': len(sub), 'gap_missing_rows': int(sub.days_since_last.isna().sum()),
            'sex_missing_rows': int(sub.sex.isna().sum()),
            'races_with_nonzero_within_field_variation': dict(zip(ORDER, variation.tolist(), strict=True)),
            'race_sex_groups': {name: sum(r['sex_group'] == name for r in rows) for name in GROUPS}}
    audit = {'artifact_kind': 'season_gap_input_audit', 'can_adopt': False, 'eligible_for_verdict': False,
        'coefficient_order': ORDER, 'years': years, 'race_attributes': attrs,
        'note': 'Sex groups and within-field variation are descriptive identifiability audits; no outcome-based selection or extra gate.'}
    return matrix, races, folds, lookup, population, audit


def h_values(context, lookup):
    keys = [(context.race_id, h.horse_id) for h in context.started_horses]
    if any(k not in lookup or lookup[k][0] != context.race_date for k in keys):
        raise ValueError('119 lookup horse/date mismatch')
    h = np.array([lookup[k][1] for k in keys], dtype=float)
    if h.shape != (len(keys), 3) or np.isinf(h).any():
        raise ValueError('119 h must be finite-or-NaN n-by3')
    return h


def gamma_array(value):
    raw = np.asarray(value)
    if raw.shape != (3,) or raw.dtype.kind not in 'fiu':
        raise ValueError('Joint gamma requires3 numeric components in registered order')
    gamma = raw.astype(float)
    if not np.isfinite(gamma).all():
        raise ValueError('Nonfinite joint gamma')
    return gamma


def fit_coefficients(factory, folds, lookup, seed):
    if list(sorted(folds)) != list(range(2019, 2027)):
        raise ValueError('2019warmup and7 evaluation folds required')
    probes, gammas = [], []
    for year, fold in sorted(folds.items()):
        predictor = factory.fit([r.context for r in fold.train], num_threads=1)
        block = []
        for race in fold.valid:
            pop = population_masks(race)
            pred = predictor.predict_race(race.context)
            p = g.screen.validate_probabilities(pred, pop.started_horse_ids)
            h = h_values(race.context, lookup)
            if pop.eligible:
                block.append(RaceProbe(str(race.context.race_date), p, h, pop.started_horse_ids.index(pop.winner_horse_id)))
        if not block:
            raise ValueError('Empty joint coefficient fold')
        if probes:
            prior = [r for previous in probes for r in previous]
            if max(r.day for r in prior) >= min(r.day for r in block):
                raise ValueError('Joint coefficient lookahead')
            gammas.append(gamma_array(fit_gamma(prior, k=3, ridge=1e-6, max_iter=50, tol=1e-9)).tolist())
        probes.append(block)
        del predictor
    diagnostics = g.screen.fit_diagnostics(probes, gammas)
    return {'artifact_kind': 'season_gap_joint_coefficients', 'can_adopt': False, 'eligible_for_verdict': False,
        'training_seed': seed, 'base_arm': 'pruning125_raw', 'base_recipe_hash': factory.recipe_hash,
        'coefficient_order': ORDER, 'gammas': {str(y): gamma for y, gamma in zip(range(2020, 2027), gammas, strict=True)},
        'fit_diagnostics': diagnostics, 'warmup_eligible_races': len(probes[0]),
        'evaluated_eligible_races': sum(map(len, probes[1:]))}


def tilt_predictions(base, context, lookup, values):
    ids = [horse.horse_id for horse in context.started_horses]
    p = g.screen.validate_probabilities(base, ids)
    h = h_values(context, lookup)
    gamma = gamma_array(values)
    z = np.nan_to_num(h, nan=0.) @ gamma
    z -= z.max()
    q = p * np.exp(z)
    if not np.isfinite(q).all() or q.sum() <= 0:
        raise ValueError('Invalid joint tilt')
    q /= q.sum()
    if (q <= 0).any() or (len(q) > 1 and (q >= 1).any()) or not np.isclose(q.sum(), 1., atol=1e-12, rtol=0):
        raise ValueError('Joint probabilities must remain interior; no clipping repair')
    result = g.assemble_predictions(ids, q, eps=0.)
    x = np.array([[result[k].win, result[k].top2, result[k].top3] for k in ids])
    if (not np.isfinite(x).all() or (x < -1e-10).any() or (x > 1 + 1e-10).any()
        or (np.diff(x, axis=1) < -1e-12).any()
        or not np.allclose(x.sum(axis=0), [min(k, len(ids)) for k in (1, 2, 3)], atol=1e-8, rtol=0)):
        raise ValueError('Joint top-k probability consistency failed')
    return result, {'below_legacy_clip_horses': int((q < g.DEFAULT_CLIP).sum()),
                    'max_post_assembly_win_change': float(np.max(np.abs(x[:, 0] - q)))}


class JointPredictor:
    is_leaky_reference = False
    def __init__(self, base, lookup, year, gamma, audit):
        self.base, self.lookup, self.year, self.gamma, self.audit = base, lookup, year, gamma, audit
    def predict_race(self, context):
        if context.race_date.year != self.year:
            raise ValueError('Joint gamma applied to wrong calendar year')
        p, audit = tilt_predictions(self.base.predict_race(context), context, self.lookup, self.gamma)
        self.audit[context.race_id] = audit
        return p


class JointFactory:
    def __init__(self, base, lookup, coefficients):
        if coefficients.get('coefficient_order') != ORDER:
            raise ValueError('Joint coefficient order changed')
        self.base, self.lookup, self.coefficients = base, lookup, coefficients
        self.expected_columns = base.expected_columns
        self.recipe_meta = {'base_recipe': base.recipe_meta, 'correction': {
            'kind': 'joint_gap_season_sex_strict_prior_year', 'coefficient_order': ORDER,
            'missing_component_exponent': 0., 'warmup_year': 2019, 'ridge': 1e-6,
            'max_iter': 50, 'tol': 1e-9, 'assembly_eps': 0., 'topk': 'Harville',
            'coefficient_hash': s.p.stable_hash(coefficients)}}
        self.recipe_hash = s.p.stable_hash(self.recipe_meta)
        self.audit = {}
    def fit(self, train_races, *, num_threads=None):
        year = max(r.race_date.year for r in train_races) + 1
        gamma = gamma_array(self.coefficients['gammas'][str(year)])
        return JointPredictor(self.base.fit(train_races, num_threads=1), self.lookup, year, gamma, self.audit)


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify()
        return
    cfg = load_config()
    before, state = source_hash(), source_state()
    matrix, races, folds, lookup, population, audit = load_inputs()
    if population != state['population']:
        raise ValueError('119 input population changed')
    frozen = {'source_hash': before, 'config_hash': s.p.gate_config_hash(cfg), 'runtime': s.runtime(), 'sources': state, 'jobs': []}
    # The old118 certifier uses only raw anchor and old retained gap baselines;
    # it never reads or creates a119 vector, and never fits a booster.
    gap_lookup = {k: (day, value[0]) for k, (day, value) in lookup.items()}
    # Baseline lookup requires the transformed gap itself, already column zero.
    parity = a.certify_baselines(cfg, frozen, matrix, races, folds, gap_lookup)
    del matrix, races, folds, lookup, gap_lookup
    gc.collect()
    if before != source_hash() or source_state() != state:
        raise ValueError('Source changed during119 preparation')
    write_json(WORK / 'season-input-audit.json', audit)
    frozen.update(artifact_kind='season_gap_recheck_freeze', can_adopt=False, eligible_for_verdict=False,
        baseline_parity=parity, season_audit_sha256=s.p.digest(WORK / 'season-input-audit.json'),
        prepared_at=dt.datetime.now(dt.timezone.utc).isoformat())
    write_json(WORK / 'run-freeze.json', frozen)
    print('PREPARE PASS joint3-term inputs and old baselines; no booster fit', flush=True)


def verified_coefficients(seed, frozen):
    path = seed_area(seed) / 'coefficients.json'
    receipt = s.read_json(path.with_name('coefficients-receipt.json'))
    if receipt != {'sha256': s.p.digest(path), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'),
                   'seed': seed, 'coefficient_order': ORDER}:
        raise ValueError('119 vector coefficient receipt mismatch')
    c = s.read_json(path)
    pop = frozen['sources']['population']
    recipe = s.p.CalibSplitFactory(None, s.p.make_recipe(seed_config(load_config(), seed), s.arm(s.load_config(), 'pruning')['drop_features']),
                                  n_oof_blocks=8, method='isotonic', require_sufficient=True)
    if (c.get('artifact_kind') != 'season_gap_joint_coefficients' or c.get('training_seed') != seed
        or c.get('can_adopt') is not False or c.get('eligible_for_verdict') is not False
        or c.get('base_arm') != 'pruning125_raw' or c.get('base_recipe_hash') != recipe.recipe_hash
        or c.get('coefficient_order') != ORDER or set(c.get('gammas', {})) != {str(y) for y in range(2020, 2027)}
        or c.get('warmup_eligible_races') != pop['2019']['eligible_races']
        or c.get('evaluated_eligible_races') != sum(pop[str(y)]['eligible_races'] for y in range(2020, 2027))):
        raise ValueError('119 joint coefficient scope differs')
    for values in c['gammas'].values():
        gamma_array(values)
    return c


def coefficient_provenance(seed, contrast):
    cpath = seed_area(seed) / 'coefficients.json'
    c = s.read_json(cpath)
    result = {'candidate': {'base_arm': 'pruning125_raw', 'path': str(cpath), 'sha256': s.p.digest(cpath),
                           'base_recipe_hash': c['base_recipe_hash'], 'coefficient_order': ORDER}, 'baseline': None}
    if contrast['id'] == 'retained':
        path = a.old_coefficient_path(seed)
        result['baseline'] = {'base_arm': 'pruning125_raw', 'path': str(path), 'sha256': s.p.digest(path),
                              'coefficient_order': ['gap_log']}
    return result


def validate_rows(rows, seed, contrast):
    source = s.read_json(d.result_path(seed, 'anchor'))
    original = s.read_json(source['evidence_path'])['rows']
    field = 'active_winner_nll' if contrast['id'] == 'anchor' else 'candidate_winner_nll'
    if len(rows) != len(original) or any((r['race_id'], r['race_day'], r['active_winner_nll']) !=
        (o['race_id'], o['race_day'], o[field]) for r, o in zip(rows, original, strict=True)):
        raise ValueError('119 baseline differs from old116 per-race loss')
    other = result_path(seed, 'retained' if contrast['id'] == 'anchor' else 'anchor')
    if other.exists():
        other_rows = s.read_json(s.read_json(other)['evidence_path'])['rows']
        if len(rows) != len(other_rows) or any((r['race_id'], r['race_day'], r['candidate_winner_nll']) !=
            (o['race_id'], o['race_day'], o['candidate_winner_nll']) for r, o in zip(rows, other_rows, strict=True)):
            raise ValueError('119 candidate differs across comparisons')


def validate_full_population(report, seed):
    original = s.read_json(d.result_path(seed, 'anchor'))
    if (report.get('n_races') != 23030 or report.get('n_eligible') != 22990
        or not report.get('race_id_set_hash') or report['race_id_set_hash'] != original['race_id_set_hash']):
        raise ValueError('119 full scored population differs from original116, including noneligible races')


def verified_result(seed, contrast, cfg, frozen):
    if isinstance(contrast, str):
        contrast = next(c for c in CONTRASTS if c['id'] == contrast)
    out = result_path(seed, contrast['id'])
    if not out.exists():
        return False
    r = s.read_json(out)
    evidence = seed_area(seed) / f"{contrast['id']}-evidence.json"
    receipt = s.read_json(seed_area(seed) / f"{contrast['id']}-receipt.json")
    if (receipt != {'report_sha256': s.p.digest(out), 'evidence_sha256': s.p.digest(evidence),
                    'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed}
        or r.get('artifact_kind') != 'season_gap_research_report' or r.get('can_adopt') is not False
        or r.get('eligible_for_verdict') is not False or r.get('training_seed') != seed or r.get('contrast') != contrast
        or r.get('study_config_hash') != s.p.gate_config_hash(cfg)
        or r.get('seed_config_hash') != s.p.gate_config_hash(seed_config(cfg, seed))
        or r.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or r.get('coefficient_provenance') != coefficient_provenance(seed, contrast)
        or r.get('evidence_path') != str(evidence) or r.get('evidence_sha256') != s.p.digest(evidence)
        or r.get('research_disposition') != s.research.assess_research(r)):
        raise ValueError('119 report/receipt/coefficients changed')
    verified_coefficients(seed, frozen)
    a.old_coefficients(seed)
    validate_full_population(r, seed)
    validate_rows(s.read_json(evidence)['rows'], seed, contrast)
    return True


def evaluate():
    cfg, frozen = verify()
    matrix, races, folds, lookup, pop, audit = load_inputs()
    if pop != frozen['sources']['population'] or audit != s.read_json(WORK / 'season-input-audit.json'):
        raise ValueError('119 input population/sex audit changed')
    gap_lookup = {k: (day, values[0]) for k, (day, values) in lookup.items()}
    for seed in SEEDS:
        area = seed_area(seed)
        cpath = area / 'coefficients.json'
        if cpath.exists():
            coefficients = verified_coefficients(seed, frozen)
        else:
            if cpath.with_name('coefficients-receipt.json').exists():
                raise ValueError('Orphan119 coefficient receipt')
            base = a.retained_factory(matrix, races, seed)
            coefficients = fit_coefficients(base, folds, lookup, seed)
            verify()
            write_json(cpath, coefficients)
            write_json(cpath.with_name('coefficients-receipt.json'), {'sha256': s.p.digest(cpath),
                'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed, 'coefficient_order': ORDER})
            del base
        for contrast in CONTRASTS:
            if verified_result(seed, contrast, cfg, frozen):
                continue
            evidence = area / f"{contrast['id']}-evidence.json"
            if evidence.exists():
                raise ValueError('Orphan119 evidence; preserve for diagnosis')
            candidate = JointFactory(a.retained_factory(matrix, races, seed), lookup, coefficients)
            baseline = (a.AnchorFactory(cfg, frozen, matrix, races, seed) if contrast['id'] == 'anchor'
                        else a.old_tilt(matrix, races, gap_lookup, seed))
            t0 = time.monotonic()
            report = s.p.paired_eval(candidate, baseline, races, gate_config=seed_config(cfg, seed), first_valid_year=2020,
                valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1,
                snapshot={'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed, 'contrast': contrast,
                          'coefficient_provenance': coefficient_provenance(seed, contrast), 'evidence_regime': 'historical_development_full_information'})
            if report.n_eligible != 22990 or report.n_races != 23030:
                raise ValueError('119 scored population changed')
            validate_rows(report.evidence.to_dict()['rows'], seed, contrast)
            result = report.to_dict()
            validate_full_population(result, seed)
            result.pop('evidence', None)
            result.pop('diffs_by_day', None)
            result['gate_readout'] = result.pop('decision')
            result.update(artifact_kind='season_gap_research_report', can_adopt=False, eligible_for_verdict=False,
                training_seed=seed, deployment_seed=42, contrast=contrast, selected_arm='pruning125_joint_gap_season',
                study_config_hash=s.p.gate_config_hash(cfg), seed_config_hash=s.p.gate_config_hash(seed_config(cfg, seed)),
                run_freeze_sha256=s.p.digest(WORK / 'run-freeze.json'), coefficient_provenance=coefficient_provenance(seed, contrast),
                candidate_columns=candidate.expected_columns, baseline_columns=baseline.expected_columns,
                evidence_regime='historical_development_full_information', limitations=cfg['limitations'],
                assembly_audit={'races': len(candidate.audit), 'assembly_eps': 0.,
                    'below_legacy_clip_horses': sum(x['below_legacy_clip_horses'] for x in candidate.audit.values()),
                    'max_post_assembly_win_change': max(x['max_post_assembly_win_change'] for x in candidate.audit.values())},
                elapsed_seconds=time.monotonic() - t0)
            result['research_disposition'] = s.research.assess_research(result)
            verify()
            write_json(evidence, report.evidence.to_dict())
            result.update(evidence_path=str(evidence), evidence_sha256=s.p.digest(evidence))
            out = result_path(seed, contrast['id'])
            write_json(out, result)
            write_json(area / f"{contrast['id']}-receipt.json", {'report_sha256': s.p.digest(out),
                'evidence_sha256': s.p.digest(evidence), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed})
            print(f"RESULT119 seed={seed} {contrast['id']} {result['periods']['all']['diff']:+.8f} {result['research_disposition']['state']}", flush=True)
            del candidate, baseline, report
            gc.collect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'evaluate'])
    {'prepare': prepare, 'evaluate': evaluate}[parser.parse_args().action]()
