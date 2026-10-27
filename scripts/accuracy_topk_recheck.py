"""132: cached mixed6 head mean vs re-Harville vs strictly prior stage calibration."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import datetime as dt
from pathlib import Path
import pickle
import time
from unittest.mock import patch

import numpy as np

import accuracy_batch_common as common
from horseracing_eval.dataset import population_masks
from horseracing_eval.stage_discount import TopkSample, fit_stage_discount

ARMS = ('head_mean', 'mean_win_harville', 'annual_stage_discount')
BOOTSTRAP = {'b': 4000, 'seed': 20260907, 'alpha': .0125}


def sample_from_record(record):
    pop = population_masks(record.er)
    if not pop.complete_results:
        return None
    p = record.heads[:, 0]
    indices = []
    sets = [set(h for h in record.ids if getattr(pop, f'started_{head}')[h]) for head in common.HEADS]
    for group in (sets[0], sets[1] - sets[0], sets[2] - sets[1]):
        indices.append(record.ids.index(next(iter(group))) if len(group) == 1 else None)
    return TopkSample(tuple(map(float, p)), *indices)


def annual_stage_predictions(records, *, min_races=300):
    records = list(records)
    if len({r.race_id for r in records}) != len(records):
        raise ValueError('Duplicate stage sample races')
    if any(r.race_date > common.CUTOFF for r in records):
        raise ValueError('Stage calibration input exceeds allowed cutoff')
    predictions, fits = {}, []
    prior = []
    for year in sorted({r.year for r in records}):
        current = [r for r in records if r.year == year]
        if any(r.year >= year for r, sample in prior):
            raise ValueError('Stage calibration lookahead')
        samples = [sample for _, sample in prior]
        sd = fit_stage_discount(samples, min_races=min_races)
        fits.append({'year': year, 'fit_from': str(min(r.race_date for r, _ in prior)) if prior else None,
                     'fit_through': str(max(r.race_date for r, _ in prior)) if prior else None,
                     'n_samples': len(samples), 'n_stage2': sd.n_races_l2, 'n_stage3': sd.n_races_l3,
                     'lambda2': sd.lambda2, 'lambda3': sd.lambda3, 'fallback': sd.fallback,
                     'sample_identity_sha256': common.stable_hash([(r.race_id, str(r.race_date)) for r, _ in prior]),
                     'sample_values_sha256': common.stable_hash([(r.race_id, sample.win, sample.i1, sample.i2, sample.i3)
                                                                 for r, sample in prior])})
        for record in current:
            predictions[record.race_id] = common.heads_from_win(record.heads[:, 0], lambda2=sd.lambda2,
                                                               lambda3=sd.lambda3)
        # Append only AFTER this entire year has been predicted. Each regime invokes this separately.
        for record in current:
            sample = sample_from_record(record)
            if sample is not None:
                prior.append((record, sample))
    return predictions, fits


def construct_arms(records, *, min_races=300):
    base = {r.race_id: r.heads for r in records}
    harville = {r.race_id: common.heads_from_win(r.heads[:, 0]) for r in records}
    calibrated, fits = annual_stage_predictions(records, min_races=min_races)
    arms = dict(zip(ARMS, (base, harville, calibrated), strict=True))
    for record in records:
        if any(not np.array_equal(p[record.race_id][:, 0], record.heads[:, 0]) for p in arms.values()):
            raise ValueError('Top-k derivation changed win probabilities')
    return arms, fits


def score_cohort(records, arms, *, bootstrap=None):
    subset = {name: {r.race_id: predictions[r.race_id] for r in records} for name, predictions in arms.items()}
    comparisons = (('reh_arville_minus_head_mean', 'head_mean', 'mean_win_harville'),
                   ('prior_stage_minus_reharville', 'mean_win_harville', 'annual_stage_discount'),
                   ('prior_stage_minus_head_mean', 'head_mean', 'annual_stage_discount'))
    return {'arms': {name: common.summarize_predictions(records, preds) for name, preds in subset.items()},
            'contrasts': {name: common.paired_metrics(records, subset[base], subset[cand], **(bootstrap or BOOTSTRAP))
                          for name, base, cand in comparisons}}


def run(output_dir=None):
    config_path = common.ROOT / 'specs/132-accuracy-first-batch/experiment.json'
    config = common.read_json(config_path)
    if (config['cutoff'] != str(common.CUTOFF) or config['bootstrap'] != BOOTSTRAP
            or config['topk']['arms'] != list(ARMS) or config['topk']['warmup'] != 2020
            or config['topk']['primary_years'] != list(range(2021, 2027))):
        raise ValueError('132 registered experiment differs')
    output_dir = Path(output_dir or common.WORK / 'topk')
    if output_dir.exists():
        raise FileExistsError('Top-k output directory exists; never overwrite evidence')
    output_dir.mkdir(parents=True)
    started = time.monotonic()
    source_paths = [Path(__file__).resolve(), Path(common.__file__).resolve(),
                    common.ROOT / 'serving/src/horseracing_serving/mixture_correction.py',
                    common.ROOT / 'eval/src/horseracing_eval/stage_discount.py',
                    common.ROOT / 'eval/src/horseracing_eval/baselines.py',
                    common.ROOT / 'eval/src/horseracing_eval/dataset.py',
                    common.ROOT / 'eval/src/horseracing_eval/metrics.py']
    source_hashes = {str(p): common.digest(p) for p in source_paths}
    design = {'artifact_kind': '132_topk_recheck_design', 'allowed_data_through': str(common.CUTOFF),
              'experiment_sha256': common.digest(config_path), 'config_hash': common.stable_hash(config),
              'arms': {'head_mean': 'Equal mean of six corrected member heads.',
                       'mean_win_harville': 'Preserve mean win; derive top2/top3 by plain Harville.',
                       'annual_stage_discount': 'Preserve mean win; stage lambdas fitted only on prior annual OOS mixed6 wins of the same regime.'},
              'primary_years': list(range(2021, 2027)), 'warmup_year': 2020,
              'bootstrap': BOOTSTRAP, 'stage_min_races': 300,
              'ci_scope': 'Saved model predictions and fitted annual lambdas fixed; paired race-day ratio bootstrap; not selection-adjusted or refit uncertainty.',
              'additional_booster_fits': 0, 'can_adopt': False, 'eligible_for_verdict': False,
              'source_hashes': source_hashes}
    common.write_json(output_dir / 'design.json', design)
    print('132 topk: loading pinned snapshot; no DB/model fits', flush=True)
    inputs = common.load_frozen_inputs()
    result = {'artifact_kind': '132_topk_recheck_summary', 'design_sha256': common.digest(output_dir / 'design.json'),
              'regimes': {}, 'additional_booster_fits': 0, 'can_adopt': False, 'eligible_for_verdict': False}
    with ExitStack() as stack:
        for target in ('horseracing_training.win_model.WinModel.fit',
                       'horseracing_training.predictor.LightGBMPredictor.fit',
                       'horseracing_training.calib_split.OofCalibratedPredictor.fit'):
            stack.enter_context(patch(target, side_effect=RuntimeError('132 topk prohibits model fitting')))
        for regime in ('preweight', 'full'):
            t0 = time.monotonic()
            records = []
            for record in common.iter_mixed_records(inputs, regime):
                records.append(record)
                if len(records) % 1000 == 0:
                    print(f'132 topk: {regime} reconstructed {len(records)} races ({time.monotonic()-t0:.0f}s)', flush=True)
            print(f'132 topk: {regime} fitting prior-year stage lambdas', flush=True)
            arms, fits = construct_arms(records)
            primary = [r for r in records if r.year >= 2021]
            report = {'annual_stage_fits': fits, 'primary_2021_onward': score_cohort(primary, arms),
                      'auxiliary_2020_onward': score_cohort(records, arms), 'by_year': {}}
            for year in common.YEARS:
                report['by_year'][str(year)] = score_cohort([r for r in records if r.year == year], arms)
            evidence = [{'race_id': r.race_id, 'race_date': str(r.race_date), 'ids': r.ids,
                         'n_result_rows': r.er.n_result_rows,
                         'labels': [(sl.horse_id, sl.win, sl.top2, sl.top3) for sl in r.er.labels],
                         'arms': {name: p[r.race_id] for name, p in arms.items()}} for r in records]
            evidence_path = output_dir / f'{regime}-predictions.pkl'
            with evidence_path.open('xb') as fh:
                pickle.dump(evidence, fh, protocol=5)
            report['predictions_sha256'] = common.digest(evidence_path)
            report['elapsed_seconds'] = time.monotonic() - t0
            common.write_json(output_dir / f'{regime}-summary.json', report)
            result['regimes'][regime] = report
            print(f'132 topk: {regime} complete ({report["elapsed_seconds"]:.0f}s)', flush=True)
            del records, arms, primary, evidence
    if (source_hashes != {str(p): common.digest(p) for p in source_paths}
            or common.digest(config_path) != design['experiment_sha256']):
        raise ValueError('Source changed during top-k evaluation')
    result['provenance'] = inputs.provenance
    result['elapsed_seconds'] = time.monotonic() - started
    common.write_json(output_dir / 'summary.json', result)
    print(f'132 topk complete: {output_dir / "summary.json"}', flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    run(args.output_dir)
