"""File-only adapters for authentic saved real-race feature components.

A capture component is not a prospective paired judgment: the preserved 133 material has
old-model inputs and limited prestart checks, but no MEV matrix or durable-ready receipt.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from scripts.roi_explore.prospective_rehearsal_162.contract import canonical, digest, field, require, time

ROOT = Path(__file__).resolve().parents[3]
CAPTURES = ROOT/'artifacts/133-prospective-paired-comparison/records/capture'
PIN_INDEX = ROOT/'specs/160-saved-timepoint-expansion/evidence/saved-model-inventory.json'
PIN_RECEIPT = ROOT/'specs/160-saved-timepoint-expansion/evidence/research-receipt.json'
BUNDLE = ROOT/'artifacts/129-candidate-mixture-serving/bundle.json'
BUNDLE_SHA = '28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pins():
    receipt = json.loads(PIN_RECEIPT.read_text())['files']
    require(sha(PIN_INDEX) == receipt[str(PIN_INDEX.relative_to(ROOT))], 'capture_pin_index_changed')
    return json.loads(PIN_INDEX.read_text())['sources']


def _frame_sha(frame):
    values = frame[sorted(frame.columns)].map(lambda value: repr(value)
        if not (isinstance(value, float) and math.isnan(value)) else 'nan')
    return hashlib.sha256(pd.util.hash_pandas_object(values, index=False).values.tobytes()).hexdigest()


def decode_inputs(record):
    """Recreate the declared original typed input only; never invent absent columns."""
    regime = record['regimes']['preweight']
    inputs = regime['inputs']
    require(set(inputs) == {'columns', 'dtypes', 'rows'}, 'capture_input_schema')
    columns = inputs['columns']
    require(isinstance(columns, list) and len(columns) == len(set(columns))
        and set(inputs['dtypes']) == set(columns) and 'horse_id' not in columns,
        'capture_input_columns_invalid')
    require(isinstance(inputs['rows'], dict) and list(inputs['rows']) == regime['started_ids'],
        'capture_started_order_mismatch')
    require(len(inputs['rows']) == regime['input_identity']['n_rows'], 'capture_input_count_mismatch')
    for values in inputs['rows'].values():
        require(isinstance(values, list) and len(values) == len(columns), 'capture_input_row_incomplete')
    frame = pd.DataFrame([dict(zip(columns, values, strict=True), horse_id=horse_id)
        for horse_id, values in inputs['rows'].items()])
    # 133 encode_rows omitted horse_id, originally the second column. This is verified below.
    original_columns = [columns[0], 'horse_id', *columns[1:]]
    require(hashlib.sha256('\n'.join(original_columns).encode()).hexdigest()
        == regime['input_identity']['columns_sha256'], 'capture_input_column_digest_mismatch')
    require('race_date' in columns and inputs['dtypes']['race_date'] == 'object',
        'capture_date_representation_unknown')
    for name, dtype in inputs['dtypes'].items():
        require(dtype in ('str', 'object', 'float64', 'int64', 'bool'), 'capture_dtype_unsupported')
        if name == 'race_date':
            frame[name] = pd.to_datetime(frame[name], errors='raise').dt.date
        elif dtype != 'object':
            frame[name] = frame[name].astype(dtype)
    frame = frame[original_columns]
    require(_frame_sha(frame) == regime['input_identity']['sha256'], 'capture_input_digest_mismatch')
    return frame


def decode_history(record):
    saved = record['regimes']['preweight']['history']
    require(set(saved) == {'n_rows', 'rows', 'sha256'}, 'capture_history_schema')
    require(isinstance(saved['rows'], list) and len(saved['rows']) == saved['n_rows'], 'capture_history_count')
    require(all(isinstance(row, list) and len(row) == 3 for row in saved['rows']), 'capture_history_row_schema')
    frame = pd.DataFrame(saved['rows'], columns=['race_id', 'horse_id', 'race_date'])
    frame['race_date'] = pd.to_datetime(frame['race_date'], errors='raise')
    require(_frame_sha(frame) == saved['sha256'], 'capture_history_digest_mismatch')
    require(not frame.duplicated(['race_id', 'horse_id']).any(), 'capture_history_duplicate')
    require(frame.horse_id.isin(record['regimes']['preweight']['started_ids']).all(), 'capture_history_other_horse')
    require(frame.race_date.lt(pd.Timestamp(record['race_day'])).all()
        and frame.race_date.ge(pd.Timestamp('2007-01-01')).all(), 'capture_history_not_strict_past')
    return [dict(race_id=r.race_id, horse_id=r.horse_id,
        race_date=r.race_date.date().isoformat(), entry_status='started') for r in frame.itertuples()]


def load_capture(path, *, expected_sha=None, decision_at=None):
    """Load a pinned 133 capture without changing any original clock.

    ``feature_available_at`` means observed acquisition completion, NOT durable storage.
    ``historical_durable_available_at`` is explicitly unknown. The optional cutoff rejects
    material whose acquisition/prediction/record timestamps cross it.
    """
    path = Path(path).resolve()
    require(path.is_relative_to(ROOT), 'capture_outside_workspace')
    relative = str(path.relative_to(ROOT))
    expected = _pins().get(relative) if expected_sha is None else expected_sha
    require(isinstance(expected, str) and sha(path) == expected, 'capture_source_sha_mismatch')
    record = json.loads(path.read_text())
    require(record['artifact_kind'] == 'paired_prospective_record' and record['source'] == 'capture'
        and record['classification'] == 'prospective', 'capture_origin_unverified')
    require(record['can_adopt'] is False and record['eligible_for_verdict'] is False,
        'capture_not_research')
    require(sha(BUNDLE) == BUNDLE_SHA and record['comparators']['new']['bundle_manifest_sha256'] == BUNDLE_SHA,
        'capture_model_identity_mismatch')
    contract = record['comparators']['input_contract']
    require(contract['feature_version'] == 'features-021' and contract['n_columns'] == 138,
        'capture_feature_contract_mismatch')
    timeline = record['timeline']
    require(timeline['clock'] == 'real', 'capture_not_real_clock')
    captured, acquired, computed, checked, written, post = map(time, (
        timeline['acquisition_started_at'], timeline['acquisition_finished_at'],
        record['regimes']['preweight']['predicted_at']['new'], timeline['post_prediction_check_at'],
        record['record_written_at'], record['scheduled_start']))
    require(captured <= acquired <= computed <= checked <= written < post, 'capture_clock_order')
    require(record['input_captured_at'] == timeline['acquisition_started_at']
        and record['predicted_at'] == timeline['post_prediction_check_at'], 'capture_clock_alias_mismatch')
    require(record['pre_result'] is True and type(record['n_result_rows_at_capture']) is int
        and record['n_result_rows_at_capture'] == 0
        and type(record['n_result_rows_after_prediction']) is int and record['n_result_rows_after_prediction'] == 0,
        'capture_results_already_present')
    require(post.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat() == record['race_day'],
        'capture_race_day_mismatch')
    if decision_at is not None:
        cutoff = time(decision_at)
        require(written <= cutoff < post, 'capture_not_available_by_cutoff')
    frame = decode_inputs(record)
    require(frame.race_id.eq(record['race_id']).all()
        and frame.race_date.eq(dt.date.fromisoformat(record['race_day'])).all(), 'capture_row_identity_mismatch')
    expected_columns = json.loads(BUNDLE.read_text())['feature_profile']['full_columns']
    require(set(frame.columns) == {'race_id', 'horse_id', 'race_date', *expected_columns},
        'capture_full_features_missing')
    rows, field_rows = [], []
    raw = record['regimes']['preweight']['inputs']
    for horse_id, values in raw['rows'].items():
        original = dict(zip(raw['columns'], values, strict=True))
        n = original['horse_number']
        require(not isinstance(n, bool) and isinstance(n, (int, float)) and int(n) == n,
            'capture_horse_number_invalid')
        features = {column: original[column] for column in expected_columns}
        require(all(features[w] is None for w in ('weight', 'weight_diff', 'carried_weight_ratio')),
            'capture_not_preweight')
        canonical(features)
        rows.append(dict(horse_id=horse_id, horse_number=int(n), features=features))
        field_rows.append(dict(horse_id=horse_id, horse_number=int(n)))
    field(field_rows)
    require(set(record['regimes']['preweight']['new_research_heads_not_served']) == set(frame.horse_id),
        'capture_prediction_field_mismatch')
    history = decode_history(record)
    result = dict(schema_version=1, mode='saved_capture_component', eligible_for_prospective=False,
        race_id=record['race_id'], race_date=record['race_day'], post_time=record['scheduled_start'],
        field=field_rows, legacy_rows=rows, legacy_history=history,
        feature_available_at=timeline['acquisition_finished_at'],
        legacy_computed_at=record['regimes']['preweight']['predicted_at']['new'],
        record_written_at=record['record_written_at'], historical_durable_available_at=None,
        field_rechecked_after_prediction=False, mev_rows=None,
        original_prediction={h: heads[0] for h, heads in record['regimes']['preweight']['new_research_heads_not_served'].items()},
        source_ref=dict(path=relative, sha256=expected, pin_index=str(PIN_INDEX.relative_to(ROOT)),
            pin_index_sha256=sha(PIN_INDEX)),
        original_input_identity=record['regimes']['preweight']['input_identity'],
        original_history_sha256=record['regimes']['preweight']['history']['sha256'],
        limitations=['no_saved_mev_input_matrix', 'no_historical_durable_ready_receipt',
            'no_post_prediction_started_id_recheck'])
    result['component_sha256'] = digest(result)
    return result


def inventory():
    components = [load_capture(path) for path in sorted(CAPTURES.glob('*.json'))]
    return dict(mode='saved_component_inventory', eligible_for_prospective=False,
        races=len(components), horses=sum(len(c['field']) for c in components),
        legacy_input_verified=sum(bool(c['legacy_rows']) for c in components),
        historical_durable_receipts=0, mev_complete_inputs=0, strict_pairs_eligible=0,
        mev_materialized_tables=mev_matrix_inventory(),
        details=[{k: c[k] for k in ('race_id', 'race_date', 'post_time', 'feature_available_at',
            'legacy_computed_at', 'record_written_at', 'source_ref', 'component_sha256', 'limitations')}
            for c in components])


def mev_matrix_inventory():
    """Schema-only inventory: complete columns do not prove pre-decision availability."""
    import pyarrow.parquet as pq
    spec = json.loads((ROOT/'artifacts/market_ev/mev-ens15-v1/ensemble.spec.json').read_text())
    needed = set(spec['features']+spec['cats'])
    names = (
        'artifacts/roi_explore/rows.parquet',
        'artifacts/market_ev/rows_2007_withp.parquet',
        'artifacts/market_ev/rows_2007.parquet',
        'artifacts/roi_explore/missed_20261004/R02_odds_drift/feats_cand.parquet',
        'artifacts/roi_strict2007_20260930/input/rows.parquet',
        'artifacts/roi_strict2007_20260930/market_models/snapshots/raw.parquet',
        'artifacts/157-verified-roi-comparison/rebuilt/features.parquet',
        'artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet',
    )
    result = []
    for name in names:
        parquet = pq.ParquetFile(ROOT/name)
        schema = parquet.schema_arrow
        cols = set(schema.names)
        clocks = sorted(cols & {'available_at', 'computed_at', 'captured_at', 'created_at',
            'updated_at', 'decision_at', 'odds_observed_at'})
        result.append(dict(path=name, rows=parquet.metadata.num_rows,
            schema_sha256=hashlib.sha256(schema.serialize().to_pybytes()).hexdigest(),
            complete_mev_columns=needed <= cols, missing_mev_columns=sorted(needed-cols),
            availability_clock_columns=clocks, asof_availability_proven=False,
            values_used_for_decision=False, reason='historical_input_availability_unproven'))
    return result
