"""Bind complete model inputs to original saved source clocks. No DB or imputation."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.model_connection_163 import models
from . import sources

ROOT = Path(__file__).resolve().parents[3]
MODE = 'offline_asof_connection'


def read_ref(reference, prefix='source'):
    name, checksum = reference[prefix+'_path'], reference[prefix+'_sha256']
    path = Path(name)
    c.require(not path.is_absolute() and path.parts and '..' not in path.parts, 'unsafe_source_path')
    full = ROOT
    for part in path.parts:
        full /= part
        c.require(not full.is_symlink(), 'unsafe_source_path')
    c.require(full.is_file() and full.resolve().is_relative_to(ROOT.resolve()), 'source_missing')
    body = full.read_bytes()
    c.require(c.sha_bytes(body) == checksum, 'source_sha_mismatch')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            c.require(key not in out, 'duplicate_source_json_key')
            out[key] = value
        return out
    return full, json.loads(body.decode('utf-8'), object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite_source_json')))


def materialize(reference):
    """Return exact raw rows and availability, retaining the original source identity."""
    if reference['source_kind'] == 'capture133':
        path = Path(reference['source_path'])
        c.require(not path.is_absolute() and '..' not in path.parts, 'unsafe_source_path')
        # Always use the global frozen160 pin. Caller-controlled hashes cannot replace it.
        saved = sources.load_capture(ROOT/path)
        c.require(saved['source_ref']['sha256'] == reference['source_sha256'], 'capture_reference_mismatch')
        result = dict(mode=MODE, eligible_for_prospective=False, kind='legacy',
            race_id=saved['race_id'], race_date=saved['race_date'], field=saved['field'],
            rows=saved['legacy_rows'], history=saved['legacy_history'],
            quote_sha256=None,
            generated_at=None, source_times=None,
            available_by_at=saved['feature_available_at'], durable_available_at=None,
            source_status='recorded_capture_component', source_ref=dict(reference))
    else:
        c.require(reference['source_kind'] == 'feature_snapshot_v1', 'unknown_feature_source')
        _, raw = read_ref(reference)
        c.keys(raw, ['artifact_kind', 'schema_version', 'mode', 'eligible_for_prospective',
            'kind', 'race_id', 'race_date', 'generated_at', 'observed_by_at', 'rows', 'history',
            'source_times', 'row_available_at', 'history_available_at', 'quote_sha256'], 'feature_snapshot')
        c.require(raw['artifact_kind'] == 'model_feature_snapshot_v1' and raw['schema_version'] == 1
            and raw['mode'] == MODE and raw['eligible_for_prospective'] is False, 'feature_snapshot_kind')
        c.require(raw['kind'] in ('mev', 'legacy'), 'feature_kind')
        c.require((raw['kind'] == 'legacy' and raw['quote_sha256'] is None)
            or (raw['kind'] == 'mev' and isinstance(raw['quote_sha256'], str)
                and len(raw['quote_sha256']) == 64), 'feature_quote_binding_missing')
        columns = models.input_columns(raw['kind'])
        c.require(set(raw['source_times']) == set(columns), 'feature_column_clock_missing')
        ids = {r['horse_id'] for r in raw['rows']}
        c.require(set(raw['row_available_at']) == ids, 'feature_row_clock_missing')
        generated, observed = c.time(raw['generated_at']), c.time(raw['observed_by_at'])
        c.require(generated <= observed, 'feature_clock_order')
        for stamp in [*raw['source_times'].values(), *raw['row_available_at'].values(), raw['history_available_at']]:
            c.require(c.time(stamp) <= generated, 'future_feature_dependency')
        _, receipt = read_ref(reference, 'receipt')
        c.keys(receipt, ['artifact_kind', 'mode', 'eligible_for_prospective', 'source_sha256',
            'available_by_at'], 'feature_ready')
        c.require(receipt['artifact_kind'] == 'feature_ready_v1' and receipt['mode'] == MODE
            and receipt['eligible_for_prospective'] is False
            and receipt['source_sha256'] == reference['source_sha256'], 'feature_ready_mismatch')
        durable = c.time(receipt['available_by_at'])
        c.require(observed <= durable, 'feature_ready_before_observation')
        result = dict(mode=MODE, eligible_for_prospective=False, kind=raw['kind'],
            race_id=raw['race_id'], race_date=raw['race_date'],
            field=[dict(horse_id=r['horse_id'], horse_number=r['horse_number']) for r in raw['rows']],
            rows=raw['rows'], history=raw['history'], available_by_at=raw['observed_by_at'],
            quote_sha256=raw['quote_sha256'],
            generated_at=raw['generated_at'], source_times=raw['source_times'],
            durable_available_at=receipt['available_by_at'], source_status='saved_feature_snapshot', source_ref=dict(reference))
    # Pure model-input/history shape validation does not execute a model or query a DB.
    frame, _ = models._input_frame(result['rows'], result['race_id'], result['race_date'], result['kind'])
    if result['kind'] == 'legacy':
        models._history(frame, result['history'], result['race_date'])
    else:
        c.require(result['history'] == [], 'mev_history_must_be_materialized_into_features')
    result['input_sha256'] = c.digest(dict(rows=result['rows'], history=result['history']))
    result['packet_sha256'] = c.digest(result)
    return result


def validate_features(reference, *, kind, race_id, race_date, expected_field, decision_at,
                      require_durable=False):
    packet = materialize(reference)
    c.require(packet['kind'] == kind and packet['race_id'] == race_id and packet['race_date'] == race_date,
        'feature_identity_mismatch')
    c.require(c.field(packet['field']) == c.field(expected_field), 'feature_field_mismatch')
    c.require(c.time(packet['available_by_at']) <= c.time(decision_at), 'features_not_available_by_decision')
    if require_durable:
        c.require(packet['durable_available_at'] is not None, 'historical_feature_durable_receipt_missing')
        c.require(c.time(packet['durable_available_at']) <= c.time(decision_at), 'feature_durable_after_decision')
    return packet
