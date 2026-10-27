from __future__ import annotations

import hashlib
import json

import pytest

from scripts.roi_explore.asof_connection_164 import sources
from scripts.roi_explore.model_connection_163 import models
from scripts.roi_explore.prospective_rehearsal_162.contract import canonical, digest, time

SOURCE = sources.CAPTURES/'202609040611.json'


def test_real_saved_input_and_history_inner_hashes():
    result = sources.load_capture(SOURCE)
    assert len(result['legacy_rows']) == 16
    assert len(result['legacy_history']) == 340
    assert all(len(row['features']) == 138 for row in result['legacy_rows'])
    assert result['feature_available_at'] == '2026-09-20T01:00:18.308026+00:00'
    assert result['historical_durable_available_at'] is None
    assert not result['field_rechecked_after_prediction']
    assert result['mev_rows'] is None and result['eligible_for_prospective'] is False
    result_hash = result.pop('component_sha256')
    assert digest(result) == result_hash
    assert json.loads(canonical(result)) == result


def test_real_saved_input_reinference_preserves_prediction():
    result = sources.load_capture(SOURCE)
    predicted = models.infer_legacy(result['legacy_rows'], race_id=result['race_id'],
        race_date=result['race_date'], history=result['legacy_history'])
    assert predicted['probabilities'] == pytest.approx(result['original_prediction'], rel=0, abs=1e-14)


def test_all_46_real_captures_validate_without_promotion():
    result = sources.inventory()
    assert (result['races'], result['horses'], result['legacy_input_verified']) == (46, 621, 46)
    assert result['strict_pairs_eligible'] == result['historical_durable_receipts'] == result['mev_complete_inputs'] == 0
    assert all(time(row['feature_available_at']) <= time(row['legacy_computed_at'])
        <= time(row['record_written_at']) < time(row['post_time']) for row in result['details'])
    tables = result['mev_materialized_tables']
    assert sum(t['complete_mev_columns'] for t in tables) == 7
    assert not any(t['asof_availability_proven'] or t['values_used_for_decision'] for t in tables)


@pytest.mark.parametrize('cutoff', ['2026-09-20T01:00:10Z', '2026-09-20T01:00:26.264605Z',
    '2026-09-20T06:30:00Z', '2026-09-20T01:00:27'])
def test_future_or_poststart_or_naive_cutoff_rejected(cutoff):
    with pytest.raises(ValueError):
        sources.load_capture(SOURCE, decision_at=cutoff)


def test_exact_available_cutoff_accepted_as_component_only():
    result = sources.load_capture(SOURCE, decision_at='2026-09-20T01:00:26.264606Z')
    assert result['eligible_for_prospective'] is False
    assert result['historical_durable_available_at'] is None


def test_wrong_source_sha_rejected():
    with pytest.raises(ValueError, match='capture_source_sha_mismatch'):
        sources.load_capture(SOURCE, expected_sha='0'*64)


def record():
    return json.loads(SOURCE.read_text())


@pytest.mark.parametrize('mutation,reason', [
    (lambda r: r['regimes']['preweight']['inputs']['columns'].pop(), 'columns_invalid'),
    (lambda r: r['regimes']['preweight']['inputs']['rows'][next(iter(r['regimes']['preweight']['inputs']['rows']))].pop(), 'row_incomplete'),
    (lambda r: r['regimes']['preweight']['input_identity'].update(sha256='bad'), 'input_digest'),
    (lambda r: r['regimes']['preweight']['inputs']['dtypes'].update(distance='category'), 'dtype_unsupported'),
    (lambda r: r['regimes']['preweight']['started_ids'].reverse(), 'started_order'),
])
def test_input_typed_digest_guards(mutation, reason):
    saved = record()
    mutation(saved)
    with pytest.raises(ValueError, match=reason):
        sources.decode_inputs(saved)


@pytest.mark.parametrize('mutation,reason', [
    (lambda r: r['regimes']['preweight']['history'].update(sha256='bad'), 'history_digest'),
    (lambda r: r['regimes']['preweight']['history'].update(n_rows=1), 'history_count'),
    (lambda r: r['regimes']['preweight']['history']['rows'][0].pop(), 'history_row_schema'),
])
def test_history_inner_digest_guards(mutation, reason):
    saved = record()
    mutation(saved)
    with pytest.raises(ValueError, match=reason):
        sources.decode_history(saved)


def test_canonical_ids_preserved_no_nk_stripping():
    result = sources.load_capture(SOURCE)
    assert any(row['horse_id'].startswith('nk:') for row in result['field'])
    assert {row['horse_id'] for row in result['field']} == {row['horse_id'] for row in result['legacy_rows']}


def test_outside_workspace_source_rejected(tmp_path):
    path = tmp_path/'copy.json'
    path.write_bytes(SOURCE.read_bytes())
    with pytest.raises(ValueError, match='outside_workspace'):
        sources.load_capture(path, expected_sha=hashlib.sha256(path.read_bytes()).hexdigest())
