"""125: preregistered joint residual terms and a fixed mixture, historical research."""
from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as dt
import gc
from pathlib import Path
import time

import numpy as np
import extra_residual_quality as q
import season_gap_recheck as n
import season_mixture_stack as mix
import probability_mixture_recheck as m
from horseracing_eval.dataset import population_masks
from horseracing_eval.residual_probe import RaceProbe, fit_gamma

s, a, g = q.s, q.a, q.g
ROOT = s.ROOT
SPEC = ROOT / 'specs/125-joint-residual-stack'
WORK = ROOT / 'artifacts/125-joint-residual-stack'
SEEDS = (42, 43, 44)
OPTIONAL = ('prior_gap', 'global_temperature')
MIX_BASELINES = ('joint_mixed6', 'mixed6', 'anchor42')
GATE_KEYS = m.GATE_KEYS
write_json = q.write_json
POLICY = {
    'selected_terms': 'All and only124 RETAIN candidates in registered prior_gap/global_temperature order; empty selection stops.',
    'joint_retention': 'Every season and selected124 contrast has negative three-seed mean NLL and valid mean quality; any hard individual quality failure is REVIEW_REQUIRED.',
    'mixture_retention': 'All three fixed mixture comparisons have negative NLL and valid quality, independently of joint retention; report every blocked comparison.',
    'preference': 'Use new_joint_mixed6 only if mixture retained, otherwise preserve123 preferred configuration.',
    'auto_retune': False,
}


def order_for(selected):
    if list(selected) != [c for c in OPTIONAL if c in selected] or not selected:
        raise ValueError('A nonempty ordered subset of registered124 terms is required')
    return ['gap_log'] + (['prior_gap_log'] if 'prior_gap' in selected else []) + ['female_sin', 'female_cos'] + (
        ['centered_logp'] if 'global_temperature' in selected else [])


def comparisons(selected):
    order_for(selected)
    return ([{'id': f'joint-seed-{seed}-vs-{base}', 'kind': 'joint', 'seed': seed, 'baseline': base}
             for seed in SEEDS for base in ('season', *selected)] +
            [{'id': f'mixture-vs-{base}', 'kind': 'mixture', 'baseline': base} for base in MIX_BASELINES])


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    expected = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg) != expected or any(cfg[k] != m.load_config()[k] for k in GATE_KEYS):
        raise ValueError('125 registered gate changed')
    s.p.assert_confirmatory(cfg, expected_hash=expected, eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg, root=ROOT)
    if (cfg['policy'] != POLICY or cfg['optional_terms'] != list(OPTIONAL) or cfg['seeds'] != list(SEEDS)
        or cfg['arms'] != n.load_config()['arms']
        or cfg['fit'] != {'ridge': 1e-6, 'max_iter': 50, 'tol': 1e-9, 'objective_max': 1e-8, 'gradient_inf_max': 1e-5}
        or cfg['additional_booster_fits'] != 0 or cfg['maximum_comparisons'] != 12
        or cfg['assembly_eps'] != 0. or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('125 fixed conditional design changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '124_source': q.source_hash(), '123_source': mix.source_hash()})


def bind_review(module, hashes):
    paths = [module.WORK / 'run-freeze.json', module.SPEC / 'verdict.json',
             module.SPEC / 'evidence/independent-review.py', module.SPEC / 'evidence/independent-review.json']
    r = s.read_json(paths[3])
    if (r.get('status') != 'PASS' or r.get('can_adopt') is not False or r.get('eligible_for_verdict') is not False
        or r.get('method_sha256') != s.p.digest(paths[2]) or r.get('summary_sha256') != s.p.digest(paths[1])
        or r.get('run_freeze_sha256') != s.p.digest(paths[0]) or r.get('report_hashes') != hashes):
        raise ValueError('Upstream independent review binding differs')
    return paths


def source_state():
    cfg124, f124 = q.verify(); cfg123, f123 = mix.verify()
    v124, v123 = s.read_json(q.SPEC / 'verdict.json'), s.read_json(mix.SPEC / 'verdict.json')
    paths, hashes124, hashes123 = [a.diagnostic.WORK / 'race-diagnostic.json'], {}, {}
    for candidate in q.CANDIDATES:
        reports, hashes124[candidate] = {}, {}
        for seed in SEEDS:
            reports[seed], hashes124[candidate][str(seed)] = {}, {}
            for c in q.CONTRASTS:
                name = c['id']
                if not q.verified_result(seed, candidate, name, cfg124, f124):
                    raise ValueError('124 all completed reports required')
                p = q.result_path(seed, candidate, name); r = reports[seed][name] = s.read_json(p)
                hashes124[candidate][str(seed)][name] = {'report_sha256': s.p.digest(p), 'evidence_sha256': s.p.digest(r['evidence_path'])}
                paths.extend([p, Path(r['evidence_path']), q.area(seed, candidate) / f'{name}-receipt.json'])
            q.coefficients(seed, candidate)
        if any(v124['candidates'][candidate].get(k) != v for k, v in q.summarize_candidate(reports).items()):
            raise ValueError('124 saved selection differs from full results')
    selected = [c for c in OPTIONAL if v124['candidates'][c]['state'] == 'RETAIN']
    if selected != v124['retained_candidates']:
        raise ValueError('124 retained candidates inconsistent')
    order_for(selected)
    reports123 = {}
    for c in mix.COMPARISONS:
        if not mix.verified_result(c, cfg123, f123):
            raise ValueError('123 all completed reports required')
        p = mix.result_path(c['id']); r = reports123[c['id']] = s.read_json(p)
        hashes123[c['id']] = {'report_sha256': s.p.digest(p), 'evidence_sha256': s.p.digest(r['evidence_path'])}
        paths.extend([p, Path(r['evidence_path']), mix.WORK / f"{c['id']}-receipt.json"])
    if any(v123.get(k) != v for k, v in mix.summarize_reports(reports123).items()):
        raise ValueError('123 saved mixture selection differs')
    paths.extend(bind_review(q, hashes124)); paths.extend(bind_review(mix, hashes123))
    if f124['sources']['population'] != f123['sources']['population']:
        raise ValueError('Upstream population differs')
    return {'files': {str(p): s.p.digest(p) for p in paths}, 'selected': selected, 'order': order_for(selected),
            'population': f124['sources']['population'], 'prior_preference': v123['preferred_research_configuration']}


def verify():
    cfg = load_config(); frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != q.probe.runtime() or frozen['sources'] != source_state()
        or frozen['comparisons'] != comparisons(frozen['sources']['selected'])):
        raise ValueError('125 frozen source/config/runtime/selection changed')
    return cfg, frozen


def load_inputs():
    matrix, races, folds, prior, population, audit = q.load_inputs()
    frame = matrix.frame[['race_id', 'horse_id', 'race_date', 'days_since_last', 'sex']]
    values = n.candidate_matrix(frame)
    season = {(rid, hid): (str(day), row[1:]) for rid, hid, day, row in
              zip(frame.race_id, frame.horse_id, frame.race_date, values, strict=True)}
    lookup = {}
    for key, (day, gap, lastgap) in prior.items():
        if season[key][0] != day:
            raise ValueError('Season/prior-gap dates differ')
        lookup[key] = (day, gap, lastgap, *map(float, season[key][1]))
    del season, values
    return matrix, races, folds, lookup, population, audit


def design(context, lookup, p, selected):
    order = order_for(selected)
    keys = [(context.race_id, h.horse_id) for h in context.started_horses]
    if any(k not in lookup or lookup[k][0] != str(context.race_date) for k in keys):
        raise ValueError('Joint input horse/date differs')
    raw = np.asarray([lookup[k][1:] for k in keys], dtype=float)
    if raw.shape != (len(keys), 4) or np.isinf(raw).any():
        raise ValueError('Invalid joint inputs')
    raw = np.nan_to_num(raw, nan=0.)
    columns = [raw[:, 0]] + ([raw[:, 1]] if 'prior_gap' in selected else []) + [raw[:, 2], raw[:, 3]]
    if 'global_temperature' in selected:
        lp = np.log(p); columns.append(lp-lp.mean())
    h = np.column_stack(columns)
    if h.shape[1] != len(order) or not np.isfinite(h).all():
        raise ValueError('Invalid joint design')
    return h


class TemperatureDomainError(ValueError):
    pass


class CoefficientFitError(ValueError):
    pass


def gamma_array(values, selected):
    raw = np.asarray(values)
    if raw.shape != (len(order_for(selected)),) or raw.dtype.kind not in 'fiu' or not np.isfinite(raw).all():
        raise ValueError('Invalid joint coefficients')
    beta = raw.astype(float)
    if 'global_temperature' in selected and 1. + beta[-1] <= 0:
        raise TemperatureDomainError('Global effective temperature exponent is nonpositive')
    return beta


def fit_coefficients(factory, folds, lookup, selected, seed):
    if sorted(folds) != list(range(2019, 2027)):
        raise ValueError('All warmup/evaluation folds required')
    probes, gammas, counts = [], [], []
    for year, fold in sorted(folds.items()):
        predictor = factory.fit([r.context for r in fold.train], num_threads=1)
        block = []
        for race in fold.valid:
            pop = population_masks(race)
            p = g.screen.validate_probabilities(predictor.predict_race(race.context), pop.started_horse_ids)
            h = design(race.context, lookup, p, selected)
            if pop.eligible:
                block.append(RaceProbe(str(race.context.race_date), p, h, pop.started_horse_ids.index(pop.winner_horse_id)))
        if not block:
            raise ValueError('Empty coefficient fold')
        if probes:
            prior = [row for b in probes for row in b]
            if max(r.day for r in prior) >= min(r.day for r in block):
                raise ValueError('Joint coefficient lookahead')
            try:
                fitted = fit_gamma(prior, k=len(order_for(selected)), ridge=1e-6, max_iter=50, tol=1e-9)
            except (ValueError, FloatingPointError, OverflowError) as exc:
                raise CoefficientFitError(str(exc)) from exc
            try:
                beta = gamma_array(fitted, selected)
            except TemperatureDomainError:
                raise
            except ValueError as exc:
                raise CoefficientFitError(str(exc)) from exc
            gammas.append(beta.tolist())
            # The global exponent is race-constant; its domain check therefore
            # covers every prior/held race, including noneligible races.
            counts.append({'year': year, 'fit_all_races': sum(len(folds[y].valid) for y in folds if y < year),
                           'held_all_races': len(fold.valid), 'temperature_exponent': float(1.+beta[-1]) if 'global_temperature' in selected else None})
        probes.append(block)
    try:
        diagnostics = g.screen.fit_diagnostics(probes, gammas)
    except (ValueError, FloatingPointError, OverflowError) as exc:
        raise CoefficientFitError(str(exc)) from exc
    return {'artifact_kind': 'joint_residual_stack_coefficients', 'can_adopt': False, 'eligible_for_verdict': False,
        'training_seed': seed, 'base_recipe_hash': factory.recipe_hash, 'base_arm': 'pruning125_raw',
        'selected': selected, 'coefficient_order': order_for(selected),
        'gammas': {str(y): v for y, v in zip(range(2020, 2027), gammas, strict=True)},
        'fit_diagnostics': diagnostics, 'temperature_domain_checks': counts,
        'warmup_eligible_races': len(probes[0]), 'evaluated_eligible_races': sum(map(len, probes[1:]))}


def assemble(base, context, lookup, coefficients):
    ids = [h.horse_id for h in context.started_horses]
    p = g.screen.validate_probabilities(base, ids)
    selected = coefficients['selected']
    beta = gamma_array(coefficients['gammas'][str(context.race_date.year)], selected)
    h = design(context, lookup, p, selected); z = h @ beta
    if not np.isfinite(z).all():
        raise ValueError('Nonfinite joint offset')
    raw = p*np.exp(z-z.max()); probability = raw/raw.sum()
    if not np.isfinite(probability).all() or (probability <= 0).any() or (len(ids)>1 and (probability>=1).any()):
        raise ValueError('Joint corrected probability domain failed; no clipping repair')
    out = g.assemble_predictions(ids, probability, eps=0.)
    m.matrix_values(out, ids)
    if not np.allclose([out[h].win for h in ids], probability, atol=1e-12, rtol=0):
        raise ValueError('Post-assembly win changed')
    return out


class Predictor:
    is_leaky_reference = False
    def __init__(self, base, lookup, coef, year):
        self.base, self.lookup, self.coef, self.year = base, lookup, coef, year
    def predict_race(self, context):
        if context.race_date.year != self.year:
            raise ValueError('Joint coefficient wrong year')
        return assemble(self.base.predict_race(context), context, self.lookup, self.coef)


class Factory:
    def __init__(self, base, lookup, coef):
        self.base, self.lookup, self.coef = base, lookup, coef
        if coef['base_recipe_hash'] != base.recipe_hash:
            raise ValueError('Joint coefficients attached to different raw model')
        self.expected_columns = base.expected_columns
        self.recipe_meta = {'base_recipe': base.recipe_meta, 'joint_correction': coef, 'assembly_eps': 0., 'heads': 'Harville'}
        self.recipe_hash = s.p.stable_hash(self.recipe_meta)
    def fit(self, train_races, *, num_threads=None):
        year = max(r.race_date.year for r in train_races)+1
        gamma_array(self.coef['gammas'][str(year)], self.coef['selected'])
        return Predictor(self.base.fit(train_races, num_threads=1), self.lookup, self.coef, year)


class MixtureFactory:
    def __init__(self, members):
        if len(members) != 6:
            raise ValueError('All three joint and three anchor-gap members required')
        self.members, self.audit = members, {}
        self.expected_columns = sorted({c for member in members for c in member.expected_columns})
        self.recipe_meta = {'method': 'fixed_equal_six_member_all_three_heads', 'members': [v.recipe_meta for v in members],
                            'member_order': [('joint', s) for s in SEEDS]+[('anchor_gap',s) for s in SEEDS], 'postprocess': None}
        self.recipe_hash = s.p.stable_hash(self.recipe_meta)
    def fit(self, train_races, *, num_threads=None):
        return m.MixturePredictor([v.fit(train_races, num_threads=1) for v in self.members], 6, self.audit)


def seed_area(seed):
    if seed not in SEEDS:
        raise ValueError('Unregistered seed')
    return WORK / f'seed-{seed}'


def verified_coefficients(seed, frozen):
    path = seed_area(seed) / 'coefficients.json'
    if (seed_area(seed) / 'numerical-failure.json').exists():
        raise ValueError('Preserve prior numerical failure; no automatic retry')
    expected = {'sha256': s.p.digest(path), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'seed': seed}
    if s.read_json(path.with_name('coefficients-receipt.json')) != expected:
        raise ValueError('Joint coefficient receipt differs')
    c = s.read_json(path); selected = frozen['sources']['selected']; pop = frozen['sources']['population']
    if (c.get('artifact_kind') != 'joint_residual_stack_coefficients' or c.get('training_seed') != seed
        or c.get('can_adopt') is not False or c.get('eligible_for_verdict') is not False
        or c.get('base_arm') != 'pruning125_raw' or c.get('base_recipe_hash') != frozen['base_recipe_hashes'][str(seed)]
        or c.get('selected') != selected or c.get('coefficient_order') != order_for(selected)
        or set(c.get('gammas', {})) != {str(y) for y in range(2020, 2027)}
        or c.get('warmup_eligible_races') != pop['2019']['eligible_races']
        or c.get('evaluated_eligible_races') != sum(pop[str(y)]['eligible_races'] for y in range(2020, 2027))):
        raise ValueError('Joint coefficient scope differs')
    for values in c['gammas'].values():
        gamma_array(values, selected)
    if len(c.get('fit_diagnostics',[])) != 7 or len(c.get('temperature_domain_checks',[])) != 7:
        raise ValueError('Missing saved joint coefficient diagnostics')
    for year, diagnostic, domain in zip(range(2020,2027), c['fit_diagnostics'], c['temperature_domain_checks'], strict=True):
        numbers = [diagnostic['regularized_fit_objective'], diagnostic['gradient_inf']]
        if (diagnostic['eval_year'] != year or diagnostic['fit_races'] != sum(pop[str(y)]['eligible_races'] for y in range(2019,year))
            or diagnostic['fit_last_day'] >= diagnostic['eval_first_day']
            or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not np.isfinite(v) for v in numbers)
            or numbers[0] > 1e-8 or numbers[1] < 0 or numbers[1] > 1e-5
            or domain['year'] != year or domain['fit_all_races'] != sum(pop[str(y)]['all_races'] for y in range(2019,year))
            or domain['held_all_races'] != pop[str(year)]['all_races']
            or domain['temperature_exponent'] != (1.+c['gammas'][str(year)][-1] if 'global_temperature' in selected else None)):
            raise ValueError('Saved joint coefficient diagnostics differ')
    return c


def old_lookups(lookup):
    prior = {k: (v[0], v[1], v[2]) for k, v in lookup.items()}
    season = {k: (dt.date.fromisoformat(v[0]), (v[1], v[3], v[4])) for k, v in lookup.items()}
    scalar = {k: (dt.date.fromisoformat(v[0]), v[1]) for k, v in lookup.items()}
    return prior, season, scalar


def build_factory(c, matrix, races, lookup, frozen, candidate):
    prior, season, scalar = old_lookups(lookup)
    if candidate:
        def joint(seed):
            return Factory(a.retained_factory(matrix, races, seed), lookup, verified_coefficients(seed, frozen))
        if c['kind'] == 'joint':
            return joint(c['seed'])
        return MixtureFactory([joint(seed) for seed in SEEDS]+m.build_factory('anchor3', matrix, races, scalar).members)
    name = c['baseline']
    if c['kind'] == 'joint':
        seed = c['seed']; base = a.retained_factory(matrix, races, seed)
        if name == 'season':
            return n.JointFactory(base, season, n.verified_coefficients(seed, s.read_json(n.WORK / 'run-freeze.json')))
        return q.Factory(base, prior, name, q.coefficients(seed, name))
    if name == 'joint_mixed6':
        return mix.build_factory(name, matrix, races, season)
    return m.build_factory(name, matrix, races, scalar)


def baseline_rows(c):
    if c['kind'] == 'joint':
        path = (n.result_path(c['seed'], 'retained') if c['baseline'] == 'season' else
                q.result_path(c['seed'], c['baseline'], 'retained'))
        field = 'candidate_winner_nll'
    elif c['baseline'] == 'joint_mixed6':
        path, field = mix.result_path('joint_mixed6_vs_anchor42'), 'candidate_winner_nll'
    elif c['baseline'] == 'mixed6':
        path, field = m.result_path('mixed6_vs_anchor42'), 'candidate_winner_nll'
    else:
        path, field = a.d.result_path(42, 'anchor'), 'active_winner_nll'
    report = s.read_json(path)
    return report, s.read_json(report['evidence_path'])['rows'], field


def validate_rows(report, rows, c):
    original, old, field = baseline_rows(c)
    if any(report[k] != original[k] for k in ('n_races', 'n_eligible', 'race_id_set_hash')):
        raise ValueError('Joint full scored population differs')
    if [(r['race_id'], r['race_day'], r['active_winner_nll']) for r in rows] != [(r['race_id'], r['race_day'], r[field]) for r in old]:
        raise ValueError('Joint ordered baseline NLL differs from saved original')
    for row in rows:
        values = [row[k] for k in ('candidate_winner_nll', 'active_winner_nll', 'diff')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) for v in values):
            raise ValueError('Invalid joint loss evidence')
        if min(values[:2]) < 0:
            raise ValueError('Negative NLL')
        m.stats.close(values[0]-values[1], values[2])
    for key, field in [('candidate','candidate_winner_nll'), ('active','active_winner_nll'), ('diff','diff')]:
        m.stats.close(float(np.mean([r[field] for r in rows])), report['periods']['all'][key])


def result_path(c):
    return SPEC / 'evidence' / f"{c['id']}.json"


def provenance(frozen):
    return {str(seed): s.p.digest(seed_area(seed) / 'coefficients.json') for seed in SEEDS
            if verified_coefficients(seed, frozen)}


def verified_result(c, cfg, frozen):
    path = result_path(c); evidence = WORK / f"{c['id']}-evidence.json"; receipt = WORK / f"{c['id']}-receipt.json"
    present = [p.exists() for p in (path, evidence, receipt)]
    if not any(present):
        return False
    if not all(present):
        raise ValueError('Orphan125 result/evidence/receipt; preserve for diagnosis')
    r = s.read_json(path)
    expected = {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(evidence), 'freeze_sha256': s.p.digest(WORK / 'run-freeze.json')}
    if (s.read_json(receipt) != expected or r.get('artifact_kind') != 'joint_residual_stack_report'
        or r.get('comparison') != c or r.get('study_config_hash') != s.p.gate_config_hash(cfg)
        or r.get('run_freeze_sha256') != expected['freeze_sha256'] or r.get('coefficient_provenance') != provenance(frozen)
        or r.get('evidence_path') != str(evidence) or r.get('evidence_sha256') != expected['evidence_sha256']):
        raise ValueError('125 saved result provenance differs')
    m.report_disposition(r)
    validate_rows(r, s.read_json(evidence)['rows'], c)
    return True


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify(); return
    cfg, before, state = load_config(), source_hash(), source_state()
    matrix, races, folds, lookup, population, audit = load_inputs()
    if any(population[str(y)][k] != state['population'][str(y)][k] for y in range(2019,2027) for k in ('all_races','eligible_races')):
        raise ValueError('125 population differs')
    frozen = {'source_hash': before, 'config_hash': s.p.gate_config_hash(cfg), 'runtime': q.probe.runtime(),
        'sources': state, 'comparisons': comparisons(state['selected']), 'input_audit': audit,
        'base_recipe_hashes': {str(seed): a.retained_factory(matrix, races, seed).recipe_hash for seed in SEEDS},
        'artifact_kind': 'joint_residual_stack_freeze', 'can_adopt': False, 'eligible_for_verdict': False,
        'additional_booster_fits': 0, 'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    del matrix, races, folds, lookup; gc.collect()
    if before != source_hash() or state != source_state():
        raise ValueError('125 sources changed during preparation')
    write_json(WORK / 'run-freeze.json', frozen)
    print(f"PREPARE125 selected={state['selected']} comparisons={len(frozen['comparisons'])}; no fit", flush=True)


def evaluate():
    cfg, frozen = verify()
    if all(verified_result(c, cfg, frozen) for c in frozen['comparisons']):
        print('125 all saved reports verified; no rerun', flush=True); return
    matrix, races, folds, lookup, population, audit = load_inputs()
    if audit != frozen['input_audit']:
        raise ValueError('125 frozen feature audit differs')
    for seed in SEEDS:
        path = seed_area(seed) / 'coefficients.json'; receipt = path.with_name('coefficients-receipt.json')
        failure = path.with_name('numerical-failure.json')
        if failure.exists():
            raise ValueError('125 earlier numerical failure; no automatic retry')
        if path.exists():
            verified_coefficients(seed, frozen); continue
        if receipt.exists():
            raise ValueError('Orphan125 coefficient receipt')
        try:
            coef = fit_coefficients(a.retained_factory(matrix, races, seed), folds, lookup, frozen['sources']['selected'], seed)
        except (CoefficientFitError, TemperatureDomainError) as exc:
            write_json(failure, {'state': 'BLOCKED_TEMPERATURE_DOMAIN' if isinstance(exc,TemperatureDomainError) else 'BLOCKED_NUMERICAL',
                'reason': str(exc), 'seed': seed, 'run_freeze_sha256': s.p.digest(WORK/'run-freeze.json'),
                'can_adopt': False, 'eligible_for_verdict': False, 'auto_retune': False})
            raise
        verify(); write_json(path, coef)
        write_json(receipt, {'sha256': s.p.digest(path), 'freeze_sha256': s.p.digest(WORK/'run-freeze.json'), 'seed': seed})
    for c in frozen['comparisons']:
        if verified_result(c, cfg, frozen):
            continue
        candidate = build_factory(c, matrix, races, lookup, frozen, True)
        baseline = build_factory(c, matrix, races, lookup, frozen, False)
        start = time.monotonic()
        report = s.p.paired_eval(candidate, baseline, races, gate_config=a.seed_config(cfg,c.get('seed',42)),
            first_valid_year=2020, valid_from=dt.date(2020,1,1), subgroups=True, num_threads=1,
            snapshot={'run_freeze_sha256': s.p.digest(WORK/'run-freeze.json'), 'comparison': c,
                      'evidence_regime': 'historical_joint_residual_and_fixed_mixture'})
        row = report.to_dict(); evidence = report.evidence.to_dict(); validate_rows(row,evidence['rows'],c)
        row.pop('evidence',None); row.pop('diffs_by_day',None); row['gate_readout'] = row.pop('decision')
        row.update(artifact_kind='joint_residual_stack_report', can_adopt=False, eligible_for_verdict=False,
            comparison=c, study_config_hash=s.p.gate_config_hash(cfg), run_freeze_sha256=s.p.digest(WORK/'run-freeze.json'),
            coefficient_provenance=provenance(frozen), limitations=cfg['limitations'], elapsed_seconds=time.monotonic()-start)
        row['research_disposition'] = s.research.assess_research(row)
        if c['kind'] == 'mixture':
            if len(candidate.audit) != 23030:
                raise ValueError('Incomplete mixture probability audit')
            row['mixture_audit'] = {'n_races': len(candidate.audit), 'max_jensen_excess': max(candidate.audit.values()), 'no_postprocess': True}
        verify(); ep = WORK / f"{c['id']}-evidence.json"; write_json(ep,evidence)
        row.update(evidence_path=str(ep),evidence_sha256=s.p.digest(ep)); write_json(result_path(c),row)
        write_json(WORK/f"{c['id']}-receipt.json", {'report_sha256':s.p.digest(result_path(c)),
            'evidence_sha256':s.p.digest(ep),'freeze_sha256':s.p.digest(WORK/'run-freeze.json')})
        print(f"RESULT125 {c['id']} {row['periods']['all']['diff']:+.8f} {row['research_disposition']['state']}",flush=True)
        del candidate,baseline,report; gc.collect()


def summarize_reports(reports, selected, prior_preference):
    cs = comparisons(selected)
    if set(reports) != {c['id'] for c in cs}:
        raise ValueError('All registered125 results required')
    dispositions = {key:m.report_disposition(value) for key,value in reports.items()}
    joint = {}
    for baseline in ('season',*selected):
        per_seed = {str(seed):reports[f'joint-seed-{seed}-vs-{baseline}'] for seed in SEEDS}
        quality = {k:float(np.mean([r['gate']['reasons'][k] for r in per_seed.values()]))
                   for k in ('top2_diff','top3_diff','cand_ece','act_ece')}
        blocked = [seed for seed,r in per_seed.items() if dispositions[f'joint-seed-{seed}-vs-{baseline}']['state']=='BLOCKED' or not m.numeric_quality(r)]
        quality_ok = quality['top2_diff']<=.0005 and quality['top3_diff']<=.0005 and quality['cand_ece']-quality['act_ece']<=.001 and quality['cand_ece']<.05
        diff = float(np.mean([r['periods']['all']['diff'] for r in per_seed.values()]))
        joint[baseline] = {'state':'REVIEW_REQUIRED' if blocked or not quality_ok else 'RETAIN' if diff<0 else 'DEFER',
                           'mean_nll_diff':diff,'mean_quality':quality,'blocked_seeds':blocked,'seed_results':deepcopy(per_seed)}
    joint_states = [v['state'] for v in joint.values()]
    joint_retention = 'REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in joint_states else 'RETAIN' if all(v=='RETAIN' for v in joint_states) else 'DEFER'
    mixture = {}
    for baseline in MIX_BASELINES:
        key=f'mixture-vs-{baseline}';r=reports[key];blocked=dispositions[key]['state']=='BLOCKED' or not m.numeric_quality(r)
        mixture[baseline]={'state':'REVIEW_REQUIRED' if blocked else 'RETAIN' if r['periods']['all']['diff']<0 else 'DEFER',
                           'report':deepcopy(r)}
    states=[v['state'] for v in mixture.values()]
    mixture_retention='REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in states else 'RETAIN' if all(v=='RETAIN' for v in states) else 'DEFER'
    return {'artifact_kind':'joint_residual_stack_summary','can_adopt':False,'eligible_for_verdict':False,
        'selected':selected,'coefficient_order':order_for(selected),'joint_retention':joint_retention,
        'mixture_retention':mixture_retention,'joint_comparisons':joint,'mixture_comparisons':mixture,
        'preferred_research_configuration':'new_joint_mixed6' if mixture_retention=='RETAIN' else prior_preference,
        'blocked_comparisons':[key for key,r in reports.items() if dispositions[key]['state']=='BLOCKED' or not m.numeric_quality(r)],
        'additional_booster_fits':0,'mixture_bundle_replicates':1,'no_mean_ci':True,'dispositions':dispositions}


def fixed_diagnostics(attributes, evidence):
    identity=[(r['race_id'],r['race_day']) for r in attributes]
    if not evidence or any([(r['race_id'],r['race_day']) for r in rows]!=identity for rows in evidence.values()):
        raise ValueError('125 fixed117 diagnostic population/order differs')
    groups={'2026_all':lambda r:r['year']==2026,
            '2026_nakayama':lambda r:r['year']==2026 and r['venue']=='06',
            '2026_partial_relative':lambda r:r['year']==2026 and r['relative_coverage']=='(.5,1)'}
    result={}
    for name,rule in groups.items():
        indices=[i for i,row in enumerate(attributes) if rule(row)]
        if not indices:raise ValueError('Empty fixed117 diagnostic group')
        result[name]={'n_races':len(indices),'n_days':len({attributes[i]['race_day'] for i in indices}),
            'new_ci':False,'extra_gate':False,
            'mean_diffs':{key:float(np.mean([rows[i]['diff'] for i in indices])) for key,rows in evidence.items()}}
    return result


def summary():
    cfg,frozen=verify();reports,hashes,identities,candidate_rows,evidence={},{},{},{},{}
    for c in frozen['comparisons']:
        if not verified_result(c,cfg,frozen):raise ValueError('125 all results required')
        path=result_path(c); r=reports[c['id']]=s.read_json(path); rows=s.read_json(r['evidence_path'])['rows']
        evidence[c['id']]=rows
        identities[c['id']]=[(v['race_id'],v['race_day']) for v in rows]
        group=c.get('seed','mixture'); values=[v['candidate_winner_nll'] for v in rows]
        if group in candidate_rows and candidate_rows[group]!=values:raise ValueError('Joint candidate differs across comparisons')
        candidate_rows[group]=values
        hashes[c['id']]={'report_sha256':s.p.digest(path),'evidence_sha256':s.p.digest(r['evidence_path'])}
    if any(v!=next(iter(identities.values())) for v in identities.values()):raise ValueError('125 cross-comparison populations differ')
    result=summarize_reports(reports,frozen['sources']['selected'],frozen['sources']['prior_preference'])
    result['fixed_diagnostics']=fixed_diagnostics(s.read_json(a.diagnostic.WORK/'race-diagnostic.json')['rows'],evidence)
    if [(v['n_races'],v['n_days']) for v in result['fixed_diagnostics'].values()]!=[(2296,70),(300,25),(1538,70)]:
        raise ValueError('125 registered fixed117 diagnostic counts differ')
    result.update(sources=hashes,run_freeze_sha256=s.p.digest(WORK/'run-freeze.json'),limitations=cfg['limitations'],
                  population={'n_races':23030,'n_eligible':22990,'n_days':715,'same_ordered_population':True})
    verify();path=SPEC/'verdict.json'
    if path.exists():
        if s.read_json(path)!=result:raise ValueError('125 existing summary changed')
    else:write_json(path,result)
    print({k:result[k] for k in ('joint_retention','mixture_retention','preferred_research_configuration')},flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','evaluate','summary'])
    {'prepare':prepare,'evaluate':evaluate,'summary':summary}[parser.parse_args().action]()
