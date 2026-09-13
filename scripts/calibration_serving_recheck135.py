"""135 Step1: controlled 131 display/calibration replay on sealed 132 predictions.

This cannot replay historical production lambda values: the supplied material does
not include the contemporaneous latest-run selection and exact runtime lambdas.
No runtime loader, database query, or booster fitting is permitted here.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
import datetime as dt
from pathlib import Path
import pickle
import sys
import time
from unittest.mock import patch

import numpy as np

import accuracy_batch_common as common
from horseracing_eval.dataset import EvalRace, ScoringLabel, population_masks
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_eval.stage_discount import StageDiscount, TopkSample, fit_stage_discount
from horseracing_probability.model_calibration import to_topk_samples
from horseracing_training.predictor import assemble_predictions

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/132-accuracy-first-batch/topk'
WORK = ROOT / 'artifacts/135-mixture-reproducibility/calibration/isolated-replay'
EXPERIMENT = ROOT / 'specs/135-mixture-reproducibility/experiment.json'
END = dt.date(2026, 8, 23)
SUMMARY_SHA = '1f85e49697ef3b5fa1d7ed9a195fb15b3cf1acd5799346493426928b339b5098'
PREDICTION_SHA = {
    'full': 'aa6ae40a6ebe7ee94bdd402da641520771cc6001b33a20437fdadc2bad4263d0',
    'preweight': 'f7e9f8c202455d4435a57aaffde7e2580c2c2e2a530b0b882403bb6bc87b42c0',
}
ARMS = ('c132', 'native131_same_lambda', 'native131_complete_annual', 'native131_available_annual')
BOOTSTRAP = {'b': 4000, 'seed': 20260907, 'alpha': .0125}
CONFIG = {'cutoff': '2026-09-06', 'source_end': str(END), 'years': list(range(2020, 2027)),
          'primary_years': list(range(2021, 2027)), 'warmup': 2020,
          'regimes': list(PREDICTION_SHA), 'arms': list(ARMS), 'stage_min_races': 300,
          'bootstrap': BOOTSTRAP, 'comparison_boundary': 'annual-prior-only',
          'can_adopt': False, 'eligible_for_verdict': False}
COMPARISONS = ('same_lambda_131_vs_132', 'native_complete_vs_132', 'native_including_incomplete_vs_132')
FORBIDDEN_CALLS = (
    'horseracing_training.win_model.WinModel.fit',
    'horseracing_training.predictor.LightGBMPredictor.fit',
    'horseracing_training.calib_split.OofCalibratedPredictor.fit',
    'horseracing_probability.model_calibration.load_topk_samples',
    'horseracing_probability.model_calibration.fit_product_stage_discount',
    'sqlalchemy.orm.Session.execute', 'sqlalchemy.orm.Session.scalar',
)


@dataclass
class DisplayRace:
    er: EvalRace
    ids: tuple[str, ...]
    heads: np.ndarray

    @property
    def race_id(self):
        return self.er.context.race_id

    @property
    def race_date(self):
        return self.er.context.race_date

    @property
    def year(self):
        return self.race_date.year


def checked_load(path, expected):
    if common.digest(path) != expected:
        raise ValueError(f'Input SHA mismatch before pickle: {path}')
    with Path(path).open('rb') as f:
        return pickle.load(f)


def source_summary():
    path = SOURCE / 'summary.json'
    if common.digest(path) != SUMMARY_SHA:
        raise ValueError('132 summary SHA differs')
    summary = common.read_json(path)
    p = summary['provenance']
    if (p['actual_data_through'] != str(END) or p['allowed_data_through'] != CONFIG['cutoff']
            or p['snapshot_sha256'] != common.SNAPSHOT_SHA256):
        raise ValueError('132 metadata cutoff or source identity differs')
    for regime, sha in PREDICTION_SHA.items():
        if summary['regimes'][regime]['predictions_sha256'] != sha:
            raise ValueError('132 prediction receipt differs')
    return summary


def experiment_contract():
    config = common.read_json(EXPERIMENT)
    expected = {'regimes': CONFIG['regimes'], 'source_end': CONFIG['source_end'],
                'warmup_year': CONFIG['warmup'], 'primary_years': CONFIG['primary_years'],
                'min_races': CONFIG['stage_min_races'], 'fit_boundary': 'strictly prior evaluation years',
                'comparisons': list(COMPARISONS), 'actual_runtime_latest_replay': False,
                'additional_booster_fits': 0}
    if (config.get('cutoff') != CONFIG['cutoff'] or config.get('historical_source_end') != str(END)
            or config.get('calibration') != expected or config.get('bootstrap') != BOOTSTRAP
            or config.get('can_adopt') is not False or config.get('eligible_for_verdict') is not False
            or config.get('step_order') != ['calibration', 'repro', 'recent']):
        raise ValueError('135 registered calibration experiment differs')
    return config


def decode_rows(rows):
    if not rows or len({r['race_id'] for r in rows}) != len(rows):
        raise ValueError('Empty or duplicate saved races')
    records = []
    for row in rows:
        day = dt.date.fromisoformat(row['race_date'])
        ids = tuple(row['ids'])
        if (not dt.date(2020, 1, 1) <= day <= END or not ids or len(set(ids)) != len(ids)
                or row['n_result_rows'] is None):
            raise ValueError('Saved race date or population outside fixed input contract')
        if len(row['labels']) != len({sl[0] for sl in row['labels']}):
            raise ValueError('Duplicate finished labels')
        for sl in row['labels']:
            if len(sl) != 4 or any(x not in (0, 1) for x in sl[1:]) or not sl[1] <= sl[2] <= sl[3]:
                raise ValueError('Invalid cumulative finished labels')
        er = EvalRace(RaceContext(row['race_id'], day, tuple(HorseEntry(h) for h in ids)),
                      tuple(ScoringLabel(*sl) for sl in row['labels']), row['n_result_rows'])
        for p in row['arms'].values():
            common.validate_heads(p, len(ids))
        win = row['arms']['head_mean'][:, 0]
        if any(not np.array_equal(win, p[:, 0]) for p in row['arms'].values()):
            raise ValueError('132 stored arms disagree on win')
        records.append(DisplayRace(er, ids, row['arms']['head_mean']))
    if [(r.race_date, r.race_id) for r in records] != sorted((r.race_date, r.race_id) for r in records):
        raise ValueError('Saved evaluation order differs')
    return records


def native_display(ids, win, sd):
    """Exact 131 display suffix: assemble eps=0, then retain original mixture win."""
    win = np.asarray(win, dtype=float)
    if (not len(win) or win.shape != (len(ids),) or len(set(ids)) != len(ids)
            or not np.isfinite(win).all() or (win <= 0).any() or (win > 1).any()
            or not np.isclose(win.sum(), 1., atol=1e-8, rtol=0)):
        raise ValueError('Invalid fixed mixture win vector')
    got = assemble_predictions(list(ids), win, eps=0., stage_discount=sd)
    if max(abs(got[h].win - win[i]) for i, h in enumerate(ids)) > 1e-12:
        raise ValueError('Display assembly changed win beyond 131 contract')
    return common.validate_heads(np.array([[win[i], got[h].top2, got[h].top3]
                                           for i, h in enumerate(ids)]), len(ids))


def c132_sample(record):
    pop = population_masks(record.er)
    if not pop.complete_results:
        return None
    sets = [set(h for h in record.ids if getattr(pop, f'started_{k}')[h]) for k in common.HEADS]
    positions = [record.ids.index(next(iter(s))) if len(s) == 1 else None
                 for s in (sets[0], sets[1] - sets[0], sets[2] - sets[1])]
    return TopkSample(tuple(map(float, record.heads[:, 0])), *positions)


def native_raw_sample(record):
    # Cumulative finished labels exactly identify unique ranks 1/2/3, including ties.
    # Match native _placed_finishers: all finished labels, with no started filter here.
    groups = ([s.horse_id for s in record.er.labels if s.win],
              [s.horse_id for s in record.er.labels if s.top2 and not s.win],
              [s.horse_id for s in record.er.labels if s.top3 and not s.top2])
    placed = tuple(g[0] if len(g) == 1 else None for g in groups)
    return record.race_id, record.race_date, dict(zip(record.ids, map(float, record.heads[:, 0]))), placed


def sample_key(samples):
    return common.stable_hash([(s.win, s.i1, s.i2, s.i3) for s in samples])


def controlled_annual(records, saved_fits, *, min_races=300):
    records = list(records)
    if any(r.race_date > END for r in records):
        raise ValueError('Annual fitting data exceeds sealed cutoff')
    fits_by_year = {f['year']: f for f in saved_fits}
    arms = {k: {} for k in ARMS[1:]}
    audits, prior, n_calls = [], [], 0
    for year in sorted({r.year for r in records}):
        current = [r for r in records if r.year == year]
        if any(r.race_date >= dt.date(year, 1, 1) for r in prior):
            raise ValueError('Annual fitting lookahead')
        original = fits_by_year[year]
        complete = [(r, s) for r in prior if (s := c132_sample(r)) is not None]
        ids_hash = common.stable_hash([(r.race_id, str(r.race_date)) for r, _ in complete])
        values_hash = common.stable_hash([(r.race_id, s.win, s.i1, s.i2, s.i3) for r, s in complete])
        if (ids_hash != original['sample_identity_sha256'] or values_hash != original['sample_values_sha256']
                or (original['fit_through'] is not None and original['fit_through'] >= f'{year}-01-01')):
            raise ValueError('Stored C calibration sample identity or boundary differs')
        original_sd = StageDiscount(original['lambda2'], original['lambda3'],
                                    original['n_stage2'], original['n_stage3'], original['fallback'])
        raw_complete = [native_raw_sample(r) for r, _ in complete]
        raw_available = [native_raw_sample(r) for r in prior]
        converted = {ARMS[2]: to_topk_samples(raw_complete), ARMS[3]: to_topk_samples(raw_available)}
        discounts, conversion, memo = {ARMS[1]: original_sd}, {}, {}
        for arm, samples in converted.items():
            key = sample_key(samples)
            reused = key in memo
            if not reused:
                memo[key] = fit_stage_discount(samples, min_races=min_races)
                n_calls += 1
            sd = discounts[arm] = memo[key]
            conversion[arm] = {**asdict(sd), 'n_samples': len(samples),
                               'converted_sample_sha256': key, 'identical_fit_reused': reused}
        changed = []
        for r, s in complete:
            raw = native_raw_sample(r)
            p, placed = raw[2], raw[3]
            native = to_topk_samples([raw])
            if native:
                ordered = sorted(p)
                delta = max(abs(native[0].win[ordered.index(h)] - p[h]) for h in p)
                if delta:
                    changed.append((r.race_id, delta, min(p.values()) < 1e-9))
        audits.append({'year': year, 'fit_from': str(prior[0].race_date) if prior else None,
                       'fit_through': str(prior[-1].race_date) if prior else None,
                       'n_prior_races': len(prior), 'n_prior_complete': len(complete),
                       'incomplete_prior_ids': [r.race_id for r in prior if not population_masks(r.er).complete_results],
                       'c132_saved': original, 'controlled_fits': conversion,
                       'normalization_changed_races': len(changed),
                       'normalization_max_abs_diff': max((d for _, d, _ in changed), default=0.),
                       'below_engine_clip_races': sum(c for _, _, c in changed)})
        for r in current:
            for arm, sd in discounts.items():
                arms[arm][r.race_id] = native_display(r.ids, r.heads[:, 0], sd)
        print(f'135 calibration annual {year}: prior={len(prior)} complete={len(complete)}', flush=True)
        prior.extend(current)
    return arms, audits, n_calls


def score_cohort(records, arms, *, paired):
    subset = {a: {r.race_id: p[r.race_id] for r in records} for a, p in arms.items()}
    result = {'arms': {a: common.summarize_predictions(records, p) for a, p in subset.items()}}
    differences = {}
    for name, base, candidate in (
        (COMPARISONS[0], ARMS[0], ARMS[1]),
        (COMPARISONS[1], ARMS[0], ARMS[2]),
        (COMPARISONS[2], ARMS[0], ARMS[3]),
        ('decomposition_native_complete_minus_same_lambda', ARMS[1], ARMS[2]),
        ('decomposition_native_available_minus_complete', ARMS[2], ARMS[3]),
    ):
        if paired:
            differences[name] = common.paired_metrics(records, subset[base], subset[candidate], **BOOTSTRAP)
        else:
            b, c = result['arms'][base], result['arms'][candidate]
            differences[name] = {'winner_nll': c['winner_nll'] - b['winner_nll'],
                **{f'{h}_{m}': c['heads'][h][m] - b['heads'][h][m]
                   for h in ('top2', 'top3') for m in ('logloss', 'brier', 'ece')}}
    result['contrasts'] = differences
    return result


def execution_sources():
    names = ['scripts/calibration_serving_recheck135.py', 'scripts/accuracy_batch_common.py',
             'serving/src/horseracing_serving/mixture_serving.py', 'serving/src/horseracing_serving/pipeline.py',
             'training/src/horseracing_training/predictor.py',
             'probability/src/horseracing_probability/model_calibration.py',
             'probability/src/horseracing_probability/fl_bias.py', 'probability/src/horseracing_probability/engine.py',
             'eval/src/horseracing_eval/stage_discount.py', 'eval/src/horseracing_eval/baselines.py',
             'eval/src/horseracing_eval/dataset.py', 'eval/src/horseracing_eval/metrics.py']
    return {str(ROOT / n): common.digest(ROOT / n) for n in names}


@contextmanager
def guarded_execution():
    with ExitStack() as stack:
        for target in FORBIDDEN_CALLS:
            stack.enter_context(patch(target, side_effect=RuntimeError('135 prohibits model fitting and DB/runtime reads')))
        yield


def run():
    experiment = experiment_contract()
    experiment_sha = common.digest(EXPERIMENT)
    source = source_summary()  # Date metadata and registered receipts before binary reads.
    if WORK.exists():
        raise FileExistsError('Calibration output exists; never overwrite evidence')
    WORK.mkdir(parents=True)
    t0 = time.monotonic()
    sources = execution_sources()
    input_hashes = {str(SOURCE / 'summary.json'): SUMMARY_SHA,
                    **{str(SOURCE / f'{k}-predictions.pkl'): v for k, v in PREDICTION_SHA.items()}}
    design = {'artifact_kind': '135_calibration_controlled_design', 'config': CONFIG,
              'config_sha256': common.stable_hash(CONFIG), 'source_sha256': sources,
              'experiment_sha256': experiment_sha, 'registered_calibration': experiment['calibration'],
              'input_sha256': input_hashes, 'runtime': {'python': sys.version, 'numpy': np.__version__},
              'actual_production_replay': False,
              'production_difference_status': 'NOT_RECONSTRUCTIBLE_FROM_SUPPLIED_FIXED_INPUTS',
              'production_difference_limitation': 'Exact historical runtime lambda and contemporaneous latest-run material are not present in the sealed 130/132 inputs; no actual production performance delta is estimated.',
              'controlled_comparison': 'Same prior-year OOS mixed6 material and information regime. 131 native conversion is tested with complete-only material and separately with all available prior rows.',
              'unmeasured_differences': ['daily versus annual update', 'historical latest-run and model-generation mixture',
                                       'pre-2020 runtime material', 'original prediction/publication availability'],
              'forbidden_calls': list(FORBIDDEN_CALLS), 'can_adopt': False, 'eligible_for_verdict': False}
    common.write_json(WORK / 'design.json', design)
    result = {'artifact_kind': '135_calibration_controlled_summary', 'design_sha256': common.digest(WORK / 'design.json'),
              'actual_data_through': str(END), 'allowed_data_through': CONFIG['cutoff'],
              'actual_production_replay': False, 'production_difference_status': design['production_difference_status'],
              'regimes': {}, 'additional_booster_fits': 0, 'stage_fit_calls': 0,
              'can_adopt': False, 'eligible_for_verdict': False}
    labels_identity = None
    with guarded_execution():
        for regime in PREDICTION_SHA:
            print(f'135 calibration: {regime} saved prediction replay', flush=True)
            rows = checked_load(SOURCE / f'{regime}-predictions.pkl', PREDICTION_SHA[regime])
            records = decode_rows(rows)
            identity = common.stable_hash([(r['race_id'], r['race_date'], r['ids'], r['labels'], r['n_result_rows']) for r in rows])
            if labels_identity is not None and identity != labels_identity:
                raise ValueError('Full/preweight labels differ')
            labels_identity = identity
            report132 = source['regimes'][regime]
            controlled, fits, n_calls = controlled_annual(records, report132['annual_stage_fits'])
            arms = {ARMS[0]: {r['race_id']: r['arms']['annual_stage_discount'] for r in rows}, **controlled}
            max_diff = np.zeros(3)
            for r in records:
                for p in arms.values():
                    if not np.array_equal(p[r.race_id][:, 0], r.heads[:, 0]):
                        raise ValueError('A calibration comparison changed win')
                max_diff = np.maximum(max_diff, np.abs(arms[ARMS[0]][r.race_id] - arms[ARMS[1]][r.race_id]).max(axis=0))
            if max_diff.max() > 1e-12:
                raise ValueError('Same-win/same-lambda display differs beyond numeric tolerance')
            report = {'same_lambda_max_abs_diff_by_head': dict(zip(common.HEADS, max_diff.tolist())),
                      'annual_fits': fits,
                      'primary_2021_onward': score_cohort([r for r in records if r.year >= 2021], arms, paired=True),
                      'auxiliary_2020_onward': score_cohort(records, arms, paired=False),
                      'by_year': {str(y): score_cohort([r for r in records if r.year == y], arms, paired=False)
                                  for y in CONFIG['years']}}
            replayed = report['auxiliary_2020_onward']['arms'][ARMS[0]]
            original = report132['auxiliary_2020_onward']['arms']['annual_stage_discount']
            if replayed != original:
                raise ValueError('Stored C score did not exactly reproduce 132')
            evidence = [{**{k: r[k] for k in ('race_id', 'race_date', 'ids', 'labels', 'n_result_rows')},
                         'arms': {a: p[r['race_id']] for a, p in arms.items()}} for r in rows]
            path = WORK / f'{regime}-predictions.pkl'
            with path.open('xb') as f:
                pickle.dump(evidence, f, protocol=5)
            report['predictions_sha256'] = common.digest(path)
            common.write_json(WORK / f'{regime}-summary.json', report)
            result['regimes'][regime] = report
            result['stage_fit_calls'] += n_calls
            del records, rows, arms, evidence, controlled
            print(f'135 calibration: {regime} complete ({time.monotonic() - t0:.1f}s total)', flush=True)
    if (execution_sources() != sources or common.digest(EXPERIMENT) != experiment_sha
            or experiment_contract() != experiment
            or any(common.digest(p) != sha for p, sha in input_hashes.items())):
        raise ValueError('Code or sealed input changed during replay')
    result['labels_identity_sha256'] = labels_identity
    result['elapsed_seconds'] = time.monotonic() - t0
    common.write_json(WORK / 'summary.json', result)
    common.write_json(WORK / 'receipt.json', {'status': 'STEP1_COMPLETE',
        'summary_sha256': common.digest(WORK / 'summary.json'), 'design_sha256': result['design_sha256'],
        'experiment_sha256': experiment_sha,
        'source_sha256': sources, 'input_sha256': input_hashes, 'additional_booster_fits': 0,
        'actual_production_replay': False, 'can_adopt': False, 'eligible_for_verdict': False})
    print('135 calibration STEP1 COMPLETE', flush=True)
    return result


if __name__ == '__main__':
    run()
