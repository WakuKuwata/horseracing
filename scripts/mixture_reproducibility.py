"""135 fixed colsample reproducibility, DB-free annual fits and portable artifacts.

prepare never fits. fit/smoke/recent-fit are explicit commands. recent-fit only
uses the sealed <=2026-08-23 training matrix; it does not load recent race inputs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import datetime as dt
import gc
import importlib.metadata
import os
from pathlib import Path
import pickle
import resource
import sys
import time
from unittest.mock import patch

import lightgbm as lgb
import numpy as np
import pandas as pd

import accuracy_batch_common as common
import market_feature_full_quality as native
from horseracing_eval.splits import expanding_folds
from horseracing_features.weight_mask import MaskSpec, apply_weight_mask
from horseracing_training.artifacts import (
    _write_model, build_preprocessor, categorical_vocab_from_booster, feature_hash, vocab_hash,
)
from horseracing_training.calib_split import CalibSplitFactory, OofCalibratedPredictor, day_block_partition
from horseracing_training.predictor import assemble_predictions
from horseracing_training.target_encoding import apply_encoded_columns
from horseracing_training.win_model import WinModel

ROOT = common.ROOT
SPEC = ROOT / 'specs/135-mixture-reproducibility'
WORK = ROOT / 'artifacts/135-mixture-reproducibility/repro'
SOURCE111 = ROOT / 'artifacts/111-ability-observation'
SOURCE125 = ROOT / 'artifacts/125-joint-residual-stack'
SOURCE132 = ROOT / 'artifacts/132-accuracy-first-batch'
END = dt.date(2026, 8, 23)
CUTOFF = dt.date(2026, 9, 6)
SEEDS, YEARS = (42, 43, 44), (2024, 2026)
REGIMES = {'full': None, 'preweight': MaskSpec(rate=1., seed=20260810)}
MODEL_FILES = ('model.txt', 'calibrator.pkl', 'preprocessor.pkl', 'metadata.json')
digest, stable_hash, read_json, write_json = common.digest, common.stable_hash, common.read_json, common.write_json


def canonical(value):
    import json
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def dump(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as out:
        pickle.dump(value, out, protocol=5); out.flush(); os.fsync(out.fileno())


def load(path):
    with Path(path).open('rb') as source:
        return pickle.load(source)


def config():
    cfg = read_json(SPEC / 'experiment.json')
    r = cfg['repro']
    expected = {'seeds': list(SEEDS), 'years': list(YEARS), 'regimes': list(REGIMES),
        'primary_regime': 'preweight', 'feature_count': 125, 'colsample_bytree': .7,
        'n_estimators': 900, 'n_oof_blocks': 8, 'te_smoothing': 10, 'weight_mask_rate': .5,
        'weight_mask_seed': 20260810, 'candidate_weight': [1, 7],
        'correction': '125 same-seed same-evaluation-year joint coefficients; recompute centered_logp on candidate',
        'topk': 'same annual/regime baseline lambda from132 for both arms',
        'seed_aggregation': 'mean of per-race loss differences; not mean prediction ensemble'}
    if (r != expected or cfg['cutoff'] != str(CUTOFF) or cfg['historical_source_end'] != str(END)
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False
        or cfg['resources']['max_workers'] != 2 or cfg['resources']['num_threads'] != 1
        or cfg['bootstrap'] != {'b': 4000, 'seed': 20260907, 'alpha': .0125}
        or cfg['quality'] != {'top2_logloss_margin': .0005, 'top3_logloss_margin': .0005,
                             'ece_margin': .001, 'ece_ceiling': .05}
        or cfg['recent']['train_through'] != str(END) or cfg['recent']['candidate_seed'] != 42):
        raise ValueError('Registered135 experiment changed')
    return cfg


def recipe(seed, smoke=False):
    if seed not in SEEDS:
        raise ValueError('Unregistered training seed')
    # Keep the exact native126 recipe, including drops for absent122 additions.
    # Effective125 inputs are separately proved identical in prepare.
    return replace(native.m.make_recipe(native.m.load_config(), 'colsample_07', smoke), seed=seed)


def recipe_meta(seed, smoke=False):
    return canonical(CalibSplitFactory(None, recipe(seed, smoke),
        n_oof_blocks=2 if smoke else 8, method='isotonic', require_sufficient=True).recipe_meta)


def source_hash():
    paths = [Path(__file__), ROOT / 'scripts/tests/test_mixture_reproducibility.py',
             ROOT / 'scripts/accuracy_batch_common.py',
             ROOT / 'serving/src/horseracing_serving/mixture_correction.py',
             ROOT / 'serving/src/horseracing_serving/__init__.py']
    for package in ('db', 'features', 'training', 'eval', 'probability'):
        paths.extend((ROOT / package / 'src').rglob('*.py'))
    return stable_hash({'files': {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))},
                        'native126_source': native.source_hash()})


def runtime():
    return {'python': sys.version, 'packages': {n: importlib.metadata.version(n) for n in
        ('numpy', 'pandas', 'lightgbm', 'scikit-learn', 'scipy', 'SQLAlchemy', 'joblib', 'threadpoolctl')}}


def validate_matrix(matrix, races):
    dates = common.validate_dates(matrix.frame.race_date, where='135 historical matrix')
    if dates.max().date() != END or any(r.context.race_date > END for r in races):
        raise ValueError('135 historical input exceeds8/23')
    if matrix.frame.duplicated(['race_id', 'horse_id']).any():
        raise ValueError('Duplicate historical matrix identity')
    cols = [c for c in matrix.feature_cols if c not in recipe(42).drop_features]
    if len(cols) != 125:
        raise ValueError('Expected native125 feature scope')
    return cols


def train_identity(races):
    return native.s.train_identity([r.context for r in races])


def partition_proof(races, blocks=8):
    ordered = sorted(races, key=lambda r: (r.context.race_date, r.context.race_id))
    days = sorted({r.context.race_date for r in ordered})
    parts = []
    for earlier, block in day_block_partition(days, blocks):
        if earlier and block and max(earlier) >= min(block):
            raise ValueError('OOF partition is not strict-prior')
        eset, bset = set(earlier), set(block)
        parts.append({'train': train_identity([r for r in ordered if r.context.race_date in eset]),
            'valid': train_identity([r for r in ordered if r.context.race_date in bset]),
            'train_days': len(earlier), 'valid_days': len(block),
            'train_through': str(max(earlier)) if earlier else None,
            'valid_from': str(min(block)) if block else None})
    fits = 1 + sum(bool(p['train_days'] and p['valid_days']) for p in parts)
    return {'sha256': stable_hash(parts), 'blocks': blocks, 'booster_fits': fits, 'partitions': parts}


def folds_for(races):
    return {f.valid_year: f for f in expanding_folds(races, YEARS[0]) if f.valid_year in YEARS}


def validate_coefficients(coef, frozen125, seed):
    if (coef['training_seed'] != seed or coef['base_arm'] != 'pruning125_raw'
        or coef['base_recipe_hash'] != frozen125['base_recipe_hashes'][str(seed)]
        or coef['coefficient_order'] != common.mc.JOINT_TERMS
        or coef['selected'] != ['prior_gap', 'global_temperature']
        or coef['can_adopt'] is not False or coef['eligible_for_verdict'] is not False):
        raise ValueError('125 same-seed coefficient provenance differs')
    for year in YEARS:
        d = next(v for v in coef['fit_diagnostics'] if v['eval_year'] == year)
        beta = np.asarray(coef['gammas'][str(year)])
        if (dt.date.fromisoformat(d['fit_last_day']) >= dt.date(year, 1, 1)
            or dt.date.fromisoformat(d['eval_first_day']).year != year
            or beta.shape != (5,) or not np.isfinite(beta).all() or 1 + beta[-1] <= 0):
            raise ValueError('Coefficient dates or temperature invalid')


def stage_lambdas(report):
    out = {}
    for regime in REGIMES:
        out[regime] = {}
        for row in report['regimes'][regime]['annual_stage_fits']:
            if row['year'] not in YEARS:
                continue
            if (row['fit_through'] is None or dt.date.fromisoformat(row['fit_through']) >= dt.date(row['year'], 1, 1)
                or row['fallback'] or not all(np.isfinite(row[k]) and row[k] > 0 for k in ('lambda2', 'lambda3'))):
                raise ValueError('Stage lambda is not a sufficient prior-year fit')
            out[regime][str(row['year'])] = row
        if set(out[regime]) != {str(y) for y in YEARS}:
            raise ValueError('Annual/regime baseline lambda absent')
    return out


def mixed_records(inputs):
    """Two registered years only; same native130 member order and correction math."""
    frame = inputs.matrix.frame
    target = frame.loc[pd.to_datetime(frame.race_date).dt.year.isin(YEARS),
                       common.mc.KEYS + ['days_since_last', 'sex']]
    corr = common.mc.build_correction_inputs(target, frame[common.mc.KEYS])
    indexed = {rid: g.set_index('horse_id', drop=False) for rid, g in corr.groupby('race_id', sort=False)}
    records = {r: [] for r in REGIMES}
    for year in YEARS:
        races = [r for r in inputs.races if r.context.race_date.year == year]
        caches = {m: common._load_annual_cache(inputs, m, year, races) for m in common.MEMBER_IDS}
        for er in races:
            ids = tuple(h.horse_id for h in er.context.started_horses)
            c = indexed[er.context.race_id].loc[list(ids)].reset_index(drop=True)
            for regime in REGIMES:
                members = []
                for m in common.MEMBER_IDS:
                    raw = caches[m]['predictions'][regime][er.context.race_id]
                    terms = common.mc.JOINT_TERMS if m.startswith('joint') else ['gap_log']
                    p = common.mc.correct_member_predictions(ids, [raw[h][0] for h in ids], c, terms,
                                                            inputs.freeze130['coefficients'][m][str(year)])
                    members.append(prediction_array(p, ids))
                members = np.stack(members)
                records[regime].append(common.MixedRace(er, ids, members, members.mean(axis=0), c))
    return records


def prediction_array(predictions, ids):
    if tuple(predictions) != tuple(ids):
        raise ValueError('Started horse identity/order differs')
    return common.validate_heads([[v.win, v.top2, v.top3] for v in predictions.values()], len(ids))


def prepare():
    if (WORK / 'run-freeze.json').exists():
        raise FileExistsError('Freeze exists; use verify')
    started = time.monotonic()
    cfg, source, environment = config(), source_hash(), runtime()
    # Reject forbidden metadata BEFORE deserializing either historical matrix.
    meta111 = read_json(SOURCE111 / 'snapshot.json')
    if meta111['data_through'] != str(END) or meta111['snapshot_sha256'] != common.SNAPSHOT_SHA256:
        raise ValueError('Historical snapshot metadata differs')
    cfg126, frozen126 = native.verify()
    if cfg126['eval_window']['to'] != str(END):
        raise ValueError('Native126 date boundary differs')
    inputs = common.load_frozen_inputs()
    cols = validate_matrix(inputs.matrix, inputs.races)
    expanded, old_races = load(native.m.WORK / 'matrix.pkl')
    projection = native.source141(expanded)
    pd.testing.assert_frame_equal(projection.frame, inputs.matrix.frame, check_exact=True)
    if (projection.feature_cols != inputs.matrix.feature_cols or projection.categorical_cols != inputs.matrix.categorical_cols
        or projection.build_audit != inputs.matrix.build_audit or old_races != inputs.races
        or cols != frozen126['arm_columns']['colsample_07']):
        raise ValueError('Native126/111 effective input identity differs')
    del expanded, projection, old_races; gc.collect()
    folds = folds_for(inputs.races)
    jobs, native_caches = [], {}
    review126_path = native.SPEC / 'evidence/independent-review.json'
    review126 = read_json(review126_path)
    if (review126['status'] != 'PASS' or review126['run_freeze_sha256'] != digest(native.WORK / 'run-freeze.json')
        or review126['method_sha256'] != digest(review126_path.with_suffix('.py'))):
        raise ValueError('Native126 independent audit binding differs')
    for year in YEARS:
        fold = folds[year]
        oldjob = next(j for j in frozen126['jobs'] if j['year'] == year)
        if not native.completed(oldjob): raise ValueError('Native126 complete receipt missing')
        oldpath = native.WORK / 'cache' / f"{oldjob['key']}.pkl"
        if digest(oldpath) != review126['cache_hashes'][f'colsample_07:{year}']:
            raise ValueError('Native126 probability cache differs from independent audit')
        old = load(oldpath)
        if (oldjob['train_hash'] != train_identity(fold.train)
            or oldjob['feature_columns'] != cols or canonical(old['recipe_meta']) != recipe_meta(42)
            or old['actual_params'] != recipe(42).resolved_params()):
            raise ValueError('Native126 fold/recipe/effective columns differ')
        native_caches[str(year)] = {'path': str(oldpath), 'sha256': digest(oldpath),
                                   'receipt_path': str(native.receipt_path(oldjob['key']))}
        for seed in SEEDS:
            jobs.append({'seed': seed, 'year': year, 'train_hash': train_identity(fold.train),
                'valid_hash': train_identity(fold.valid), 'n_train': len(fold.train), 'n_valid': len(fold.valid),
                'train_through': str(max(r.context.race_date for r in fold.train)),
                'recipe': recipe_meta(seed), 'oof': partition_proof(fold.train)})
    files = dict(frozen126['sources']['files'])
    files.update({str(native.WORK / 'run-freeze.json'): digest(native.WORK / 'run-freeze.json'),
        str(native.m.WORK / 'matrix.pkl'): frozen126['matrix_sha256'],
        str(common.SNAPSHOT): common.SNAPSHOT_SHA256,
        str(SOURCE111 / 'snapshot.json'): digest(SOURCE111 / 'snapshot.json'),
        str(common.SOURCE / 'run-freeze.json'): digest(common.SOURCE / 'run-freeze.json')})
    for item in native_caches.values():
        files[item['path']] = item['sha256']
        files[item['receipt_path']] = digest(item['receipt_path'])
    files.update({str(review126_path): digest(review126_path),
                  str(review126_path.with_suffix('.py')): digest(review126_path.with_suffix('.py'))})
    frozen125 = read_json(SOURCE125 / 'run-freeze.json')
    files[str(SOURCE125 / 'run-freeze.json')] = digest(SOURCE125 / 'run-freeze.json')
    coefficients = {}
    for seed in SEEDS:
        p = SOURCE125 / f'seed-{seed}/coefficients.json'; rp = p.with_name('coefficients-receipt.json')
        c, receipt = read_json(p), read_json(rp)
        if receipt != {'sha256': digest(p), 'freeze_sha256': digest(SOURCE125 / 'run-freeze.json'), 'seed': seed}:
            raise ValueError('125 coefficient receipt differs')
        validate_coefficients(c, frozen125, seed)
        coefficients[str(seed)] = {str(y): c['gammas'][str(y)] for y in YEARS}
        files.update({str(p): digest(p), str(rp): digest(rp)})
    topk_path = SOURCE132 / 'topk/summary.json'; lambdas = stage_lambdas(read_json(topk_path))
    p132 = SOURCE132 / 'colsample/add_transferred_1of7-predictions.pkl'
    report132 = read_json(p132.with_name('add_transferred_1of7-report.json'))
    if report132['prediction_sha256'] != digest(p132):
        raise ValueError('132 prediction receipt differs')
    files.update({str(topk_path): digest(topk_path), str(p132): digest(p132),
                  str(p132.with_name('add_transferred_1of7-report.json')): digest(p132.with_name('add_transferred_1of7-report.json'))})
    records = mixed_records(inputs)
    files.update(inputs.provenance['cache_sha256'])
    for member in common.MEMBER_IDS:
        for year in YEARS:
            p = common.SOURCE / 'receipts' / f'{member}-{year}.json'; files[str(p)] = digest(p)
    old132 = load(p132)
    baseline_parity = {}
    for regime in REGIMES:
        records_r = records[regime]
        if len(records_r) != sum(len(folds[y].valid) for y in YEARS):
            raise ValueError('Two-year mixed6 population differs')
        scores = {}
        for year in YEARS:
            cohort = [r for r in records_r if r.year == year]
            scores[str(year)] = common.summarize_predictions(cohort, {r.race_id: r.heads for r in cohort})
            expected = read_json(topk_path)['regimes'][regime]['by_year'][str(year)]['arms']['head_mean']
            if scores[str(year)] != expected:
                raise ValueError('132 baseline mixed6 score parity failed')
        baseline_parity[regime] = scores
    final_train = [r for r in inputs.races if r.context.race_date <= END]
    recent = {'seed': 42, 'kind': 'recent-model-only', 'train_hash': train_identity(final_train),
              'train_through': str(END), 'n_train': len(final_train), 'oof': partition_proof(final_train),
              'recipe': recipe_meta(42)}
    dump(WORK / 'records.pkl', records)
    write_json(WORK / 'input-audit.json', {'effective125_exact': True, 'all111_columns_exact': True,
        'labels_categories_dtypes_exact': True, 'baseline_parity': baseline_parity,
        'native126_caches': native_caches, 'data_through': str(END), 'additional_fits': 0})
    for source_path, name in ((Path(__file__), 'execution-source.py'),
        (ROOT / 'scripts/tests/test_mixture_reproducibility.py', 'execution-tests.py')):
        with (WORK / name).open('xb') as target: target.write(source_path.read_bytes())
    if source_hash() != source or runtime() != environment:
        raise ValueError('Execution source/runtime changed during prepare')
    for p, sha in files.items():
        if digest(p) != sha: raise ValueError(f'Source changed during prepare: {p}')
    frozen = {'schema_version': 1, 'config': cfg, 'config_sha256': stable_hash(cfg),
        'source_hash': source, 'runtime': environment, 'inputs': files, 'feature_columns': cols,
        'feature_dtypes': {c: str(inputs.matrix.frame[c].dtype) for c in cols},
        'feature_version': meta111.get('feature_version', 'frozen111-raw'),
        'jobs': jobs, 'recent_job': recent, 'coefficients': coefficients, 'lambdas': lambdas,
        'native126_caches': native_caches, 'records_sha256': digest(WORK / 'records.pkl'),
        'input_audit_sha256': digest(WORK / 'input-audit.json'),
        'execution_source_sha256': digest(WORK / 'execution-source.py'),
        'execution_tests_sha256': digest(WORK / 'execution-tests.py'),
        'prepare_seconds': time.monotonic() - started, 'can_adopt': False, 'eligible_for_verdict': False}
    write_json(WORK / 'run-freeze.json', frozen)
    print(f'PREPARE PASS jobs={len(jobs)} additional_fits=0 seconds={frozen["prepare_seconds"]:.1f}', flush=True)


def verify():
    f = read_json(WORK / 'run-freeze.json')
    if (f['config'] != config() or f['config_sha256'] != stable_hash(config())
        or f['source_hash'] != source_hash() or f['runtime'] != runtime()):
        raise ValueError('135 source/config/runtime freeze changed')
    for p, sha in f['inputs'].items():
        if digest(p) != sha: raise ValueError(f'135 frozen input changed: {p}')
    for name, key in (('records.pkl', 'records_sha256'), ('input-audit.json', 'input_audit_sha256'),
                     ('execution-source.py', 'execution_source_sha256'), ('execution-tests.py', 'execution_tests_sha256')):
        if digest(WORK / name) != f[key]: raise ValueError(f'135 artifact changed: {name}')
    return f


@contextmanager
def frozen_outcomes(races):
    outcomes = {r.context.race_id: (r.n_result_rows, {l.horse_id for l in r.labels if l.win == 1}) for r in races}
    def lookup(session, ids):
        if session is not None or any(i not in outcomes for i in ids):
            raise ValueError('OOF outcome request outside explicit frozen training races')
        return {i: outcomes[i] for i in ids}
    with patch('horseracing_training.calib_split._started_all_outcomes', side_effect=lookup): yield


def make_predictor(matrix, seed, smoke=False):
    return OofCalibratedPredictor(None, recipe(seed, smoke), shared_data=matrix,
        n_oof_blocks=2 if smoke else 8, method='isotonic', require_sufficient=True)


@dataclass
class ResearchModel:
    model: WinModel
    calibrator: object
    encoders: dict
    metadata: dict

    def predict_encoded(self, context, X):
        """Explicit already-encoded125 rows; no TE transform, coercion or fallback.

        The caller must prove the encoding-domain conversion before using this
        entry point. Index must be exact started horse IDs in prediction order.
        """
        ids = tuple(h.horse_id for h in context.started_horses)
        if (not dt.date(2007, 1, 1) <= context.race_date <= CUTOFF or not ids
            or len(ids) != len(set(ids)) or not isinstance(X, pd.DataFrame)
            or tuple(X.index) != ids or list(X.columns) != self.metadata['feature_columns']):
            raise ValueError('Explicit encoded feature schema/order/date differs')
        categorical = set(self.metadata['categorical_columns'])
        for col in X:
            if (col in categorical and not isinstance(X[col].dtype, pd.CategoricalDtype)) or (
                col not in categorical and not pd.api.types.is_numeric_dtype(X[col].dtype)):
                raise ValueError('Encoded categorical/numeric dtype differs; implicit coercion prohibited')
            if col not in categorical and np.isinf(X[col].to_numpy(dtype=float, na_value=np.nan)).any():
                raise ValueError('Infinite encoded feature value')
        raw = self.model.predict(X, group_ids=[context.race_id] * len(ids))
        if np.asarray(raw).shape != (len(ids),) or not np.isfinite(raw).all():
            raise ValueError('Raw model output shape/finite invalid')
        calibrated = self.calibrator.transform(raw)
        if np.asarray(calibrated).shape != (len(ids),) or not np.isfinite(calibrated).all():
            raise ValueError('Calibrated model output shape/finite invalid')
        return prediction_array(assemble_predictions(list(ids), calibrated, eps=1e-6), ids)

    def predict(self, context, rows, regime='full'):
        if regime not in REGIMES:
            raise ValueError('Unknown prediction regime')
        ids = tuple(h.horse_id for h in context.started_horses)
        if (not ids or rows.race_id.nunique() != 1 or rows.race_id.iloc[0] != context.race_id
            or rows.horse_id.duplicated().any() or set(rows.horse_id) != set(ids)
            or not pd.to_datetime(rows.race_date).eq(pd.Timestamp(context.race_date)).all()):
            raise ValueError('Explicit feature rows must exactly cover one started race')
        rows = rows.copy()
        if REGIMES[regime] is not None: rows = apply_weight_mask(rows, spec=REGIMES[regime])
        rows = rows.set_index('horse_id').reindex(ids)
        cols = self.metadata['feature_columns']
        X = rows[cols].copy()
        X = apply_encoded_columns(X, {c: enc.transform(rows[c]) for c, enc in self.encoders.items()}, cols)
        X.index = rows.index
        return self.predict_encoded(context, X)


def load_model(path, files, *, expected=None):
    path = Path(path)
    if set(files) != set(MODEL_FILES) or path.is_symlink(): raise ValueError('Invalid model manifest')
    # Local owned pickle files only, SHA verified BEFORE unpickling.
    for name, sha in files.items():
        if (path / name).is_symlink() or digest(path / name) != sha:
            raise ValueError('Model file SHA or symlink invalid')
    meta = read_json(path / 'metadata.json')
    if expected is not None and meta != expected: raise ValueError('Model metadata differs')
    if (meta['artifact_kind'] != '135_colsample_research_model' or meta['session'] is not None
        or meta['can_adopt'] is not False or meta['eligible_for_verdict'] is not False
        or meta['seed'] not in SEEDS or meta['feature_count'] != 125 or len(meta['feature_columns']) != 125
        or meta['recipe'] != recipe_meta(meta['seed'], meta['smoke'])
        or meta['source_hash'] != source_hash() or meta['runtime'] != runtime()
        or dt.date.fromisoformat(meta['train_through']) > END):
        raise ValueError('Model frozen profile invalid')
    prep, calib = load(path / 'preprocessor.pkl'), load(path / 'calibrator.pkl')
    if (prep['feature_cols'] != meta['feature_columns'] or prep['feature_hash'] != feature_hash(meta['feature_columns'])
        or prep['categorical_cols'] != meta['categorical_columns'] or prep['te_smoothing'] != 10.
        or set(prep['encoders']) != {'jockey_id', 'trainer_id'}
        or prep['objective'] != 'pl_topk' or prep['race_class_representation'] != 'raw'
        or getattr(calib, 'method', None) != 'isotonic' or getattr(calib, 'identity', True)
        or stable_hash(calib.params_dict()) != meta['calibrator_sha256']):
        raise ValueError('TE/calibrator schema differs')
    booster = lgb.Booster(model_file=str(path / 'model.txt'))
    vocab = categorical_vocab_from_booster(booster, meta['feature_columns'], meta['categorical_columns'])
    if (booster.feature_name() != meta['feature_columns'] or booster.num_trees() != meta['actual_params']['n_estimators']
        or '[num_threads: 1]' not in (path / 'model.txt').read_text()
        or vocab_hash(vocab) != meta['categorical_vocab_sha256']):
        raise ValueError('Fitted booster/vocabulary differs')
    model = WinModel(seed=meta['seed'], params=meta['actual_params'], objective='pl_topk')
    model.booster_, model.feature_cols_ = booster, meta['feature_columns']
    return ResearchModel(model, calib, prep['encoders'], meta)


def save_model(base, path, job, frozen, smoke):
    path.mkdir(parents=True, exist_ok=False)
    if base.session is not None: raise ValueError('Research fit retained a DB session')
    info = canonical(base.fit_info_)
    if (info['feature_cols'] != frozen['feature_columns'] or info['seed'] != job['seed']
        or info['params'] != recipe(job['seed'], smoke).resolved_params()
        or info['target_encode_cols'] != ['jockey_id', 'trainer_id'] or info['te_smoothing'] != 10.
        or info['calibration'] != 'isotonic_strict_past_oof' or info['calibrator_degenerate']):
        raise ValueError('Actual fitted model recipe/profile differs')
    _write_model(base, path / 'model.txt')
    dump(path / 'calibrator.pkl', base.calibrator_)
    dump(path / 'preprocessor.pkl', build_preprocessor(base, frozen['feature_version']))
    vocab = categorical_vocab_from_booster(base.win_model_.booster_, info['feature_cols'], info['categorical_cols'])
    meta = {'artifact_kind': '135_colsample_research_model', 'session': None, 'seed': job['seed'],
        'year': job.get('year'), 'kind': job.get('kind', 'annual'), 'smoke': smoke,
        'train_hash': job['train_hash'], 'train_through': job['train_through'], 'oof_partition': job['oof'],
        'feature_count': 125, 'feature_columns': info['feature_cols'], 'categorical_columns': info['categorical_cols'],
        'categorical_vocab_sha256': vocab_hash(vocab), 'calibrator_sha256': stable_hash(base.calibrator_.params_dict()),
        'recipe': recipe_meta(job['seed'], smoke), 'actual_params': info['params'], 'fit_info': info,
        'source_hash': frozen['source_hash'], 'runtime': frozen['runtime'],
        'snapshot_sha256': common.SNAPSHOT_SHA256, 'freeze_sha256': digest(WORK / 'run-freeze.json'),
        'can_adopt': False, 'eligible_for_verdict': False}
    write_json(path / 'metadata.json', meta)
    files = {name: digest(path / name) for name in MODEL_FILES}
    return load_model(path, files, expected=meta), files


def job_name(seed, year=None, smoke=False, recent=False):
    if seed not in SEEDS or (not recent and year not in YEARS) or (recent and seed != 42):
        raise ValueError('Unregistered fit job')
    return ('smoke-' if smoke else '') + (f'recent-{seed}' if recent else f'seed-{seed}-{year}')


def step_gate(*, recent=False):
    """Check completed owned artifacts only; never import/execute Step1 code."""
    area = WORK.parent / 'calibration'
    receipt = read_json(area / 'receipt.json')
    if (receipt['status'] != 'STEP1_COMPLETE' or receipt['can_adopt'] is not False
        or receipt['eligible_for_verdict'] is not False or receipt['additional_booster_fits'] != 0
        or receipt['experiment_sha256'] != digest(SPEC / 'experiment.json')
        or receipt['design_sha256'] != digest(area / 'design.json')
        or receipt['summary_sha256'] != digest(area / 'summary.json')):
        raise ValueError('Step1 completion/config/summary receipt differs')
    for key in ('source_sha256', 'input_sha256'):
        for path, sha in receipt[key].items():
            actual = Path(path)
            # The recorded source filename was externally replaced after execution.
            # Only this one explicit alias is allowed, and must retain the EXACT old SHA.
            if key == 'source_sha256' and actual == ROOT / 'scripts/calibration_serving_recheck.py':
                actual = area / 'execution-source.py'
            if digest(actual) != sha: raise ValueError(f'Step1 recorded {key} changed: {path}')
    review_path = SPEC / 'evidence/calibration-independent-review.json'
    review = read_json(review_path)
    if (review['status'] != 'PASS' or review['summary_sha256'] != receipt['summary_sha256']
        or review['design_sha256'] != receipt['design_sha256']
        or review['experiment_sha256'] != receipt['experiment_sha256']
        or review['original_source_recovery_sha256'] != digest(area / 'execution-source.py')
        or review['additional_booster_fits'] != 0 or review['can_adopt'] is not False
        or review['eligible_for_verdict'] is not False):
        raise ValueError('Step1 independent review identity differs')
    binding = {'step1_receipt_sha256': digest(area / 'receipt.json'),
               'step1_summary_sha256': receipt['summary_sha256'],
               'step1_independent_review_sha256': digest(review_path)}
    if recent:
        summary = read_json(WORK / 'summary.json')
        if (summary['status'] != 'RESEARCH_COMPLETE' or summary['freeze_sha256'] != digest(WORK / 'run-freeze.json')
            or summary['prediction_sha256'] != digest(WORK / 'mixture-predictions.pkl')):
            raise ValueError('Step2 must be complete before recent-fit')
        binding['step2_summary_sha256'] = digest(WORK / 'summary.json')
    return binding


def fit(seed, year=None, *, smoke=False, recent=False):
    name = job_name(seed, year, smoke, recent)
    frozen = verify()
    gate = step_gate(recent=recent)
    if smoke and (seed != 42 or year != 2024 or recent):
        raise ValueError('Only registered seed42/2024 structural smoke is allowed')
    if not smoke:
        smoke_receipt = read_json(WORK / 'jobs/smoke-seed-42-2024/receipt.json')
        if (smoke_receipt['freeze_sha256'] != digest(WORK / 'run-freeze.json')
            or smoke_receipt['roundtrip']['status'] != 'PASS'):
            raise ValueError('Completed portable-model smoke required')
    job = frozen['recent_job'] if recent else next(j for j in frozen['jobs'] if j['seed'] == seed and j['year'] == year)
    final, partial = WORK / 'jobs' / name, WORK / 'jobs' / (name + '.partial')
    if final.exists() or partial.exists(): raise FileExistsError('Completed/partial fit preserved')
    partial.mkdir(parents=True, exist_ok=False)
    lock = WORK / (name + '.lock'); write_json(lock, {'pid': os.getpid(), 'job': name})
    try:
        if len(list(WORK.glob('*.lock'))) > 2:
            raise ValueError('Maximum two research workers exceeded')
        matrix, races = load(common.SNAPSHOT); validate_matrix(matrix, races)
        if recent:
            train = races
            valid = [r for r in races if r.context.race_date.year == 2026]  # Serialization probes; not an efficacy claim.
        else:
            fold = folds_for(races)[year]; train, valid = list(fold.train), list(fold.valid)
        if train_identity(train) != job['train_hash'] or partition_proof(train) != job['oof']:
            raise ValueError('Actual train/OOF partition differs')
        if smoke:
            train = [r for r in train if r.context.race_date <= dt.date(2008, 12, 31)]
            valid = valid[:24]
            job = dict(job, train_hash=train_identity(train), train_through=str(max(r.context.race_date for r in train)),
                       n_train=len(train), n_valid=len(valid), oof=partition_proof(train, 2))
        t0 = time.monotonic()
        predictor = make_predictor(matrix, seed, smoke)
        print(f'FIT {name} train={len(train)} valid={len(valid)} boosters={job["oof"]["booster_fits"]}', flush=True)
        with frozen_outcomes(train): predictor.fit([r.context for r in train], num_threads=1)
        predictions = {r: {} for r in REGIMES}
        for regime, mask in REGIMES.items():
            predictor.set_predict_weight_mask(mask)
            for er in valid:
                predictions[regime][er.context.race_id] = prediction_array(predictor.predict_race(er.context),
                    [h.horse_id for h in er.context.started_horses])
        predictor.set_predict_weight_mask(None)
        base = predictor.to_servable()
        loaded, model_files = save_model(base, partial / 'model', job, frozen, smoke)
        groups = matrix.frame.loc[matrix.frame.race_id.isin([r.context.race_id for r in valid])].groupby('race_id', sort=False)
        n_values = 0
        for er in valid:
            rows = groups.get_group(er.context.race_id)
            for regime in REGIMES:
                before = predictions[regime][er.context.race_id]
                after = loaded.predict(er.context, rows, regime)
                if not np.array_equal(before, after): raise ValueError('Serialized full/preweight prediction parity failed')
                n_values += before.size
        parity126 = None
        if seed == 42 and not smoke and not recent:
            old = load(frozen['native126_caches'][str(year)]['path'])
            for er in valid:
                rid = er.context.race_id; ids = [h.horse_id for h in er.context.started_horses]
                if not np.array_equal(predictions['full'][rid], prediction_array(old['predictions'][rid], ids)):
                    raise ValueError('Fresh seed42 full probabilities do not reproduce native126')
            cohort = [r for r in load(WORK / 'records.pkl')['full'] if r.year == year]
            old_predictions = {r.race_id: prediction_array(old['predictions'][r.race_id], r.ids) for r in cohort}
            scores = common.summarize_predictions(cohort, predictions['full'])
            old_scores = common.summarize_predictions(cohort, old_predictions)
            if scores != old_scores: raise ValueError('Native126 full loss parity failed')
            parity126 = {'status': 'PASS', 'n_races': len(valid), 'max_abs_diff': 0.,
                         'all_metrics_exact': True, 'scores': scores}
        elapsed = time.monotonic() - t0
        payload = {'job': job, 'smoke': smoke, 'recent': recent, 'predictions': predictions,
            'valid_ids': [r.context.race_id for r in valid], 'oof_info': canonical(predictor.oof_info_),
            'fit_seconds': elapsed, 'source_hash': frozen['source_hash'], 'recipe': recipe_meta(seed, smoke),
            'can_adopt': False, 'eligible_for_verdict': False}
        dump(partial / 'predictions.pkl', payload)
        verify()
        if step_gate(recent=recent) != gate: raise ValueError('Step completion receipt changed during fit')
        write_json(partial / 'receipt.json', {'job': name, 'freeze_sha256': digest(WORK / 'run-freeze.json'),
            'step_gate': gate,
            'prediction_sha256': digest(partial / 'predictions.pkl'), 'model_files': model_files,
            'roundtrip': {'status': 'PASS', 'n_races': len(valid), 'n_values': n_values, 'max_abs_diff': 0.,
                          'regimes': list(REGIMES)}, 'native126_parity': parity126,
            'fit_seconds': elapsed, 'booster_fits': job['oof']['booster_fits'], 'model_threads': 1,
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'recent_inputs_loaded': False, 'can_adopt': False, 'eligible_for_verdict': False})
        partial.rename(final)
        print(f'FIT PASS {name} seconds={elapsed:.1f}', flush=True)
    finally:
        lock.unlink()


def read_job(seed, year, frozen):
    path = WORK / 'jobs' / job_name(seed, year)
    receipt = read_json(path / 'receipt.json')  # Missing cache never starts training.
    if (receipt['freeze_sha256'] != digest(WORK / 'run-freeze.json')
        or receipt['prediction_sha256'] != digest(path / 'predictions.pkl')
        or receipt['roundtrip']['status'] != 'PASS'):
        raise ValueError('Fit receipt differs')
    payload = load(path / 'predictions.pkl')
    job = next(j for j in frozen['jobs'] if j['seed'] == seed and j['year'] == year)
    if (payload['job'] != job or payload['smoke'] or payload['recent']
        or payload['source_hash'] != frozen['source_hash'] or not payload['oof_info']['sufficient']
        or payload['recipe'] != recipe_meta(seed) or set(payload['predictions']) != set(REGIMES)):
        raise ValueError('Fit cache identity differs')
    records = load(WORK / 'records.pkl')['full']
    expected_ids = [r.race_id for r in records if r.year == year]
    if payload['valid_ids'] != expected_ids or any(list(p) != expected_ids for p in payload['predictions'].values()):
        raise ValueError('Fit validation race order differs')
    model = load_model(path / 'model', receipt['model_files'])
    if (model.metadata['train_hash'] != job['train_hash'] or model.metadata['seed'] != seed
        or model.metadata['year'] != year or model.metadata['train_through'] != job['train_through']
        or model.metadata['oof_partition'] != job['oof'] or model.metadata['smoke']
        or model.metadata['kind'] != 'annual'):
        raise ValueError('Saved model identity differs from frozen job')
    return payload['predictions']


def construct(record, raw, beta, lambdas):
    corrected = prediction_array(common.mc.correct_member_predictions(record.ids, raw[:, 0],
        record.correction_inputs, common.mc.JOINT_TERMS, beta), record.ids)
    w = 1. / 7.
    # Match132's floating-point expression as well as its mathematical weights.
    mixed = common.validate_heads((1. - w) * record.heads + w * corrected)
    stage = {k: lambdas[k] for k in ('lambda2', 'lambda3')}
    baseline = common.heads_from_win(record.heads[:, 0], **stage)
    candidate = common.heads_from_win(mixed[:, 0], **stage)
    if not np.array_equal(candidate[:, 0], mixed[:, 0]): raise ValueError('Topk method changed win')
    return baseline, candidate, mixed


def seed_mean_metrics(records, baseline, candidates, *, b, seed, alpha):
    """Average losses of seeds for each race; never create an averaged predictor."""
    base, counts = common._day_statistics(records, baseline)
    all_candidates = [common._day_statistics(records, p) for p in candidates]
    if any(c != counts for _, c in all_candidates): raise ValueError('Seed populations differ')
    output = {}
    for metric in base:
        days = sorted(base[metric])
        den = np.array([base[metric][d][1] for d in days], dtype=float)
        if not days: raise ValueError('Empty seed aggregate metric')
        for stats, _ in all_candidates:
            if sorted(stats[metric]) != days or not np.array_equal(den, [stats[metric][d][1] for d in days]):
                raise ValueError('Seed/day denominators differ')
        nums = np.array([[stats[metric][d][0] - base[metric][d][0] for d in days] for stats, _ in all_candidates])
        num = nums.mean(axis=0); rng = np.random.default_rng(seed); reps = []
        for start in range(0, b, 128):
            take = rng.integers(0, len(days), size=(min(128, b - start), len(days)))
            reps.extend((num[take].sum(axis=1) / den[take].sum(axis=1)).tolist())
        points = nums.sum(axis=1) / den.sum()
        output[metric] = {'point': float(num.sum() / den.sum()),
            'ci': np.quantile(reps, [alpha / 2, 1 - alpha / 2]).tolist(), 'n_days': len(days), **counts[metric],
            'seed_points': points.tolist(), 'seed_sd': float(points.std(ddof=1)) if len(points) > 1 else None,
            'seed_range': [float(points.min()), float(points.max())]}
    return output


def noninferiority(metric, margin):
    low, high = metric['ci']
    return 'SUPPORTED' if high <= margin else ('WORSENING_SUPPORTED' if low > margin else 'UNRESOLVED')


def quality(scores, base_scores, paired, cfg):
    return {'topk': {h: {'margin': cfg[f'{h}_logloss_margin'],
                'status': noninferiority(paired[f'{h}_logloss'], cfg[f'{h}_logloss_margin'])} for h in ('top2', 'top3')},
        'ece': {h: {'point': scores['heads'][h]['ece'],
                   'diff': scores['heads'][h]['ece'] - base_scores['heads'][h]['ece'],
                   'within_descriptive_guard': scores['heads'][h]['ece'] <= cfg['ece_ceiling'] and
                    scores['heads'][h]['ece'] - base_scores['heads'][h]['ece'] <= cfg['ece_margin']}
                for h in common.HEADS}}


def summarize():
    frozen = verify()
    if (WORK / 'summary.json').exists(): raise FileExistsError('Summary exists; preserved')
    records = load(WORK / 'records.pkl')
    old132 = load(SOURCE132 / 'colsample/add_transferred_1of7-predictions.pkl')
    reports, outputs = {}, {r: {s: {} for s in SEEDS} for r in REGIMES}
    raw_mixtures = {r: {s: {} for s in SEEDS} for r in REGIMES}
    baseline = {r: {} for r in REGIMES}; parity132 = 0
    for year in YEARS:
        for seed in SEEDS:
            raw = read_job(seed, year, frozen)
            for regime in REGIMES:
                cohort = [r for r in records[regime] if r.year == year]
                if set(raw[regime]) != {r.race_id for r in cohort}: raise ValueError('Prediction race set differs')
                for r in cohort:
                    b, c, mixed = construct(r, common.validate_heads(raw[regime][r.race_id], len(r.ids)),
                        frozen['coefficients'][str(seed)][str(year)], frozen['lambdas'][regime][str(year)])
                    if r.race_id in baseline[regime] and not np.array_equal(baseline[regime][r.race_id], b):
                        raise ValueError('Baseline changed across seeds')
                    baseline[regime][r.race_id] = b
                    outputs[regime][seed][r.race_id] = c
                    raw_mixtures[regime][seed][r.race_id] = mixed
                    if seed == 42 and regime == 'full':
                        if not np.array_equal(mixed, old132['predictions'][r.race_id]):
                            raise ValueError('132 corrected1/7 mixture parity failed')
                        parity132 += 1
    for regime in REGIMES:
        reports[regime] = {}
        for label, cohort in [(str(y), [r for r in records[regime] if r.year == y]) for y in YEARS] + [('pooled', records[regime])]:
            ids = [r.race_id for r in cohort]
            b = {rid: baseline[regime][rid] for rid in ids}
            bs = common.summarize_predictions(cohort, b)
            candidates = [{rid: outputs[regime][s][rid] for rid in ids} for s in SEEDS]
            per_seed = {}
            for seed, c in zip(SEEDS, candidates, strict=True):
                scores = common.summarize_predictions(cohort, c)
                paired = common.paired_metrics(cohort, b, c, **frozen['config']['bootstrap'])
                per_seed[str(seed)] = {'scores': scores, 'paired': paired,
                    'quality': quality(scores, bs, paired, frozen['config']['quality'])}
            mean = seed_mean_metrics(cohort, b, candidates, **frozen['config']['bootstrap'])
            reports[regime][label] = {'baseline': bs, 'seeds': per_seed, 'seed_mean_loss_difference': mean,
                'seed_mean_topk_quality': {h: noninferiority(mean[f'{h}_logloss'], frozen['config']['quality'][f'{h}_logloss_margin'])
                                         for h in ('top2', 'top3')}}
    dump(WORK / 'mixture-predictions.pkl', {'baseline': baseline, 'candidates': outputs,
                                          'raw_three_head_mixtures': raw_mixtures})
    verify()
    write_json(WORK / 'summary.json', {'status': 'RESEARCH_COMPLETE', 'reports': reports,
        'native132_seed42_full_parity': {'status': 'PASS', 'n_races': parity132, 'max_abs_diff': 0.},
        'prediction_sha256': digest(WORK / 'mixture-predictions.pkl'),
        'freeze_sha256': digest(WORK / 'run-freeze.json'), 'data_through': str(END),
        'uncertainty': 'Day-cluster sampling conditional on these fitted models/fixed coefficients; three seeds do not establish procedure variance.',
        'primary_regime': 'preweight', 'can_adopt': False, 'eligible_for_verdict': False})
    print('SUMMARIZE PASS six fixed jobs; no seed-probability ensemble', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=('prepare', 'verify', 'fit', 'smoke', 'recent-fit', 'summarize'))
    ap.add_argument('--seed', type=int, choices=SEEDS, default=42)
    ap.add_argument('--year', type=int, choices=YEARS, default=2024)
    args = ap.parse_args()
    if args.command == 'prepare': prepare()
    elif args.command == 'verify': verify(); print('VERIFY PASS', flush=True)
    elif args.command == 'summarize': summarize()
    else: fit(args.seed, args.year, smoke=args.command == 'smoke', recent=args.command == 'recent-fit')


if __name__ == '__main__': main()
