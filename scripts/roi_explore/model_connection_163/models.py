"""File-only inference for the pinned 15-member MEV and six-member preweight mixture.

No training, mutable DB loads, fetcher, clock or persistence lives in this module. All
features and the old model's strictly past started history must be explicit inputs.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
for package in ('serving', 'training', 'features', 'eval', 'probability', 'db'):
    source = str(ROOT/package/'src')
    if source not in sys.path:
        sys.path.insert(0, source)

from horseracing_serving.mixture_correction import (  # noqa: E402
    build_correction_inputs, correct_member_predictions,
)
from horseracing_serving.mixture_model import (  # noqa: E402
    average_for_profile, load_mixture_bundle, predict_base, predict_mixture, prepare_race_inputs,
)
from horseracing_serving.mixture_serving import assert_history_matches_features  # noqa: E402
from horseracing_serving.model_loader import ServingError  # noqa: E402
from horseracing_training.market_ev import EnsembleMarketEvModel  # noqa: E402
from horseracing_training.target_encoding import apply_encoded_columns  # noqa: E402
from scripts.roi_explore.prospective_rehearsal_162.contract import (  # noqa: E402
    WEIGHTS, canonical, digest, field, make_protocol, require, sha_bytes,
)

MODE = 'offline_model_connection'
MEV_DIR = ROOT/'artifacts/market_ev/mev-ens15-v1'
LEGACY_BUNDLE = ROOT/'artifacts/129-candidate-mixture-serving/bundle.json'
PINNED_SHA = dict(mev='6a4f42895b7c7b622559fc30458f84eff39744099a3a80778c7d1a541de1c7d9',
    legacy='28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471')
SOURCES = ('models.py', 'training/src/horseracing_training/market_ev.py',
    'serving/src/horseracing_serving/mixture_model.py',
    'serving/src/horseracing_serving/mixture_correction.py',
    'serving/src/horseracing_serving/mixture_serving.py',
    'serving/src/horseracing_serving/model_loader.py',
    'training/src/horseracing_training/predictor.py',
    'training/src/horseracing_training/target_encoding.py')


def _encode(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        require(not math.isinf(float(value)), 'infinite_model_input')
        return None if math.isnan(float(value)) else float(value)
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)):
        return value.isoformat()
    return str(value)


def _frame_evidence(frame):
    out = dict(columns=list(frame.columns), dtypes={c: str(frame[c].dtype) for c in frame},
        rows=[[_encode(value) for value in row] for row in frame.itertuples(index=False, name=None)])
    out['categories'] = {c: [_encode(v) for v in frame[c].cat.categories]
        for c in frame if isinstance(frame[c].dtype, pd.CategoricalDtype)}
    return out


def _verify_pins():
    protocol = make_protocol()  # Validates every model/preprocessor/calibrator body before pickle load.
    require(protocol['model_shas'] == PINNED_SHA, 'unpinned_model_manifest')
    return protocol


def input_columns(kind):
    protocol = _verify_pins()
    if kind == 'legacy':
        return list(protocol['feature_columns']['legacy'])
    require(kind == 'mev', 'unknown_model_kind')
    spec = json.loads((MEV_DIR/'ensemble.spec.json').read_text())
    return list(spec['features'])+list(spec['cats'])


def _input_frame(rows, race_id, race_date, kind):
    require(isinstance(race_id, str) and len(race_id) == 12 and race_id.isdigit(), 'invalid_race_id')
    day = dt.date.fromisoformat(race_date)
    require(day.year == 2026, 'unsupported_model_year')
    protocol = _verify_pins()
    require(protocol['train_through'][kind] < race_date, 'model_training_overlaps_target')
    expected = input_columns(kind)
    field(rows)
    output = []
    for row in rows:
        require(set(row) == {'horse_id', 'horse_number', 'features'}, 'invalid_model_input_row_schema')
        features = row['features']
        require(isinstance(features, dict) and set(features) == set(expected), 'incomplete_model_input_matrix')
        canonical(features)  # Reject nonfinite JSON, do not impute unknown values.
        require(features['horse_number'] == row['horse_number'], 'model_horse_number_mismatch')
        require(features['field_size'] == len(rows), 'model_field_size_mismatch')
        if kind == 'legacy':
            require(all(features[k] is None for k in WEIGHTS), 'legacy_not_preweight')
        output.append(dict(race_id=race_id, horse_id=row['horse_id'], race_date=race_date, **features))
    # JSON canonical publication sorts nested feature keys; model input order must be
    # artifact-defined rather than depend on the caller's dict insertion order.
    frame = pd.DataFrame(output, columns=['race_id', 'horse_id', 'race_date', *expected]).sort_values(
        'horse_id', kind='stable').reset_index(drop=True)
    return frame, protocol


def _evidence(kind, protocol, consumed):
    paths = [Path(__file__) if p == 'models.py' else ROOT/p for p in SOURCES]
    return dict(mode=MODE, eligible_for_prospective=False, collection_enabled=False,
        database_access=False, actual_model_inference=True, new_fits=0, kind=kind,
        model_version=protocol['model_versions'][kind], model_sha256=PINNED_SHA[kind],
        num_threads=1, consumed_input=consumed, consumed_input_sha256=digest(consumed),
        model_files={p: s for p, s in protocol['model_files'].items()
            if ('market_ev' in p if kind == 'mev' else '129-candidate-mixture-serving' in p)},
        source_files={str(p.relative_to(ROOT)): sha_bytes(p.read_bytes()) for p in paths})


@lru_cache(maxsize=1)
def _mev():
    model = EnsembleMarketEvModel.load(MEV_DIR, 'mev-ens15-v1')
    boosters, manifest_sha = model.load_members(MEV_DIR/'ensemble_2026.json')
    require(manifest_sha == PINNED_SHA['mev'], 'mev_manifest_changed')
    return model, boosters


@lru_cache(maxsize=1)
def _legacy():
    bundle = load_mixture_bundle(LEGACY_BUNDLE, expected_sha256=PINNED_SHA['legacy'])
    require(all(m.model.booster.params.get('num_threads') == 1 for m in bundle.members),
        'legacy_thread_configuration_changed')
    return bundle


def infer_mev(rows, *, race_id, race_date):
    """Run every saved MEV booster on the explicitly supplied 66 numeric + 11 cat columns."""
    frame, protocol = _input_frame(rows, race_id, race_date, 'mev')
    model, boosters = _mev()
    X = model.design_matrix(frame)
    require(X.shape == (len(rows), 77) and not np.isinf(X).any(), 'invalid_mev_consumed_matrix')
    members = np.vstack([booster.predict(X, num_threads=1) for booster in boosters])
    require(members.shape == (15, len(rows)) and np.isfinite(members).all()
        and ((members > 0) & (members < 1)).all(), 'invalid_mev_member_probability')
    probabilities = np.mean(members, axis=0)
    ids = frame.horse_id.tolist()
    evidence = _evidence('mev', protocol, _frame_evidence(frame))
    encoded = dict(columns=list(model.spec['features'])+list(model.spec['cats']),
        horse_ids=ids, dtype='float32', rows=[[_encode(v) for v in row] for row in X])
    evidence.update(encoded_input=encoded, encoded_input_sha256=digest(encoded),
        member_probabilities={str(seed): {h: float(p) for h, p in zip(ids, vector, strict=True)}
            for seed, vector in enumerate(members, 1)}, aggregation='arithmetic_mean_15')
    result = {h: float(p) for h, p in zip(ids, probabilities, strict=True)}
    evidence['probabilities_sha256'] = digest(result)
    return dict(probabilities=result, evidence=evidence)


def _history(rows, history, race_date):
    require(isinstance(history, list), 'legacy_started_history_required')
    allowed = {'race_id', 'horse_id', 'race_date', 'entry_status'}
    ids = set(rows.horse_id)
    records = []
    for row in history:
        require(isinstance(row, dict) and {'race_id', 'horse_id', 'race_date'} <= set(row)
            and set(row) <= allowed, 'invalid_history_schema')
        require(row['horse_id'] in ids and row.get('entry_status', 'started') == 'started',
            'history_not_target_started')
        require(isinstance(row['race_id'], str) and row['race_id'].isdigit() and len(row['race_id']) == 12,
            'invalid_history_race_id')
        day = dt.date.fromisoformat(row['race_date'])
        require(dt.date(2007, 1, 1) <= day < dt.date.fromisoformat(race_date), 'history_not_strict_past')
        records.append(dict(race_id=row['race_id'], horse_id=row['horse_id'], race_date=day,
            entry_status='started'))
    frame = pd.DataFrame(records, columns=['race_id', 'horse_id', 'race_date', 'entry_status'])
    require(not frame.duplicated(['race_id', 'horse_id']).any(), 'duplicate_history')
    require(not frame.duplicated(['horse_id', 'race_date']).any(), 'duplicate_started_day')
    frame = frame.sort_values(['horse_id', 'race_date', 'race_id'], kind='stable').reset_index(drop=True)
    require(pd.to_numeric(rows.career_starts, errors='coerce').notna().all(), 'history_count_unknown')
    try:
        assert_history_matches_features(rows, frame)
    except ServingError as exc:
        raise ValueError(str(exc)) from exc
    for r in rows.itertuples():
        previous = frame.loc[frame.horse_id.eq(r.horse_id), 'race_date']
        if previous.empty:
            require(pd.isna(r.days_since_last), 'history_gap_mismatch')
        else:
            gap = (dt.date.fromisoformat(race_date)-previous.max()).days
            require(float(r.days_since_last) == gap, 'history_gap_mismatch')
    return frame


def infer_legacy(rows, *, race_id, race_date, history):
    """Run pinned calibrated/corrected six-member preweight; history is never reconstructed."""
    frame, protocol = _input_frame(rows, race_id, race_date, 'legacy')
    history_frame = _history(frame, history, race_date)
    bundle = _legacy()
    official = predict_mixture(bundle, race_id, frame, history_frame, regime='preweight')
    prepared, _ = prepare_race_inputs(frame, race_id, 'preweight')
    ids = prepared.horse_id.tolist()
    correction = build_correction_inputs(prepared, history_frame)
    traces, corrected = {}, []
    for member in bundle.members:
        base, encoded = predict_base(member.model, prepared)
        base_win = np.array([base[h].win for h in ids])
        prediction = correct_member_predictions(ids, base_win, correction, member.terms, member.coefficients)
        corrected.append(prediction)
        encoded_evidence = _frame_evidence(encoded)
        traces[member.id] = dict(base_probabilities={h: float(p) for h, p in zip(ids, base_win, strict=True)},
            corrected_heads={h: [float(prediction[h].win), float(prediction[h].top2), float(prediction[h].top3)] for h in ids},
            encoded_input=encoded_evidence, encoded_input_sha256=digest(encoded_evidence))
    reproduced = average_for_profile(bundle.manifest['profile'], corrected,
        [m['weight'] for m in bundle.manifest['members']])
    probabilities = {h: float(official.predictions[h].win) for h in ids}
    require(all(math.isclose(reproduced[h].win, probabilities[h], rel_tol=0, abs_tol=1e-14) for h in ids),
        'legacy_product_path_parity_failed')
    consumed = _frame_evidence(official.inputs)
    history_evidence = _frame_evidence(history_frame)
    evidence = _evidence('legacy', protocol, consumed)
    evidence.update(started_history=history_evidence, started_history_sha256=digest(history_evidence),
        history_input_contract='explicit_started_only_since_2007_strict_past',
        correction_input=_frame_evidence(correction), members=traces,
        aggregation='arithmetic_mean_6_corrected', product_audit=official.audit,
        probabilities_sha256=digest(probabilities))
    canonical(evidence)
    return dict(probabilities=probabilities, evidence=evidence)


def _encoded_without_prediction(model, rows):
    """The pure category/target-encoder part of predict_base; never runs a booster."""
    data = rows.copy()
    for column in model.categorical_cols:
        data[column] = data[column].astype('category')
    for column in model.feature_cols:
        if column not in model.categorical_cols and column not in model.encoders:
            data[column] = pd.to_numeric(data[column], errors='coerce')
    encoded = {column: encoder.transform(data[column]) for column, encoder in model.encoders.items()}
    return apply_encoded_columns(data[model.feature_cols].copy(), encoded, model.feature_cols)


def validate_evidence_binding(kind, prediction, *, race_id, race_date):
    """Validate saved typed inputs, probability/member traces and pins without inference.

    A separate replay must run the actual model again to establish output reproducibility.
    Hashes alone are not proof of model execution against hostile evidence fabrication.
    """
    require(kind in ('mev', 'legacy'), 'unknown_model_kind')
    rows = [dict(horse_id=r['horse_id'], horse_number=r['horse_number'], features=r['features'])
        for r in prediction['rows']]
    frame, protocol = _input_frame(rows, race_id, race_date, kind)
    evidence = prediction['evidence']
    probabilities = {r['horse_id']: float(r['probability']) for r in prediction['rows']}
    require(all(math.isfinite(p) and 0 < p < 1 for p in probabilities.values()), 'invalid_bound_probability')
    require(evidence['probabilities_sha256'] == digest(probabilities), 'bound_probability_sha_mismatch')
    if kind == 'legacy':
        history = evidence['started_history']
        require(evidence['started_history_sha256'] == digest(history), 'bound_history_sha_mismatch')
        require(evidence['history_input_contract'] == 'explicit_started_only_since_2007_strict_past',
            'bound_history_contract_mismatch')
        history_rows = [dict(zip(history['columns'], values, strict=True)) for values in history['rows']]
        history_frame = _history(frame, history_rows, race_date)
        require(history == _frame_evidence(history_frame), 'bound_history_matrix_mismatch')
        prepared, _ = prepare_race_inputs(frame, race_id, 'preweight')
        correction = build_correction_inputs(prepared, history_frame)
        require(evidence['correction_input'] == _frame_evidence(correction), 'bound_correction_input_mismatch')
        consumed = _frame_evidence(prepared)
    else:
        consumed = _frame_evidence(frame)
    for key, expected in _evidence(kind, protocol, consumed).items():
        require(evidence.get(key) == expected, 'bound_evidence_mismatch:'+key)
    ids = frame.horse_id.tolist()
    if kind == 'mev':
        # Matrix encoding reads only the pinned categorical mapping; no booster execution.
        spec = json.loads((MEV_DIR/'ensemble.spec.json').read_text())
        model = EnsembleMarketEvModel(MEV_DIR, spec, 'mev-ens15-v1')
        X = model.design_matrix(frame)
        encoded = dict(columns=spec['features']+spec['cats'], horse_ids=ids, dtype='float32',
            rows=[[_encode(v) for v in row] for row in X])
        require(evidence['encoded_input'] == encoded and evidence['encoded_input_sha256'] == digest(encoded),
            'bound_mev_encoded_matrix_mismatch')
        require(evidence['aggregation'] == 'arithmetic_mean_15'
            and set(evidence['member_probabilities']) == {str(i) for i in range(1, 16)}, 'bound_mev_member_set')
        values = np.array([[evidence['member_probabilities'][str(i)][h] for h in ids]
            for i in range(1, 16)], dtype=float)
        require(np.isfinite(values).all() and ((values > 0) & (values < 1)).all(), 'bound_member_probability_invalid')
        mean = np.mean(values, axis=0)
    else:
        bundle = _legacy()
        require(evidence['aggregation'] == 'arithmetic_mean_6_corrected'
            and set(evidence['members']) == {m.id for m in bundle.members}, 'bound_legacy_member_set')
        corrected = []
        for member in bundle.members:
            trace = evidence['members'][member.id]
            x = _frame_evidence(_encoded_without_prediction(member.model, prepared))
            require(trace['encoded_input'] == x and trace['encoded_input_sha256'] == digest(x),
                'bound_legacy_encoded_matrix_mismatch')
            require(set(trace['base_probabilities']) == set(ids), 'bound_legacy_base_field_mismatch')
            base = np.array([trace['base_probabilities'][h] for h in ids], dtype=float)
            result = correct_member_predictions(ids, base, correction, member.terms, member.coefficients)
            expected = {h: [float(result[h].win), float(result[h].top2), float(result[h].top3)] for h in ids}
            require(trace['corrected_heads'] == expected, 'bound_legacy_corrected_heads_mismatch')
            corrected.append(result)
        expected = average_for_profile(bundle.manifest['profile'], corrected,
            [m['weight'] for m in bundle.manifest['members']])
        mean = np.array([expected[h].win for h in ids])
    require(all(math.isclose(float(p), probabilities[h], rel_tol=0, abs_tol=1e-14)
        for h, p in zip(ids, mean, strict=True)), 'bound_member_mean_mismatch')
    return True
