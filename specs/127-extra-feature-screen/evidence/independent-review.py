"""Independent127 scalar audit of immutable forecasts; no training or calibration."""
from __future__ import annotations

import gc
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
HELPER = ROOT / 'specs/122-market-feature-screen/evidence/independent-review.py'
scalar = types.ModuleType('scalar122_for_127')
scalar.__file__ = str(HELPER)
exec(compile(HELPER.read_text(), str(HELPER), 'exec'), scalar.__dict__)
COUNTS = {'baseline': 125, 'f04': 131, 'finish_decomp': 135, 'weight_deviation': 126}


def main():
    import extra_feature_screen as m
    output = Path(__file__).with_suffix('.json')
    assert not output.exists(), 'Preserve completed127 audit'
    cfg, frozen = m.verify()
    assert m.NAMES == list(COUNTS) and len(frozen['jobs']) == 3
    assert not (m.WORK / 'running.lock').exists() and all(m.completed(j) for j in frozen['jobs'])
    assert frozen['baseline']['mode'] == 'native_cache'
    summary_path = m.SPEC / 'verdict.json'
    start = dict(frozen['sources']['files'])
    feature_path = m.SPEC / 'evidence/independent-feature-review.json'
    feature_method = feature_path.with_suffix('.py')
    initial_paths = [Path(__file__), HELPER, summary_path, m.WORK / 'run-freeze.json', m.WORK / 'matrix.pkl',
                     feature_path, feature_method, Path(frozen['baseline']['path']),
                     Path(frozen['baseline']['anchor_cache_path'])]
    for job in frozen['jobs']:
        initial_paths.extend([m.WORK / 'cache' / f"{job['key']}.pkl", m.receipt_path(job['key'])])
    for name in list(COUNTS)[1:]:
        initial_paths.extend([m.result_path(name), m.WORK / f'{name}-evidence.json', m.result_receipt(name)])
    for p in initial_paths:
        start[str(p)] = m.s.p.digest(p)
    summary = m.s.read_json(summary_path)
    feature = m.s.read_json(feature_path)
    assert feature['status'] == 'PASS' and feature['can_adopt'] is False and feature['eligible_for_verdict'] is False
    assert feature['additional_fits'] == 0 and feature['horse_rows'] == 958011 and feature['feature_cells'] == 958011 * 17
    assert feature['method_sha256'] == start[str(feature_method)]
    assert feature['run_freeze_sha256'] == start[str(m.WORK / 'run-freeze.json')]
    assert feature['matrix_sha256'] == frozen['matrix_sha256'] == start[str(m.WORK / 'matrix.pkl')]
    assert feature['source_frames_sha256'] == m.s.p.digest(m.s.p.WORK / 'source-frames.pkl')
    assert feature['strict_prior_dates'] is True and feature['same_day_excluded'] is True
    matrix, races, fold = m.inputs(cfg)
    valid = list(fold.valid)
    assert len(valid) == 3454 and fold.valid_year == 2018
    days = {r.context.race_id: str(r.context.race_date) for r in valid}
    drops = next(c['drop_features'] for c in m.s.old.load_config()['candidates'] if c['id'] == 'relative_ability')
    native_matrix = m.native_matrix(matrix)
    key, train_hash, native, native_path = m.native_source(native_matrix, races, fold, drops)
    assert str(native_path) == frozen['baseline']['path'] and key == frozen['baseline']['key']
    assert train_hash == frozen['baseline']['train_hash']
    baseline_factory = m.Factory(cfg, frozen, matrix, races, 'baseline')
    assert native.expected_columns == baseline_factory.expected_columns
    assert m.s.strip_drops(native.recipe_meta) == m.s.strip_drops(baseline_factory.recipe_meta)
    cache_hashes, scores, receipts = {}, {}, {}
    for name in COUNTS:
        if name == 'baseline':
            path = native_path
            cache = m.s.reuse.load(path)
            m.s.reuse.check_payload(cache, key, train_hash, native, valid)
        else:
            factory = m.Factory(cfg, frozen, matrix, races, name)
            job = next(j for j in frozen['jobs'] if j['arm'] == name)
            assert m.job_for(factory, fold) == job and m.completed(job)
            path = m.WORK / 'cache' / f"{job['key']}.pkl"
            cache = m.s.reuse.load(path)
            m.check_payload(cache, job['key'], job['train_hash'], factory, valid)
            assert cache['actual_params']['colsample_bytree'] == 1.0
            receipt_path = m.receipt_path(job['key'])
            receipts[name] = m.s.p.digest(receipt_path)
            assert start[str(receipt_path)] == receipts[name]
        assert len(cache['feature_columns']) == COUNTS[name]
        assert cache['oof_info']['sufficient'] is True
        cache_hashes[name] = m.s.p.digest(path)
        assert start[str(path)] == cache_hashes[name]
        scores[name] = scalar.inspect(valid, cache['predictions'])
        del cache
        gc.collect()
    del matrix, native_matrix, native, baseline_factory
    gc.collect()
    baseline = scores['baseline']
    assert len(baseline['winner_rows']) == 3448
    records, hashes = {}, {}
    for name in list(COUNTS)[1:]:
        assert m.verified_result(name, cfg, frozen)
        path = m.result_path(name)
        report = m.s.read_json(path)
        assert report['evidence_path'] == str(m.WORK / f'{name}-evidence.json')
        evidence = m.s.read_json(report['evidence_path'])
        candidate = scores[name]
        assert report['can_adopt'] is False and report['eligible_for_verdict'] is False
        assert list(candidate['winner_rows']) == list(baseline['winner_rows']) == [r['race_id'] for r in evidence['rows']]
        reconstructed = []
        for row in evidence['rows']:
            rid = row['race_id']
            assert row['race_day'] == days[rid]
            c, b = candidate['winner_rows'][rid], baseline['winner_rows'][rid]
            for actual, saved in [(c, row['candidate_winner_nll']), (b, row['active_winner_nll']), (c - b, row['diff'])]:
                scalar.close(actual, saved)
            reconstructed.append({'race_id': rid, 'race_day': days[rid], 'diff': c - b})
        diff = candidate['nll'] - baseline['nll']
        top2 = candidate['top2'] - baseline['top2']
        top3 = candidate['top3'] - baseline['top3']
        reasons = report['gate']['reasons']
        for actual, saved in [(candidate['nll'], report['periods']['all']['candidate']),
                              (baseline['nll'], report['periods']['all']['active']),
                              (diff, report['periods']['all']['diff']),
                              (top2, reasons['top2_diff']), (top3, reasons['top3_diff']),
                              (candidate['ece'], reasons['cand_ece']), (baseline['ece'], reasons['act_ece'])]:
            scalar.close(actual, saved)
        quality = top2 <= .0005 and top3 <= .0005 and candidate['ece'] < .05 and candidate['ece'] - baseline['ece'] <= .001
        progression = 'QUALITY_REVIEW_REQUIRED' if not quality else 'ADVANCE_TO_FULL_RESEARCH' if diff < 0 else 'DEFER'
        assert report['progression'] == progression
        ci = scalar.bootstrap(reconstructed)
        assert ci['n_days'] == report['total_ci']['n_days'] == 109
        assert report['seed_noise']['n_folds'] == report['seed_noise']['k_seeds'] == 1
        assert report['seed_noise']['sd_fold'] == .001816
        for field, bounds in [('bootstrap_ci', ci['sample']), ('total_ci', ci['total'])]:
            scalar.close(ci['point'], report[field]['point'])
            scalar.close(bounds[0], report[field]['ci_low'])
            scalar.close(bounds[1], report[field]['ci_high'])
        hashes[name] = {'report_sha256': m.s.p.digest(path), 'evidence_sha256': m.s.p.digest(report['evidence_path'])}
        for p in [path, Path(report['evidence_path']), m.result_receipt(name)]:
            assert start[str(p)] == m.s.p.digest(p)
        records[name] = {'nll': candidate['nll'], 'diff': diff, 'top2_diff': top2, 'top3_diff': top3,
                         'candidate_ece': candidate['ece'], 'baseline_ece': baseline['ece'],
                         'progression': progression, 'ci': ci}
    assert summary['can_adopt'] is False and summary['eligible_for_verdict'] is False
    assert summary['run_freeze_sha256'] == start[str(m.WORK / 'run-freeze.json')]
    assert summary['reports'] == {n: {'sha256': hashes[n]['report_sha256'], 'progression': records[n]['progression']} for n in list(COUNTS)[1:]}
    m.verify()
    assert all(m.completed(j) for j in frozen['jobs'])
    assert all(m.verified_result(n, cfg, frozen) for n in list(COUNTS)[1:])
    assert all(m.s.p.digest(path) == sha for path, sha in start.items())
    m.write_json(output, {
        'artifact_kind': 'extra_feature_screen_independent_review', 'status': 'PASS',
        'can_adopt': False, 'eligible_for_verdict': False, 'additional_fits': 0,
        'method_sha256': start[str(Path(__file__))], 'helper_method_sha256': start[str(HELPER)],
        'feature_review_sha256': start[str(feature_path)], 'feature_method_sha256': start[str(feature_method)],
        'run_freeze_sha256': start[str(m.WORK / 'run-freeze.json')], 'summary_sha256': start[str(summary_path)],
        'report_hashes': hashes, 'cache_hashes': cache_hashes, 'completion_receipt_hashes': receipts,
        'source_cache_report_hashes_start_end_equal': True,
        'population': {'n_races': 3454, 'n_eligible': 3448, 'n_days': 109,
                       'complete_started_horses': baseline['complete_started_horses'],
                       'incomplete_races': baseline['incomplete_races']},
        'comparisons': records, 'checks': scalar.CHECKS, 'max_numeric_error': scalar.MAX_ERROR,
        'limitations': ['Single2018 historical-development screen. No full-period, recent-year, stack or production efficacy claim.',
                       'Scalar metric and bootstrap primitives reused from independently reviewed122 audit, with this study independently bound.',
                       'Cached forecasts audited without independent model retraining. Feature formula reconstruction is a separate audit.']})
    print('127 independent forecast/metric/CI/progression audit PASS', scalar.CHECKS, scalar.MAX_ERROR, flush=True)


if __name__ == '__main__':
    main()
