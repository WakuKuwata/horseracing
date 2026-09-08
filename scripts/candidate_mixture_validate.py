"""129 historical all-head serving parity, using frozen annual caches only.

This certifies an inference implementation, not the performance of the newly
trained deployment bundle. The costly upstream verification runs once per new
audit; immutable input hashes and source/runtime checks bracket the replay.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'serving/src'))
import joint_residual_stack as old
from horseracing_eval.dataset import population_masks
from horseracing_serving import mixture_correction as new

OUTPUT = ROOT / 'specs/129-candidate-mixture-serving/evidence/annual-serving-parity.json'
ATOL = 1e-12
MEMBERS = [(branch, seed) for branch in ('joint', 'anchor') for seed in (42, 43, 44)]


def sha(path):
    return old.s.p.digest(Path(path))


def assert_hashes(hashes):
    for path, expected in hashes.items():
        if sha(path) != expected:
            raise ValueError(f'Parity source changed: {path}')


def compare_heads(actual, expected, ids):
    if list(actual) != ids or list(expected) != ids:
        raise ValueError('Parity horse order/set differs')
    a, b = new._heads(actual, ids), new._heads(expected, ids)
    delta = np.abs(a - b)
    if not np.allclose(a, b, atol=ATOL, rtol=0):
        raise ValueError(f'All-head parity failed: {float(delta.max())}')
    return delta.max(axis=0), len(ids) * 3


def verify_existing(path):
    report = json.loads(Path(path).read_text())
    if (report.get('status') != 'PASS' or report.get('artifact_kind') != 'candidate_annual_serving_parity'
            or report.get('method_sha256') != sha(__file__)
            or report.get('helper_sha256') != sha(new.__file__)
            or report.get('can_adopt') is not False or report.get('eligible_for_verdict') is not False
            or report.get('n_races') != 23030 or report.get('n_eligible') != 22990
            or report.get('n_days') != 715 or report.get('additional_booster_fits') != 0
            or report.get('additional_coefficient_fits') != 0
            or set(report.get('years', {})) != {str(y) for y in range(2020, 2027)}):
        raise ValueError('Invalid existing parity evidence')
    required = {f'{b}-{s}' for b, s in MEMBERS}
    if (not report.get('input_hashes') or report.get('atol') != ATOL
            or sum(v['all_races'] for v in report['years'].values()) != 23030
            or sum(v['eligible_races'] for v in report['years'].values()) != 22990
            or any(set(v['members']) != required or v['all_six_annual_gammas_preserved'] is not True
                   for v in report['years'].values())):
        raise ValueError('Incomplete existing parity accounting')
    error = np.asarray(report.get('max_abs_by_head'))
    if error.shape != (3,) or not np.isfinite(error).all() or (error < 0).any() or (error > ATOL).any():
        raise ValueError('Invalid saved maximum head error')
    assert_hashes(report['input_hashes'])
    if report['research_source_hash'] != old.source_hash() or report['runtime'] != old.q.probe.runtime():
        raise ValueError('Existing parity source/runtime changed')
    return report


def input_paths():
    """Fix cache/report/receipt paths before deserializing their numeric contents."""
    paths = {Path(__file__), Path(new.__file__), old.WORK / 'run-freeze.json',
             old.SPEC / 'gate-config.json', old.SPEC / 'gate-config.hash.txt',
             old.SPEC / 'verdict.json', old.SPEC / 'evidence/independent-review.py',
             old.SPEC / 'evidence/independent-review.json', old.s.p.WORK / 'snapshot.pkl'}
    for module in (old.s, old.a, old.a.d):
        paths.add(module.WORK / 'run-freeze.json')
    prior113 = old.s.read_json(old.s.WORK / 'run-freeze.json')
    prior116 = old.s.read_json(old.a.d.WORK / 'run-freeze.json')
    for record in prior113['source_caches']:
        if record['arm'] in ('anchor', 'pruning') and 2020 <= record['year'] <= 2026:
            paths.update([Path(record['path']), Path(record['receipt_path'])])
    for job in prior116['jobs']:
        if job['arm'] in ('anchor', 'pruning') and 2020 <= job['year'] <= 2026:
            paths.update([old.a.d.WORK / 'cache' / f"{job['key']}.pkl",
                          old.a.d.WORK / 'prefill' / f"{job['key']}.json"])
    for seed in (42, 43, 44):
        for area in (old.seed_area(seed), old.a.seed_area(seed)):
            paths.update([area / 'coefficients.json', area / 'coefficients-receipt.json'])
    for c in old.comparisons(list(old.OPTIONAL)):
        paths.update([old.result_path(c), old.WORK / f"{c['id']}-evidence.json",
                      old.WORK / f"{c['id']}-receipt.json"])
    return sorted(paths)


def run(output=OUTPUT):
    output = Path(output)
    if output.exists():
        verify_existing(output)
        print('129 annual parity verified existing output; no replay or rewrite', flush=True)
        return
    hashes = {str(p): sha(p) for p in input_paths()}
    source = old.source_hash(); runtime = old.q.probe.runtime()
    cfg, frozen = old.verify()  # Exactly one full upstream verification.
    if frozen['sources']['selected'] != list(old.OPTIONAL):
        raise ValueError('129 requires the retained five-term joint branch')
    reports = {}
    report_hashes = {}
    for c in frozen['comparisons']:
        if not old.verified_result(c, cfg, frozen):
            raise ValueError('Incomplete125 evidence')
        report = reports[c['id']] = old.s.read_json(old.result_path(c))
        report_hashes[c['id']] = {'report_sha256': sha(old.result_path(c)),
                                'evidence_sha256': sha(report['evidence_path'])}
    audited = old.s.read_json(old.SPEC / 'evidence/independent-review.json')
    summary = old.s.read_json(old.SPEC / 'verdict.json')
    if (audited.get('status') != 'PASS' or audited.get('report_hashes') != report_hashes
            or audited.get('method_sha256') != sha(old.SPEC / 'evidence/independent-review.py')
            or audited.get('summary_sha256') != sha(old.SPEC / 'verdict.json')
            or audited.get('run_freeze_sha256') != sha(old.WORK / 'run-freeze.json')
            or summary.get('preferred_research_configuration') != 'new_joint_mixed6'):
        raise ValueError('125 independent numerical audit/selection differs')
    joint = {seed: old.verified_coefficients(seed, frozen) for seed in old.SEEDS}
    frozen118 = old.s.read_json(old.a.WORK / 'run-freeze.json')
    anchor = {seed: old.a.verified_coefficients(seed, frozen118) for seed in old.SEEDS}
    matrix, races, folds = old.s.inputs(old.s.load_config())
    matrix_dates = pd.to_datetime(matrix.frame.race_date)
    target = matrix.frame.loc[matrix_dates.between(pd.Timestamp('2020-01-01'),
                               pd.Timestamp('2026-08-23')), new.KEYS + ['days_since_last', 'sex']]
    fresh = new.build_correction_inputs(target, matrix.frame[new.KEYS])
    legacy_prior, _ = old.q.probe.prior_gap_features(matrix.frame)
    legacy_prior = legacy_prior.set_index(['race_id', 'horse_id'])
    season = old.n.candidate_matrix(target)
    keys = list(zip(target.race_id, target.horse_id, strict=True))
    legacy_values = np.column_stack((season[:, 0], legacy_prior.loc[keys, 'prior_gap_log'], season[:, 1:]))
    if not np.array_equal(fresh[new.INPUT_TERMS].to_numpy(), legacy_values, equal_nan=True):
        raise ValueError('New as-of terms differ from frozen research formulas')
    lookup = {key: (str(day.date()), *values) for key, day, values in
              zip(keys, fresh.race_date, legacy_values, strict=True)}
    gap_lookup = {key: (pd.Timestamp(value[0]).date(), value[1]) for key, value in lookup.items()}
    per_race = {rid: group for rid, group in fresh.groupby('race_id', sort=False)}
    del legacy_prior, fresh, target, season, legacy_values, keys
    gc.collect()
    evidence = old.s.read_json(reports['mixture-vs-anchor42']['evidence_path'])['rows']
    years, total_checks, max_error = {}, 0, np.zeros(3)
    ordered_eligible, days, n_races = [], set(), 0
    evidence_pos = 0
    for year in range(2020, 2027):
        fold = folds[year]
        valid = fold.valid
        pop = [population_masks(r) for r in valid]
        expected = frozen['sources']['population'][str(year)]
        if len(valid) != expected['all_races'] or sum(p.eligible for p in pop) != expected['eligible_races']:
            raise ValueError('Annual all/eligible population differs')
        train = [r.context for r in fold.train]
        if max(r.race_date for r in train) >= min(r.context.race_date for r in valid):
            raise ValueError('Annual base model chronology differs')
        all_new, all_old, member_checks = [], [], {}
        for branch, seed in MEMBERS:
            if branch == 'joint':
                factory = old.Factory(old.a.retained_factory(matrix, races, seed), lookup, joint[seed])
                coef, terms = joint[seed]['gammas'][str(year)], new.JOINT_TERMS
            else:
                factory = old.g.TiltFactory(old.a.AnchorFactory(old.a.load_config(), frozen118, matrix, races, seed),
                                           gap_lookup, anchor[seed])
                coef, terms = [anchor[seed]['gammas'][str(year)]], ['gap_log']
            predictor = factory.fit(train, num_threads=1)  # Cache-only factory, no booster fit.
            if not isinstance(predictor.base, old.s.p.ReplayPredictor):
                raise ValueError('Historical parity requires a cache-only raw predictor')
            new_predictions, old_predictions, member_max, checks = [], [], np.zeros(3), 0
            for race in valid:
                ctx = race.context; ids = [h.horse_id for h in ctx.started_horses]
                inputs = per_race[ctx.race_id].set_index('horse_id', drop=False).loc[ids].reset_index(drop=True)
                if set(per_race[ctx.race_id].horse_id) != set(ids):
                    raise ValueError('Started input population differs')
                base = predictor.base.predict_race(ctx)
                current = new.correct_member_predictions(ids, [base[h].win for h in ids], inputs, terms, coef)
                prior = predictor.predict_race(ctx)
                delta, count = compare_heads(current, prior, ids)
                member_max = np.maximum(member_max, delta); checks += count
                new_predictions.append(current); old_predictions.append(prior)
            all_new.append(new_predictions); all_old.append(old_predictions)
            member_checks[f'{branch}-{seed}'] = {'head_checks': checks, 'max_abs_by_head': member_max.tolist()}
            total_checks += checks; max_error = np.maximum(max_error, member_max)
            del predictor, factory
            gc.collect()
        for i, (race, population) in enumerate(zip(valid, pop, strict=True)):
            ctx = race.context; ids = [h.horse_id for h in ctx.started_horses]
            current = new.average_member_predictions([p[i] for p in all_new])
            prior, _ = old.m.average_predictions([p[i] for p in all_old], ids, 6)
            delta, count = compare_heads(current, prior, ids)
            total_checks += count; max_error = np.maximum(max_error, delta)
            days.add(str(ctx.race_date)); n_races += 1
            if population.eligible:
                key = (ctx.race_id, str(ctx.race_date)); row = evidence[evidence_pos]
                if (row['race_id'], row['race_day']) != key:
                    raise ValueError('Eligible NLL evidence order differs')
                nll = -math.log(current[population.winner_horse_id].win)
                if abs(nll - row['candidate_winner_nll']) > ATOL:
                    raise ValueError('New mixture differs from saved125 NLL')
                ordered_eligible.append(key); evidence_pos += 1
        years[str(year)] = {'all_races': len(valid), 'eligible_races': sum(p.eligible for p in pop),
                            'members': member_checks, 'all_six_annual_gammas_preserved': True}
        print(f'129 annual serving parity PASS year={year} races={len(valid)}; no fit', flush=True)
        del all_new, all_old
        gc.collect()
    if (n_races, len(ordered_eligible), len(days), evidence_pos) != (23030, 22990, 715, len(evidence)):
        raise ValueError('Incomplete full historical parity population')
    assert_hashes(hashes)
    if source != old.source_hash() or runtime != old.q.probe.runtime():
        raise ValueError('Source/runtime changed during parity')
    report = {'artifact_kind': 'candidate_annual_serving_parity', 'status': 'PASS',
              'can_adopt': False, 'eligible_for_verdict': False, 'method_sha256': sha(__file__),
              'helper_sha256': sha(new.__file__), 'research_source_hash': source, 'runtime': runtime,
              'input_hashes': hashes, 'report_hashes': report_hashes, 'years': years,
              'n_races': n_races, 'n_eligible': len(ordered_eligible), 'n_days': len(days),
              'all_head_checks': total_checks, 'max_abs_by_head': max_error.tolist(), 'atol': ATOL,
              'eligible_order_sha256': hashlib.sha256(json.dumps(ordered_eligible).encode()).hexdigest(),
              'additional_booster_fits': 0, 'additional_coefficient_fits': 0,
              'scope': 'Historical annual caches and annual coefficients; raw full-information replay only. '
                       'Not final-export parity or future/preweight performance evidence.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as f:
        json.dump(report, f, ensure_ascii=False, indent=2); f.write('\n')
    print(f'129 annual serving parity PASS checks={total_checks}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, default=OUTPUT)
    run(parser.parse_args().output)
