"""129: six file-only final models; no DB registration or coefficient fitting.

Only this module and its tests enter the new build-code freeze. The existing training
and research dependencies remain pinned; unrelated new serving modules can develop
while these jobs run. Final bundle assembly pins its own complete serving runtime.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import datetime as dt
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import pickle
import resource
import subprocess
import sys
import time
from unittest.mock import patch

import lightgbm as lgb
import numpy as np
import pandas as pd

import joint_residual_stack as research
import small_gain_stack as s
from horseracing_training.artifacts import (
    _write_model, build_preprocessor, categorical_vocab_from_booster, check_artifact_root,
    feature_hash, vocab_hash,
)
from horseracing_training.calib_split import OofCalibratedPredictor, day_block_partition
from horseracing_training.predictor import assemble_predictions
from horseracing_training.target_encoding import apply_encoded_columns

ROOT = s.ROOT
SPEC = ROOT / 'specs/129-candidate-mixture-serving'
WORK = ROOT / 'artifacts/129-candidate-mixture-serving'
# The frozen training venv need not install the serving project as a dependency.
sys.path.insert(0, str(ROOT / 'serving/src'))
from horseracing_serving.model_loader import ServingModel

MEMBERS = [dict(id=f'{label}-{seed}', branch=branch, seed=seed)
           for label, branch in [('joint', 'pruning'), ('anchor', 'anchor')] for seed in [42, 43, 44]]
FILES = ('model.txt', 'calibrator.pkl', 'preprocessor.pkl', 'metadata.json')
CUTOFF = dt.date(2026, 8, 23)
digest = s.p.digest
read_json = s.read_json


def clean(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def write_json(path, value):
    """Serialize completely before creating an exclusive, never-overwritten file."""
    text = json.dumps(clean(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n'
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as fh:
        fh.write(text); fh.flush(); os.fsync(fh.fileno())


def member_for(name):
    for member in MEMBERS:
        if name == member['id']:
            return dict(member)
    raise ValueError('Unknown registered member')


def build_config():
    return dict(schema_version=1, artifact_kind='candidate_mixture_build_config',
                profile='125_new_joint_mixed6_v1', mode='shadow',
                train_from='2007-01-01', train_through=str(CUTOFF), coefficient_train_through='2025-12-31',
                members=deepcopy(MEMBERS), full_outer_fits=6, full_boosters=48,
                n_estimators=900, n_oof_blocks=8, num_threads=1, max_workers=2,
                weight_mask_rate=.5, weight_mask_seed=20260810, base_eps=1e-6,
                db_registration=False, additional_coefficient_fits=0,
                can_adopt=False, eligible_for_verdict=False,
                smoke={'train_through': '2007-12-31', 'probe_from': '2008-01-01',
                       'probe_through': '2008-01-31', 'n_estimators': 5, 'n_oof_blocks': 2,
                       'outer_fits': 6, 'boosters': 12},
                parity={'scope': 'frozen raw inputs, all started horses and all three heads on fixed year representatives',
                        'absolute_tolerance': 0., 'purpose': 'serialization only; no efficacy or serving-regime claim'})


def build_source_paths():
    # Existing package source is already bound by research.source_hash(). These two
    # old serving modules define ServingModel.raw_predict and its package import.
    return [Path(__file__).resolve(), ROOT / 'scripts/tests/test_candidate_mixture_build.py',
            ROOT / 'serving/src/horseracing_serving/__init__.py',
            ROOT / 'serving/src/horseracing_serving/model_loader.py']


def source_hash():
    return s.p.stable_hash({'build_files': {str(p): digest(p) for p in build_source_paths()},
                            '125_source': research.source_hash()})


def runtime():
    return {'python': sys.version, 'packages': {name: importlib.metadata.version(name) for name in
            ('numpy', 'pandas', 'lightgbm', 'scikit-learn', 'scipy', 'SQLAlchemy', 'joblib', 'threadpoolctl')}}


def upstream_state():
    """Read and verify the complete old research chain, including its final audit."""
    _, old = research.verify()
    s.verify(); s.verify_historical_certificate()
    root = research.SPEC
    final_path = root / 'evidence/final-integrity.json'
    final = read_json(final_path)
    if (final.get('status') != 'PASS' or final.get('source_hash') != old['source_hash']
        or final.get('config_hash') != old['config_hash'] or final.get('can_adopt') is not False
        or final.get('eligible_for_verdict') is not False
        or final.get('preferred_research_configuration') != 'new_joint_mixed6'):
        raise ValueError('125 final research integrity is not the retained source')
    files = dict(final['input_hashes']) | dict(final['documentation_hashes'])
    for path, sha in files.items():
        if digest(path) != sha: raise ValueError(f'125 final dependency changed: {path}')
    audit = read_json(root / 'evidence/independent-review.json')
    if (audit.get('status') != 'PASS' or audit.get('can_adopt') is not False
        or audit.get('eligible_for_verdict') is not False or audit.get('additional_fits') != 0
        or audit.get('method_sha256') != digest(root / 'evidence/independent-review.py')
        or audit.get('run_freeze_sha256') != digest(research.WORK / 'run-freeze.json')
        or audit.get('summary_sha256') != digest(root / 'verdict.json')
        or audit.get('mixture_retention') != 'RETAIN'):
        raise ValueError('125 independent audit binding changed')
    hashes = {}
    for contrast in old['comparisons']:
        if not research.verified_result(contrast, research.load_config(), old):
            raise ValueError('Missing verified125 result')
        path = research.result_path(contrast); report = read_json(path)
        hashes[contrast['id']] = {'report_sha256': digest(path), 'evidence_sha256': digest(report['evidence_path'])}
    if hashes != audit['report_hashes']:
        raise ValueError('125 audit report/evidence hash mismatch')
    files[str(final_path)] = digest(final_path)
    for path in (s.p.WORK / 'snapshot.pkl', s.p.WORK / 'source-frames.pkl', s.p.WORK / 'run-freeze.json',
                 s.p.MODEL / 'metadata.json', s.p.MODEL / 'model.txt'):
        files[str(path)] = digest(path)
    return {'files': files, 'research125_freeze_sha256': digest(research.WORK / 'run-freeze.json'),
            'snapshot_sha256': digest(s.p.WORK / 'snapshot.pkl'),
            'source_frames_sha256': digest(s.p.WORK / 'source-frames.pkl')}


def make_recipe(member, smoke=False):
    if member != member_for(member.get('id')): raise ValueError('Member identity changed')
    cfg = deepcopy(s.load_config()); cfg['arms']['seed'] = member['seed']
    return s.p.make_recipe(cfg, s.arm(cfg, member['branch'])['drop_features'], smoke)


def expected_columns(member):
    recipe = make_recipe(member)
    cols = s.p.columns_from_model() + s.p.OBSERVATION_COLUMNS
    return [c for c in cols if c not in recipe.drop_features]


def recipe_metadata(member, smoke=False):
    from horseracing_training.calib_split import CalibSplitFactory
    f = CalibSplitFactory(None, make_recipe(member, smoke), n_oof_blocks=2 if smoke else 8,
                         method='isotonic', require_sufficient=True)
    return clean(f.recipe_meta)


def inputs(smoke=False):
    matrix, races = s.reuse.load(s.p.WORK / 'snapshot.pkl')
    races = sorted([r for r in races if r.context.race_date <= CUTOFF],
                   key=lambda r: (r.context.race_date, r.context.race_id))
    if smoke:
        train = [r for r in races if r.context.race_date < dt.date(2008, 1, 1)]
        probes = [r for r in races if dt.date(2008, 1, 1) <= r.context.race_date <= dt.date(2008, 1, 31)]
    else:
        train = races
        probes = select_probes(races)
    if not train or not probes: raise ValueError('Empty training or serialization probe population')
    return matrix, train, probes


def select_probes(races):
    """First/middle/last per historical year, determined without predictions/outcomes."""
    selected = []
    for year in sorted({r.context.race_date.year for r in races}):
        rows = [r for r in races if r.context.race_date.year == year]
        selected.extend(rows[i] for i in sorted({0, len(rows) // 2, len(rows) - 1}))
    return selected


def population(matrix, races):
    if matrix.feature_cols != s.p.columns_from_model() + s.p.OBSERVATION_COLUMNS:
        raise ValueError('Original141 column order changed')
    frame = matrix.frame
    if frame.duplicated(['race_id', 'horse_id']).any(): raise ValueError('Duplicate matrix horse')
    ids = [r.context.race_id for r in races]
    if not ids or len(ids) != len(set(ids)): raise ValueError('Empty or duplicate train race')
    days = [r.context.race_date for r in races]
    if min(days) < dt.date(2007, 1, 1) or max(days) > CUTOFF: raise ValueError('Training dates outside frozen scope')
    if any(r.n_result_rows is None for r in races): raise ValueError('Frozen result counts missing')
    grouped = frame[['race_id', 'horse_id', 'race_date']].groupby('race_id', sort=False)
    for er in races:
        ctx = er.context
        if ctx.race_id not in grouped.indices: raise ValueError('Train race absent from matrix')
        rows = grouped.get_group(ctx.race_id)
        started = [h.horse_id for h in ctx.started_horses]
        if (not started or len(started) != len(set(started)) or set(rows.horse_id) != set(started)
            or not (rows.race_date == ctx.race_date).all()):
            raise ValueError('Matrix and full started horse/date population differ')
    data = frame[frame.race_id.isin(ids)]
    numeric = data[matrix.feature_cols].select_dtypes(include='number')
    if np.isinf(numeric.to_numpy(dtype=float)).any(): raise ValueError('Infinite model input')
    extra = frame[~frame.race_id.isin(ids)]
    extras = [{'race_id': str(rid), 'race_date': str(group.race_date.iloc[0]), 'rows': len(group)}
              for rid, group in extra.groupby('race_id', sort=True)]
    return {'n_train_races': len(races), 'n_train_rows': len(data), 'train_from': str(min(days)),
            'train_through': str(max(days)), 'train_hash': s.train_identity([r.context for r in races]),
            'matrix_train_rows_hash': hashlib.sha256(pd.util.hash_pandas_object(data, index=True).values.tobytes()).hexdigest(),
            'outcome_hash': s.p.stable_hash([(r.context.race_id, r.n_result_rows,
                sorted(l.horse_id for l in r.labels if l.win == 1)) for r in races]),
            'dtypes': {c: str(frame[c].dtype) for c in matrix.feature_cols},
            'categories': {c: list(frame[c].cat.categories) for c in matrix.categorical_cols},
            'categorical_cols': matrix.categorical_cols,
            'snapshot_only_rows': len(extra), 'snapshot_only_races': extras,
            'snapshot_only_note': 'Matrix rows outside this explicit EvalRace training population are not fitted.'}


def partition_hash(races, n_blocks):
    days = sorted({r.context.race_date for r in races})
    parts = list(day_block_partition(days, n_blocks))
    if len(parts) != n_blocks - 1 or any(not a or not b or max(a) >= min(b) for a, b in parts):
        raise ValueError('OOF strict-past block partition invalid')
    return s.p.stable_hash(clean(parts))


def freeze_path(): return WORK / 'run-freeze.json'
def member_dir(name, smoke=False):
    member_for(name)
    return WORK / ('smoke/members' if smoke else 'members') / name
def stage_dir(name, smoke=False): return member_dir(name, smoke).with_name(name + '.partial')
def receipt_path(name, smoke=False):
    member_for(name)
    return WORK / ('smoke/receipts' if smoke else 'receipts') / (name + '.json')


def prepare():
    if freeze_path().exists(): raise FileExistsError('Existing129 freeze; use verify-members, never overwrite')
    check_artifact_root(WORK.resolve())
    cfg, before, versions = build_config(), source_hash(), runtime()
    sources = upstream_state()
    matrix, train, probes = inputs()
    pop = population(matrix, train)
    if pop['train_through'] != cfg['train_through']: raise ValueError('Actual cutoff differs')
    jobs = [{'member': member, 'recipe': recipe_metadata(member), 'feature_cols': expected_columns(member),
             'train_hash': pop['train_hash']} for member in MEMBERS]
    frozen = {'schema_version': 1, 'artifact_kind': 'candidate_mixture_build_freeze', 'config': cfg,
              'config_hash': s.p.stable_hash(cfg), 'source_hash': before, 'runtime': versions,
              'sources': sources, 'population': pop, 'jobs': jobs,
              'oof_partition_hash': partition_hash(train, 8),
              'probe_race_ids': [r.context.race_id for r in probes],
              'source_feature_version': read_json(s.p.MODEL / 'metadata.json')['feature_version'],
              'prepared_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'can_adopt': False, 'eligible_for_verdict': False, 'db_registration': False}
    del matrix, train, probes; gc.collect()
    if source_hash() != before or runtime() != versions or upstream_state() != sources:
        raise ValueError('Build dependencies changed during preparation')
    write_json(freeze_path(), frozen)
    print('PREPARE PASS: six final fits registered; no DB write or fitting', flush=True)


def verify():
    cfg = build_config(); frozen = read_json(freeze_path())
    if (frozen.get('artifact_kind') != 'candidate_mixture_build_freeze' or frozen.get('config') != cfg
        or frozen.get('config_hash') != s.p.stable_hash(cfg) or frozen.get('source_hash') != source_hash()
        or frozen.get('runtime') != runtime() or frozen.get('sources') != upstream_state()
        or frozen.get('can_adopt') is not False or frozen.get('eligible_for_verdict') is not False
        or frozen.get('db_registration') is not False):
        raise ValueError('Frozen build/source/runtime/input changed')
    expected = [{'member': m, 'recipe': recipe_metadata(m), 'feature_cols': expected_columns(m),
                 'train_hash': frozen['population']['train_hash']} for m in MEMBERS]
    if frozen['jobs'] != expected: raise ValueError('Frozen six jobs changed')
    return cfg, frozen


@contextmanager
def frozen_outcomes(races):
    outcomes = {r.context.race_id: (r.n_result_rows, {l.horse_id for l in r.labels if l.win == 1}) for r in races}
    def lookup(session, ids):
        if session is not None or any(rid not in outcomes for rid in ids):
            raise ValueError('OOF outcome request is outside frozen inputs')
        return {rid: outcomes[rid] for rid in ids}
    with patch('horseracing_training.calib_split._started_all_outcomes', side_effect=lookup):
        yield


def prediction_array(ids, predictions):
    if list(predictions) != list(ids) or len(ids) != len(set(ids)) or not ids:
        raise ValueError('Prediction started set/order differs')
    values = np.array([[v.win, v.top2, v.top3] for v in predictions.values()], dtype=float)
    if (not np.isfinite(values).all() or (values < 0).any() or (values > 1 + 1e-10).any()
        or (np.diff(values, axis=1) < -1e-10).any()
        or not np.allclose(values.sum(axis=0), [min(k, len(ids)) for k in (1, 2, 3)], rtol=0, atol=1e-8)):
        raise ValueError('Prediction probability constraints failed')
    return values


def validate_fit(info, oof, member, pop, smoke=False):
    recipe = make_recipe(member, smoke)
    protocol = info.get('calibration_protocol') or {}
    if (info.get('feature_cols') != expected_columns(member) or info.get('seed') != member['seed']
        or info.get('objective') != 'pl_topk' or info.get('postprocess') != 'group_softmax'
        or info.get('params') != recipe.resolved_params()
        or info.get('race_class_representation') != 'raw' or info.get('model_degenerate') is not False
        or info.get('calibrator_degenerate') is not False or oof.get('sufficient') is not True
        or oof.get('calibrator_degenerate') is not False
        or info.get('calibration') != 'isotonic_strict_past_oof'
        or info.get('calibration_split_unit') is not None or info.get('calib_from') is not None
        or info.get('calib_through') is not None or info.get('calib_frac') != 0.
        or info.get('n_train_rows') != pop['n_train_rows'] or info.get('n_model_rows') != pop['n_train_rows']
        or str(info.get('train_through')) != pop['train_through']
        or str(info.get('model_fit_through')) != pop['train_through']
        or info.get('target_encode_cols') != list(recipe.target_encode_cols)
        or info.get('te_smoothing') != recipe.te_smoothing
        or protocol.get('protocol') != 'strict_past_oof_isotonic_v1'
        or protocol.get('n_oof_blocks') != (2 if smoke else 8)
        or protocol.get('score_space') != 'raw_race_softmax'
        or protocol.get('booster_calib_frac') != 0.
        or info.get('weight_mask') != {'rate': .5, 'seed': 20260810, 'unit': 'race',
            'columns': ['weight', 'weight_diff', 'carried_weight_ratio']}):
        raise ValueError('Actual fitted recipe/columns/OOF/population differs')
    if info.get('categorical_cols') != [c for c in pop['categorical_cols']
                                      if c not in recipe.target_encode_cols and c not in recipe.drop_features]:
        raise ValueError('Actual categorical columns differ')
    if (info.get('n_calib_rows') != oof.get('n_oof_rows') or protocol.get('n_oof_rows') != oof.get('n_oof_rows')
        or oof.get('n_oof_rows', 0) < 2000 or oof.get('n_oof_races', 0) < 200
        or oof.get('n_positives', 0) < 200 or oof.get('n_distinct_scores', 0) < 2):
        raise ValueError('Insufficient or contradictory OOF sample')
    for name in ('score_min', 'score_max'):
        if not np.isfinite(oof.get(name, np.nan)): raise ValueError('Non-finite OOF score')
    for name in ('n_oof_rows', 'n_oof_races', 'n_positives', 'n_distinct_scores', 'oof_pred_from', 'oof_pred_through'):
        if protocol.get(name) != oof.get(name): raise ValueError('OOF protocol detail differs')


def load_member_artifact(path, expected_metadata=None):
    """File-only loader for the exact registered125/138 raw profile; never global compat."""
    path = Path(path)
    if path.is_symlink() or any((path / n).is_symlink() for n in FILES):
        raise ValueError('Member symlink is not allowed')
    meta = read_json(path / 'metadata.json')
    if expected_metadata is not None and meta != expected_metadata: raise ValueError('Metadata differs')
    member = member_for(meta.get('member_id'))
    if (meta.get('branch') != member['branch'] or meta.get('seed') != member['seed']
        or meta.get('artifact_kind') != 'candidate_mixture_member' or meta.get('mode') != 'shadow'
        or meta.get('feature_cols') != expected_columns(member)
        or meta.get('feature_hash') != feature_hash(expected_columns(member))
        or meta.get('race_class_representation') != 'raw' or meta.get('can_adopt') is not False
        or meta.get('eligible_for_verdict') is not False or meta.get('db_registration') is not False):
        raise ValueError('Invalid member profile or schema')
    with (path / 'preprocessor.pkl').open('rb') as fh: prep = pickle.load(fh)
    with (path / 'calibrator.pkl').open('rb') as fh: calib = pickle.load(fh)
    if (prep.get('feature_cols') != meta['feature_cols'] or prep.get('feature_hash') != meta['feature_hash']
        or prep.get('categorical_cols') != meta['categorical_cols']
        or prep.get('feature_version') != meta['feature_version']
        or prep.get('race_class_representation') != 'raw' or prep.get('objective') != 'pl_topk'
        or prep.get('postprocess') != 'group_softmax'
        or prep.get('target_encode_cols') != meta['fit_info']['target_encode_cols']
        or set(prep.get('encoders', {})) != set(meta['fit_info']['target_encode_cols'])
        or prep.get('te_smoothing') != 10. or getattr(calib, 'identity', True)
        or getattr(calib, 'method', None) != 'isotonic' or getattr(calib, 'clip', None) != 1e-6
        or getattr(calib, '_iso', None) is None):
        raise ValueError('Preprocessor/calibrator profile mismatch')
    params = calib.params_dict()
    x, y = np.asarray(params['x']), np.asarray(params['y'])
    checksum = hashlib.sha256(json.dumps(params, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    if (x.ndim != 1 or x.shape != y.shape or len(x) < 2 or not np.isfinite(x).all()
        or not np.isfinite(y).all() or (np.diff(x) <= 0).any() or (np.diff(y) < 0).any()
        or (y < 0).any() or (y > 1).any()
        or checksum != meta['fit_info']['calibration_protocol']['threshold_checksum']):
        raise ValueError('Fitted calibrator threshold integrity failed')
    booster = lgb.Booster(model_file=str(path / 'model.txt'))
    if (booster.feature_name() != meta['feature_cols'] or booster.num_trees() != meta['actual_params']['n_estimators']
        or '[num_threads: 1]' not in (path / 'model.txt').read_text()):
        raise ValueError('Actual booster columns/tree count/thread count differs')
    vocab = categorical_vocab_from_booster(booster, meta['feature_cols'], meta['categorical_cols'])
    if vocab != meta['categorical_vocab'] or vocab_hash(vocab) != meta['categorical_vocab_hash']:
        raise ValueError('Booster categorical vocabulary differs')
    return ServingModel(member['id'], booster, 0., calib, meta['feature_cols'], meta['categorical_cols'],
                        encoders=prep['encoders'], feature_version=meta['feature_version'], feature_hash=meta['feature_hash'],
                        race_class_representation='raw', categorical_vocab=vocab, objective='pl_topk', metadata=meta)


def predict_loaded(model, context, rows):
    ids = [h.horse_id for h in context.started_horses]
    if set(rows.horse_id) != set(ids) or rows.horse_id.duplicated().any(): raise ValueError('Probe horse rows differ')
    data = rows.set_index('horse_id').reindex(ids)
    X = data[model.feature_cols].copy()
    encoded = {col: enc.transform(data[col]) for col, enc in model.encoders.items()}
    X = apply_encoded_columns(X, encoded, model.feature_cols)
    raw = model.raw_predict(X)
    if not np.isfinite(raw).all(): raise ValueError('Non-finite reloaded raw probability')
    return assemble_predictions(ids, model.calibrator.transform(raw), eps=1e-6)


def serialization_parity(predictor, loaded, matrix, probes):
    ids = {r.context.race_id for r in probes}
    frame = matrix.frame[matrix.frame.race_id.isin(ids)].copy()
    original = predictor._data
    predictor._data = replace(original, frame=frame)
    values = []; count = 0
    try:
        for er in probes:
            ctx = er.context; started = [h.horse_id for h in ctx.started_horses]
            before = prediction_array(started, predictor.predict_race(ctx))
            after = prediction_array(started, predict_loaded(loaded, ctx, frame[frame.race_id == ctx.race_id]))
            if not np.array_equal(before, after): raise ValueError('Non-exact serialized prediction roundtrip')
            count += before.size; values.append([ctx.race_id, started, before.tolist()])
    finally:
        predictor._data = original
    return {'status': 'PASS', 'scope': 'frozen raw inputs; serialization only', 'n_races': len(probes),
            'n_head_values': count, 'max_abs_diff': 0., 'prediction_hash': s.p.stable_hash(values),
            'race_ids': [r.context.race_id for r in probes]}


def completed(member, cfg, frozen, smoke=False):
    name = member_for(member['id'])['id']
    path, receipt, stage = member_dir(name, smoke), receipt_path(name, smoke), stage_dir(name, smoke)
    if not any(p.exists() for p in (path, receipt, stage)): return False
    if stage.exists() or not path.is_dir() or not receipt.is_file():
        raise ValueError(f'Incomplete member {name}; preserve files and diagnose before retry')
    r = read_json(receipt)
    if (r.get('member') != member or r.get('smoke') is not smoke or r.get('source_hash') != frozen['source_hash']
        or r.get('run_freeze_sha256') != digest(freeze_path()) or r.get('runtime') != frozen['runtime']
        or r.get('can_adopt') is not False or r.get('eligible_for_verdict') is not False
        or r.get('db_registration') is not False or r.get('model_threads') != 1
        or set(r.get('files', {})) != set(FILES)):
        raise ValueError('Completion receipt identity changed')
    if set(p.name for p in path.iterdir()) != set(FILES): raise ValueError('Unexpected member artifacts')
    for filename, sha in r['files'].items():
        if digest(path / filename) != sha: raise ValueError('Completed artifact SHA changed')
    meta = read_json(path / 'metadata.json')
    if (meta.get('source_hash') != frozen['source_hash'] or meta.get('runtime') != frozen['runtime']
        or meta.get('run_freeze_sha256') != r['run_freeze_sha256'] or meta.get('smoke') is not smoke
        or meta.get('recipe') != recipe_metadata(member, smoke) or meta.get('population') != r.get('population')
        or meta.get('actual_params') != make_recipe(member, smoke).resolved_params()
        or meta.get('coefficient_train_through') != '2025-12-31'):
        raise ValueError('Completed metadata provenance differs')
    if not smoke and (r['population'] != frozen['population'] or meta['oof_partition_hash'] != frozen['oof_partition_hash']):
        raise ValueError('Completed final training population differs')
    validate_fit(meta['fit_info'], meta['oof_info'], member, r['population'], smoke)
    parity = r.get('parity', {})
    if (parity.get('status') != 'PASS' or parity.get('max_abs_diff') != 0. or parity.get('n_head_values', 0) <= 0
        or (not smoke and parity.get('race_ids') != frozen['probe_race_ids'])):
        raise ValueError('Missing complete serialization parity')
    if not np.isfinite(r.get('fit_seconds', np.nan)) or r['fit_seconds'] < 0:
        raise ValueError('Invalid fit timer')
    load_member_artifact(path, meta)
    return True


def assert_no_other_training():
    locks = [p for p in (ROOT / 'artifacts').glob('*/running.lock') if p.parent != WORK]
    if locks: raise ValueError(f'Other research training is active: {locks}')


def check_workers(workers):
    if type(workers) is not int or workers not in (1, 2): raise ValueError('Only one or two workers allowed')


def fit_member(name, smoke=False):
    assert_no_other_training()
    cfg, frozen = verify(); member = member_for(name)
    if completed(member, cfg, frozen, smoke): return
    launch = read_json(WORK / 'running.lock')
    if (launch.get('source_hash') != frozen['source_hash'] or launch.get('smoke') is not smoke
        or member not in launch.get('members', [])):
        raise ValueError('Worker is not part of the registered parent launch')
    check_workers(launch['workers'])
    os.kill(launch['pid'], 0)  # A stale parent lock never authorizes a detached fit.
    if not smoke and not all(completed(m, cfg, frozen, True) for m in MEMBERS):
        raise ValueError('All six smoke receipts required before full fit')
    matrix, races, probes = inputs(smoke)
    pop = population(matrix, races)
    ph = partition_hash(races, 2 if smoke else 8)
    if not smoke and (pop != frozen['population'] or ph != frozen['oof_partition_hash']
                      or [r.context.race_id for r in probes] != frozen['probe_race_ids']):
        raise ValueError('Actual training inputs differ from prepare')
    stage = stage_dir(name, smoke); stage.mkdir(parents=True, exist_ok=False)
    t0 = time.monotonic()
    predictor = OofCalibratedPredictor(None, make_recipe(member, smoke), shared_data=matrix,
        n_oof_blocks=2 if smoke else 8, method='isotonic', require_sufficient=True)
    # Any failure leaves the exclusive .partial directory for explicit diagnosis.
    with frozen_outcomes(races): predictor.fit([r.context for r in races], num_threads=1)
    elapsed = time.monotonic() - t0
    base = predictor.to_servable()
    info, oof = clean(base.fit_info_), clean(predictor.oof_info_)
    validate_fit(info, oof, member, pop, smoke)
    _write_model(base, stage / 'model.txt')
    prep = build_preprocessor(base, frozen['source_feature_version'])
    for file, obj in [('calibrator.pkl', base.calibrator_), ('preprocessor.pkl', prep)]:
        with (stage / file).open('xb') as fh:
            pickle.dump(obj, fh, protocol=5); fh.flush(); os.fsync(fh.fileno())
    booster = base.win_model_.booster_
    vocab = categorical_vocab_from_booster(booster, info['feature_cols'], info['categorical_cols'])
    meta = {'schema_version': 1, 'artifact_kind': 'candidate_mixture_member', 'mode': 'shadow',
            'member_id': name, **{k: member[k] for k in ('branch', 'seed')}, 'smoke': smoke,
            'feature_cols': info['feature_cols'], 'categorical_cols': info['categorical_cols'],
            'feature_hash': feature_hash(info['feature_cols']), 'feature_version': frozen['source_feature_version'],
            'race_class_representation': 'raw', 'categorical_vocab': vocab, 'categorical_vocab_hash': vocab_hash(vocab),
            'objective': 'pl_topk', 'postprocess': 'group_softmax', 'recipe': recipe_metadata(member, smoke),
            'actual_params': info['params'], 'oof_info': oof, 'fit_info': info, 'population': pop,
            'train_through': pop['train_through'], 'coefficient_train_through': '2025-12-31',
            'oof_partition_hash': ph, 'source_hash': frozen['source_hash'], 'runtime': frozen['runtime'],
            'sources': frozen['sources'], 'run_freeze_sha256': digest(freeze_path()),
            'can_adopt': False, 'eligible_for_verdict': False, 'db_registration': False}
    write_json(stage / 'metadata.json', meta)
    loaded = load_member_artifact(stage, clean(meta))
    parity = serialization_parity(base, loaded, matrix, probes)
    del loaded, predictor, base, matrix, races, probes; gc.collect()
    verify()
    files = {file: digest(stage / file) for file in FILES}
    final = member_dir(name, smoke)
    if final.exists() or receipt_path(name, smoke).exists(): raise FileExistsError('Concurrent member output')
    stage.rename(final)
    write_json(receipt_path(name, smoke), {'artifact_kind': 'candidate_mixture_member_receipt',
        'member': member, 'smoke': smoke, 'files': files, 'population': pop, 'parity': parity,
        'source_hash': frozen['source_hash'], 'runtime': frozen['runtime'], 'run_freeze_sha256': digest(freeze_path()),
        'fit_seconds': elapsed, 'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'model_threads': 1, 'booster_fits': 2 if smoke else 8, 'can_adopt': False,
        'eligible_for_verdict': False, 'db_registration': False,
        'completed_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    completed(member, cfg, frozen, smoke)
    print(f'COMPLETE {name} smoke={smoke} fit_seconds={elapsed:.3f}', flush=True)


def run_jobs(workers=2, smoke=False):
    check_workers(workers); assert_no_other_training()
    cfg, frozen = verify()
    # Check every existing output before scheduling any new fit.
    pending = [m for m in MEMBERS if not completed(m, cfg, frozen, smoke)]
    if not smoke:
        if not all(completed(m, cfg, frozen, True) for m in MEMBERS): raise ValueError('All six smoke receipts required')
    lock = WORK / 'running.lock'
    write_json(lock, {'pid': os.getpid(), 'workers': workers, 'smoke': smoke,
                      'source_hash': frozen['source_hash'], 'members': pending})
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    logs = WORK / 'logs' / stamp; logs.mkdir(parents=True, exist_ok=False)
    def launch(m):
        command = [sys.executable, str(Path(__file__).resolve()), 'worker', '--member', m['id']]
        if smoke: command.append('--smoke')
        with (logs / (m['id'] + '.log')).open('x') as fh:
            result = subprocess.run(command, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                env={**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'})
        if result.returncode: raise RuntimeError(f'Member failed; preserve files and inspect {logs / (m["id"] + ".log")}')
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            queue = iter(pending)
            running = {pool.submit(launch, m) for m in [next(queue, None) for _ in range(workers)] if m is not None}
            while running:
                done, running = wait(running, return_when=FIRST_COMPLETED)
                # Inspect every finished job before scheduling another. On failure the
                # other already-running member may finish, but no new fit is launched.
                for future in done: future.result()
                for _ in done:
                    member = next(queue, None)
                    if member is not None: running.add(pool.submit(launch, member))
    finally:
        lock.unlink()
    verify_members(smoke)


def verify_members(smoke=False):
    cfg, frozen = verify()
    result = []
    for member in MEMBERS:
        if not completed(member, cfg, frozen, smoke): raise ValueError(f'Missing member: {member["id"]}')
        r = read_json(receipt_path(member['id'], smoke))
        result.append({'member': member, 'artifact_dir': str(member_dir(member['id'], smoke)),
                       'files': r['files'], 'receipt_sha256': digest(receipt_path(member['id'], smoke)),
                       'parity': r['parity']})
    verify()
    print(f'VERIFY MEMBERS PASS smoke={smoke}: six complete file-only artifacts', flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'smoke', 'train', 'verify-members', 'worker'])
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--member', choices=[m['id'] for m in MEMBERS])
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare': prepare()
    elif args.command == 'smoke': run_jobs(args.workers, True)
    elif args.command == 'train': run_jobs(args.workers, False)
    elif args.command == 'verify-members': verify_members(args.smoke)
    else:
        if args.member is None: parser.error('worker requires --member')
        fit_member(args.member, args.smoke)


if __name__ == '__main__': main()
