"""Explicit file-only serving for the frozen research-125 six-member profile.

This loader does not change the legacy model registry or its compatibility rules.
All artifact digests are checked before unpickling a member. Use a pinned external
manifest digest when loading a previously selected bundle.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from pathlib import Path
import pickle
import re

import lightgbm as lgb
import numpy as np
import pandas as pd
from horseracing_eval.predictor import Prediction
from horseracing_features import registry
from horseracing_training.artifacts import categorical_vocab_from_booster, feature_hash, vocab_hash
from horseracing_training.predictor import assemble_predictions
from horseracing_training.target_encoding import apply_encoded_columns

from .mixture_correction import (JOINT_TERMS, average_member_predictions,
                                 build_correction_inputs, correct_member_predictions)
from .model_loader import ServingModel
from .predictor import SAME_DAY_WEIGHT_COLUMNS, normalise_weight_availability

PROFILE = '125_new_joint_mixed6_v1'
FILES = ('model.txt', 'calibrator.pkl', 'preprocessor.pkl', 'metadata.json')
IDENTITIES = [dict(id=f'{label}-{seed}', branch=branch, seed=seed)
              for label, branch in [('joint', 'pruning'), ('anchor', 'anchor')]
              for seed in (42, 43, 44)]
FULL_HASH = '663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a'
PRUNED_HASH = 'dd64c70b709261954922839bc97cbdf3f28324dd8dd7c1c954e738fbbb227ed3'
PRUNING_DROPS = [
    'win_rate_vs_field', 'recent_win_rate_vs_field', 'place_rate_vs_field',
    'show_rate_vs_field', 'dist_band_win_rate_vs_field', 'surface_win_rate_vs_field',
    'rel_time_avg_vs_field', 'rel_last3f_avg_vs_field', 'finish_diff_best_vs_field',
    'jockey_win_rate_vs_field', 'trainer_win_rate_vs_field', 'win_rate_field_rank',
    'rel_time_avg_field_rank',
]
INFERENCE = dict(base_eps=1e-6, assembly_eps=0., head_aggregation='arithmetic_mean',
                 postprocess='none', history_start='2007-01-01', same_day_excluded=True)
PARAMS = dict(objective='binary', n_estimators=900, learning_rate=.05, num_leaves=31,
              min_child_samples=20, subsample=1., colsample_bytree=1., reg_lambda=1.)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def read_json(path):
    def reject(value): raise ValueError(f'Nonfinite JSON value: {value}')
    return json.loads(Path(path).read_text(), parse_constant=reject)


def _is_sha(value): return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def feature_profile():
    full = registry.model_input_features()
    if registry.FEATURE_VERSION != 'features-021' or feature_hash(full) != FULL_HASH:
        raise ValueError('Current feature registry differs from the explicit frozen raw profile')
    return dict(full_columns=full, pruning_drops=list(PRUNING_DROPS), full_columns_hash=FULL_HASH,
                raw_representation='raw', source_feature_version='features-021')


def columns_for(branch):
    full = feature_profile()['full_columns']
    if branch == 'anchor': return full
    if branch != 'pruning': raise ValueError('Unknown member branch')
    cols = [c for c in full if c not in PRUNING_DROPS]
    if len(cols) != 125 or feature_hash(cols) != PRUNED_HASH: raise ValueError('Pruning profile changed')
    return cols


def contained_path(root, relative):
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or '..' in rel.parts:
        raise ValueError('Bundle path must be relative and contained')
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink(): raise ValueError('Bundle symlink is not allowed')
    if not path.resolve().is_relative_to(root): raise ValueError('Bundle path escaped root')
    return path


def validate_manifest(d):
    fixed = dict(schema_version=1, artifact_kind='candidate_mixture_bundle', profile=PROFILE,
                 mode='shadow', can_adopt=False, eligible_for_verdict=False,
                 train_through='2026-08-23', coefficient_train_through='2025-12-31')
    if not isinstance(d, dict) or any(d.get(k) != v for k, v in fixed.items()):
        raise ValueError('Unknown or non-shadow candidate bundle')
    if d.get('can_adopt') is not False or d.get('eligible_for_verdict') is not False:
        raise ValueError('Bundle cannot adopt or issue a verdict')
    if not isinstance(d.get('bundle_id'), str) or not d['bundle_id']:
        raise ValueError('Missing bundle identity')
    try:
        created = dt.datetime.fromisoformat(d['created_at'])
        if created.utcoffset() != dt.timedelta(0): raise ValueError('UTC timestamp required')
    except (KeyError, TypeError, ValueError) as exc: raise ValueError('Invalid creation timestamp') from exc
    if d.get('feature_profile') != feature_profile() or d.get('inference') != INFERENCE:
        raise ValueError('Feature or inference profile differs')
    members = d.get('members', [])
    if len(members) != 6: raise ValueError('Exactly six ordered members required')
    for item, expected in zip(members, IDENTITIES, strict=True):
        if ({k: item.get(k) for k in expected} != expected or item.get('weight') != 1 / 6
                or item.get('artifact_dir') != 'members/' + expected['id']
                or set(item.get('files', {})) != set(FILES)
                or not all(_is_sha(s) for s in item['files'].values())):
            raise ValueError('Member identity, order, weight, path, or file hashes differ')
        correction = item.get('correction', {})
        joint = expected['branch'] == 'pruning'
        terms = JOINT_TERMS if joint else ['gap_log']
        beta = np.asarray(correction.get('coefficients', []))
        if (correction.get('terms') != terms or beta.shape != (len(terms),)
                or beta.dtype.kind not in 'fiu' or not np.isfinite(beta).all()
                or (joint and 1 + beta[-1] <= 0)
                or correction.get('source_study') != (125 if joint else 118)
                or not _is_sha(correction.get('source_sha256'))
                or correction.get('valid_year') != 2026 or correction.get('fit_through') != '2025-12-31'):
            raise ValueError('Frozen correction profile differs')
    if not isinstance(d.get('sources'), dict): raise ValueError('Missing provenance')


@dataclass(frozen=True)
class MixtureMember:
    id: str
    model: ServingModel
    terms: tuple[str, ...]
    coefficients: tuple[float, ...]


@dataclass(frozen=True)
class MixtureBundle:
    path: Path | None
    sha256: str
    manifest: dict
    members: tuple[MixtureMember, ...]


@dataclass(frozen=True)
class MixturePrediction:
    predictions: dict[str, Prediction]
    inputs: pd.DataFrame
    audit: dict


def _load_member(path, item):
    meta = read_json(path / 'metadata.json')
    cols = columns_for(item['branch']); fh = feature_hash(cols)
    info, oof = meta.get('fit_info', {}), meta.get('oof_info', {})
    protocol = info.get('calibration_protocol', {})
    if (meta.get('artifact_kind') != 'candidate_mixture_member' or meta.get('mode') != 'shadow'
            or meta.get('smoke') is not False or meta.get('member_id') != item['id']
            or meta.get('branch') != item['branch'] or meta.get('seed') != item['seed']
            or meta.get('feature_cols') != cols or meta.get('feature_hash') != fh
            or meta.get('feature_version') != 'features-021' or meta.get('race_class_representation') != 'raw'
            or meta.get('train_through') != '2026-08-23' or meta.get('coefficient_train_through') != '2025-12-31'
            or any(meta.get(k) is not False for k in ('can_adopt', 'eligible_for_verdict', 'db_registration'))
            or meta.get('objective') != 'pl_topk' or meta.get('postprocess') != 'group_softmax'
            or meta.get('actual_params') != PARAMS or info.get('params') != PARAMS
            or info.get('seed') != item['seed'] or info.get('feature_cols') != cols
            or info.get('objective') != 'pl_topk' or info.get('model_degenerate') is not False
            or info.get('calibrator_degenerate') is not False or oof.get('sufficient') is not True
            or oof.get('calibrator_degenerate') is not False
            or protocol.get('protocol') != 'strict_past_oof_isotonic_v1'
            or protocol.get('n_oof_blocks') != 8 or protocol.get('score_space') != 'raw_race_softmax'
            or info.get('target_encode_cols') != ['jockey_id', 'trainer_id'] or info.get('te_smoothing') != 10.
            or info.get('weight_mask') != dict(rate=.5, seed=20260810, unit='race', columns=list(SAME_DAY_WEIGHT_COLUMNS))):
        raise ValueError('Member fitted profile/provenance differs')
    pop = meta.get('population', {})
    # ``train_from`` is the ACTUAL first training race day inside the frozen 2007-01-01 scope
    # (the calendar has no race on 2007-01-01), so it is bounded rather than pinned.
    try:
        first_day = dt.date.fromisoformat(str(pop.get('train_from')))
    except ValueError:
        first_day = None
    if (pop.get('train_through') != '2026-08-23' or first_day is None
            or not (dt.date(2007, 1, 1) <= first_day <= dt.date(2007, 1, 31))
            or pop.get('n_train_races', 0) <= 0 or pop.get('n_train_rows', 0) <= 0
            or info.get('n_train_rows') != pop.get('n_train_rows')
            or info.get('n_model_rows') != pop.get('n_train_rows')
            or oof.get('n_oof_rows', 0) < 2000 or oof.get('n_oof_races', 0) < 200
            or oof.get('n_positives', 0) < 200 or oof.get('n_distinct_scores', 0) < 2):
        raise ValueError('Member training or calibration population differs')
    with (path / 'preprocessor.pkl').open('rb') as f: prep = pickle.load(f)
    with (path / 'calibrator.pkl').open('rb') as f: calib = pickle.load(f)
    if (prep.get('feature_cols') != cols or prep.get('feature_hash') != fh
            or prep.get('feature_version') != 'features-021' or prep.get('race_class_representation') != 'raw'
            or prep.get('categorical_cols') != meta.get('categorical_cols')
            or not set(prep.get('categorical_cols', [])).issubset(cols)
            or prep.get('objective') != 'pl_topk' or prep.get('postprocess') != 'group_softmax'
            or prep.get('target_encode_cols') != ['jockey_id', 'trainer_id']
            or set(prep.get('encoders', {})) != {'jockey_id', 'trainer_id'}
            or prep.get('te_smoothing') != 10. or getattr(calib, 'identity', True)
            or getattr(calib, 'method', None) != 'isotonic' or getattr(calib, 'clip', None) != 1e-6):
        raise ValueError('Member preprocessor or calibrator differs')
    booster = lgb.Booster(model_file=str(path / 'model.txt'))
    if (booster.feature_name() != cols or booster.num_trees() != 900
            or '[num_threads: 1]' not in (path / 'model.txt').read_text()):
        raise ValueError('Member booster columns, tree count, or thread count differs')
    vocab = categorical_vocab_from_booster(booster, cols, meta['categorical_cols'])
    if vocab != meta.get('categorical_vocab') or vocab_hash(vocab) != meta.get('categorical_vocab_hash'):
        raise ValueError('Member categorical vocabulary differs')
    return ServingModel(item['id'], booster, 0., calib, cols, meta['categorical_cols'],
                        encoders=prep['encoders'], feature_version='features-021', feature_hash=fh,
                        race_class_representation='raw', categorical_vocab=vocab,
                        objective='pl_topk', metadata=meta)


def load_mixture_bundle(path, *, expected_sha256=None):
    path = Path(path)
    if path.is_symlink(): raise ValueError('Bundle manifest symlink is not allowed')
    digest = sha256(path)
    if expected_sha256 is not None and digest != expected_sha256: raise ValueError('Bundle SHA mismatch')
    d = read_json(path); validate_manifest(d)
    paths = []
    # Verify the entire six-member envelope before deserializing any pickle.
    for item in d['members']:
        directory = contained_path(path.parent, item['artifact_dir'])
        for name, expected in item['files'].items():
            artifact = contained_path(path.parent, item['artifact_dir'] + '/' + name)
            if not artifact.is_file() or sha256(artifact) != expected:
                raise ValueError('Member artifact SHA mismatch or missing: ' + str(artifact))
        paths.append(directory)
    members = tuple(MixtureMember(item['id'], _load_member(directory, item),
                                   tuple(item['correction']['terms']), tuple(item['correction']['coefficients']))
                    for directory, item in zip(paths, d['members'], strict=True))
    populations = [member.model.metadata['population'] for member in members]
    if any(p != populations[0] for p in populations[1:]): raise ValueError('Member populations differ')
    return MixtureBundle(path.resolve(), digest, d, members)


def prepare_race_inputs(feature_rows, race_id, regime, *, feature_cols=None):
    if regime not in ('preweight', 'serving', 'full_information_replay'): raise ValueError('Unknown input regime')
    cols = feature_profile()['full_columns'] if feature_cols is None else feature_cols
    required = {'race_id', 'horse_id', 'race_date', *cols}
    if not required.issubset(feature_rows.columns) or feature_rows.columns.duplicated().any():
        raise ValueError('Missing or duplicate raw model input columns')
    data = feature_rows[feature_rows.race_id == race_id].copy()
    if (data.empty or data.horse_id.isna().any() or data.horse_id.duplicated().any()
            or data.race_date.nunique() != 1): raise ValueError('Invalid started race identity')
    data = data.sort_values('horse_id', kind='stable').reset_index(drop=True)
    n_weighed = int(pd.to_numeric(data['weight'], errors='coerce').notna().sum()) if 'weight' in data else 0
    normalised = False
    if regime == 'preweight':
        data[[c for c in SAME_DAY_WEIGHT_COLUMNS if c in data]] = np.nan
    elif regime == 'serving':
        availability = normalise_weight_availability(data, feature_cols=cols)
        data, normalised = availability.rows, availability.normalised
    return data, dict(regime=regime, n_started=len(data), n_weighed_before=n_weighed,
                     weight_normalised=normalised, diagnostic_only=regime == 'full_information_replay')


def predict_base(model, rows):
    """The same raw/category/TE/calibration path as serving, before residuals."""
    data = rows.copy()
    for col in model.categorical_cols: data[col] = data[col].astype('category')
    for col in model.feature_cols:
        if col not in model.categorical_cols and col not in model.encoders:
            data[col] = pd.to_numeric(data[col], errors='coerce')
    encoded = {col: enc.transform(data[col]) for col, enc in model.encoders.items()}
    x = apply_encoded_columns(data[model.feature_cols].copy(), encoded, model.feature_cols)
    numeric = x.select_dtypes(include='number').to_numpy(dtype=float)
    if np.isinf(numeric).any(): raise ValueError('Infinite model input')
    raw = np.asarray(model.raw_predict(x), dtype=float)
    calibrated = np.asarray(model.calibrator.transform(raw), dtype=float)
    if (raw.shape != (len(rows),) or calibrated.shape != raw.shape
            or not np.isfinite(raw).all() or not np.isfinite(calibrated).all()):
        raise ValueError('Invalid base prediction')
    return assemble_predictions(rows.horse_id.tolist(), calibrated, eps=1e-6), x


def coefficient_year_offset(valid_year, target_year, grace_years=0):
    """Years the target lies past the coefficients' fit year; fail closed outside the grace."""
    offset = int(target_year) - int(valid_year)
    if offset < 0 or offset > int(grace_years):
        raise ValueError(
            f'Frozen residual coefficients are valid for {valid_year} (+{grace_years} grace), '
            f'not {target_year}')
    return offset


def predict_mixture(bundle, race_id, feature_rows, history, regime='preweight', *,
                    coefficient_grace_years=0):
    if [member.id for member in bundle.members] != [item['id'] for item in IDENTITIES]:
        raise ValueError('Six loaded members required in the registered order')
    rows, audit = prepare_race_inputs(feature_rows, race_id, regime)
    valid_years = {int(item['correction']['valid_year']) for item in bundle.manifest['members']}
    if len(valid_years) != 1:
        raise ValueError('Members disagree on coefficient year')
    valid_year = valid_years.pop()
    stale = coefficient_year_offset(valid_year, pd.Timestamp(rows.race_date.iloc[0]).year,
                                    coefficient_grace_years)
    audit.update(coefficient_year=valid_year, coefficient_stale_years=stale)
    # Filter to target horses before the history helper; result-less starts remain.
    correction = build_correction_inputs(rows, history[history.horse_id.isin(rows.horse_id)])
    ids = rows.horse_id.tolist(); predictions = []; base_hashes = {}
    for member in bundle.members:
        base, _ = predict_base(member.model, rows)
        p = np.array([base[h].win for h in ids])
        base_hashes[member.id] = hashlib.sha256(p.astype('<f8').tobytes()).hexdigest()
        predictions.append(correct_member_predictions(ids, p, correction, member.terms, member.coefficients))
    audit.update(bundle_sha256=bundle.sha256, member_ids=[member.id for member in bundle.members],
                 base_win_sha256=base_hashes, head_aggregation='arithmetic_mean', postprocess='none')
    return MixturePrediction(average_member_predictions(predictions), rows, audit)
