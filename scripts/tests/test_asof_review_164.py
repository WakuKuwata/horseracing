"""Independent adversarial tests of stored guard facts; no live I/O or historical re-timing."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.roi_explore.asof_connection_164 import guards

RACE = '202605040907'
POST = '2026-10-25T06:00:00+00:00'
DECISION = '2026-10-25T05:50:01+00:00'
FIELD = [{'horse_id': 'fixture-one', 'horse_number': 1}, {'horse_id': 'fixture-two', 'horse_number': 2}]


def _write(path, value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _fixture(tmp_path, monkeypatch, mutate=None, receipt_at='2026-10-25T05:50:00.600000+00:00'):
    monkeypatch.setattr(guards, 'ROOT', tmp_path)
    def query(rows):
        return dict(started_at='2026-10-25T05:50:00+00:00',
            completed_at='2026-10-25T05:50:00.500000+00:00', rows=rows)
    raw = dict(artifact_kind='race_guard_query_evidence_v1', schema_version=1,
        mode='offline_asof_connection', eligible_for_prospective=False, race_id=RACE, stage='decision',
        observations=dict(race=query([dict(race_id=RACE, race_date='2026-10-25', post_time=POST)]),
            entries=query([dict(**r, entry_status='started') for r in FIELD]), results=query([])))
    if mutate:
        mutate(raw)
    checksum = _write(tmp_path/'guard.json', raw)
    receipt = dict(artifact_kind='race_guard_ready_v1', mode='offline_asof_connection',
        eligible_for_prospective=False, source_sha256=checksum, available_by_at=receipt_at)
    receipt_sha = _write(tmp_path/'receipt.json', receipt)
    return dict(source_kind='guard_observation_v1', source_path='guard.json', source_sha256=checksum,
        receipt_path='receipt.json', receipt_sha256=receipt_sha)


def _validate(record, **kwargs):
    params = dict(expected_field=FIELD, race_id=RACE, decision_at=DECISION, post_time=POST, stage='decision')
    params.update(kwargs)
    return guards.validate_guard(record, **params)


def test_complete_query_fixture_is_explicitly_not_historical_prospective(tmp_path, monkeypatch):
    evidence = _validate(_fixture(tmp_path, monkeypatch))
    assert evidence['pending'] is True and evidence['field_reobserved'] is True
    assert evidence['source_status'] == 'complete_saved_query_fixture'
    assert evidence['eligible_for_prospective'] is False


def test_long_running_snapshot_is_not_fresh(tmp_path, monkeypatch):
    def mutate(raw):
        raw['observations']['entries']['started_at'] = '2026-10-25T04:50:00+00:00'
    record = _fixture(tmp_path, monkeypatch, mutate)
    with pytest.raises(ValueError, match='guard_stale_or_future'):
        _validate(record)


@pytest.mark.parametrize('override', [dict(expected_field=[dict(FIELD[0]),dict(horse_id='other',horse_number=2)]),
    dict(race_id='202605040908'), dict(post_time='2026-10-25T06:01:00+00:00'),
    dict(decision_at=POST), dict(stage='after'), dict(stage='unknown'), dict(max_age_seconds=99)])
def test_fact_mismatch_cannot_be_overridden(tmp_path, monkeypatch, override):
    record = _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _validate(record, **override)


def test_results_present_are_not_pending(tmp_path, monkeypatch):
    record = _fixture(tmp_path, monkeypatch,
        lambda raw:raw['observations']['results']['rows'].append(dict(race_id=RACE, horse_id='fixture-one')))
    with pytest.raises(ValueError, match='pending'):
        _validate(record)


def test_future_ready_receipt_is_not_available(tmp_path, monkeypatch):
    record = _fixture(tmp_path, monkeypatch, receipt_at='2026-10-25T05:50:02+00:00')
    with pytest.raises(ValueError, match='not_available'):
        _validate(record)


def test_ready_before_observation_is_rejected(tmp_path, monkeypatch):
    record = _fixture(tmp_path, monkeypatch, receipt_at='2026-10-25T05:49:59+00:00')
    with pytest.raises(ValueError, match='receipt_before'):
        _validate(record)


def test_metadata_cannot_override_source_clock(tmp_path, monkeypatch):
    record = _fixture(tmp_path, monkeypatch)
    record['observed_at'] = '2026-10-25T05:50:01+00:00'
    with pytest.raises(ValueError, match='source_clock_mismatch'):
        _validate(record)


def test_guard_source_and_receipt_tampering(tmp_path, monkeypatch):
    record = _fixture(tmp_path, monkeypatch)
    bad = copy.deepcopy(record)
    bad['source_sha256'] = '0'*64
    with pytest.raises(ValueError, match='sha_mismatch'):
        _validate(bad)
    bad = copy.deepcopy(record)
    bad['source_path'] = '../guard.json'
    with pytest.raises(ValueError, match='path_unsafe'):
        _validate(bad)
    receipt = json.loads((tmp_path/'receipt.json').read_text())
    receipt['source_sha256'] = '0'*64
    record['receipt_sha256'] = _write(tmp_path/'receipt.json', receipt)
    with pytest.raises(ValueError, match='receipt_invalid'):
        _validate(record)


def test_actual_capture_after_has_no_field_reobservation():
    root = Path(__file__).resolve().parents[2]
    path = root/'artifacts/133-prospective-paired-comparison/records/capture/202609040611.json'
    record = dict(source_kind='capture133', source_path=str(path.relative_to(root)),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    before = guards.inspect_guard(record, stage='before')
    after = guards.inspect_guard(record, stage='after')
    assert before['pending'] and before['field_reobserved']
    assert after['pending'] and not after['field_reobserved']
    assert after['available_by_at'] is None
    assert 'field_not_reobserved' in after['incomplete_reasons']
    with pytest.raises(ValueError, match='field_not_reobserved'):
        guards.validate_guard(record, expected_field=before['field'], race_id=before['race_id'],
            decision_at=after['observed_at'], post_time=before['post_time'], stage='after')
    with pytest.raises(ValueError, match='decision_guard_not_saved'):
        guards.validate_guard(record, expected_field=before['field'], race_id=before['race_id'],
            decision_at=after['observed_at'], post_time=before['post_time'], stage='decision')


def _feature_fixture(tmp_path, monkeypatch, mutate=None, receipt_at='2026-10-25T05:49:00+00:00'):
    from scripts.roi_explore.asof_connection_164 import features
    from scripts.roi_explore.model_connection_163 import models
    monkeypatch.setattr(features, 'ROOT', tmp_path)
    cols = models.input_columns('mev')
    rows = []
    for identity in FIELD:
        values = dict.fromkeys(cols)
        values.update(horse_number=identity['horse_number'], field_size=len(FIELD))
        rows.append(dict(**identity, features=values))
    raw = dict(artifact_kind='model_feature_snapshot_v1', schema_version=1,
        mode='offline_asof_connection', eligible_for_prospective=False, kind='mev', race_id=RACE,
        race_date='2026-10-25', generated_at='2026-10-25T05:48:00+00:00',
        observed_by_at='2026-10-25T05:48:01+00:00', rows=rows, history=[],
        source_times={k:'2026-10-25T05:47:00+00:00' for k in cols},
        row_available_at={r['horse_id']:'2026-10-25T05:47:00+00:00' for r in FIELD},
        history_available_at='2026-10-25T05:47:00+00:00', quote_sha256='0'*64)
    if mutate:
        mutate(raw)
    checksum = _write(tmp_path/'features.json', raw)
    receipt = dict(artifact_kind='feature_ready_v1', mode='offline_asof_connection',
        eligible_for_prospective=False, source_sha256=checksum, available_by_at=receipt_at)
    receipt_sha = _write(tmp_path/'feature-receipt.json', receipt)
    return dict(source_kind='feature_snapshot_v1', source_path='features.json', source_sha256=checksum,
        receipt_path='feature-receipt.json', receipt_sha256=receipt_sha)


def test_feature_origin_may_precede_quote_without_retiming(tmp_path, monkeypatch):
    from scripts.roi_explore.asof_connection_164 import features
    packet = features.validate_features(_feature_fixture(tmp_path, monkeypatch), kind='mev', race_id=RACE,
        race_date='2026-10-25', expected_field=FIELD, decision_at=DECISION, require_durable=True)
    assert packet['available_by_at'] == '2026-10-25T05:48:01+00:00'
    assert packet['durable_available_at'] == '2026-10-25T05:49:00+00:00'


@pytest.mark.parametrize('mutate', [
    lambda raw:raw['source_times'].update(distance='2026-10-25T05:49:00+00:00'),
    lambda raw:raw['row_available_at'].update({'fixture-one':'2026-10-25T05:49:00+00:00'}),
    lambda raw:raw.update(history_available_at='2026-10-25T05:49:00+00:00'),
    lambda raw:raw['source_times'].pop('distance'),
    lambda raw:raw['rows'][0]['features'].pop('distance'),
])
def test_feature_future_dependencies_or_missing_columns(tmp_path, monkeypatch, mutate):
    from scripts.roi_explore.asof_connection_164 import features
    with pytest.raises(ValueError):
        features.materialize(_feature_fixture(tmp_path, monkeypatch, mutate))


def test_feature_future_durable_receipt_is_not_available(tmp_path, monkeypatch):
    from scripts.roi_explore.asof_connection_164 import features
    reference = _feature_fixture(tmp_path, monkeypatch, receipt_at='2026-10-25T05:50:02+00:00')
    with pytest.raises(ValueError, match='durable_after_decision'):
        features.validate_features(reference, kind='mev', race_id=RACE, race_date='2026-10-25',
            expected_field=FIELD, decision_at=DECISION, require_durable=True)


def test_real_legacy_capture_is_component_not_durable_pair():
    from scripts.roi_explore.asof_connection_164 import features, sources
    root = Path(__file__).resolve().parents[2]
    path = root/'artifacts/133-prospective-paired-comparison/records/capture/202609040611.json'
    component = sources.load_capture(path)
    reference = dict(source_kind='capture133', source_path=str(path.relative_to(root)),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    packet = features.validate_features(reference, kind='legacy', race_id=component['race_id'],
        race_date=component['race_date'], expected_field=component['field'],
        decision_at=component['record_written_at'])
    assert len(packet['rows']) == 16 and len(packet['history']) == 340
    assert packet['durable_available_at'] is None
    with pytest.raises(ValueError, match='durable_receipt_missing'):
        features.validate_features(reference, kind='legacy', race_id=component['race_id'],
            race_date=component['race_date'], expected_field=component['field'],
            decision_at=component['record_written_at'], require_durable=True)


def _rewrite_ref(root, reference, prefix, mutate):
    path = root/reference[prefix+'_path']
    raw = json.loads(path.read_text())
    mutate(raw)
    reference[prefix+'_sha256'] = _write(path, raw)
    return raw


def test_complete_fixture_future_prediction_receipt_is_rejected(tmp_path, monkeypatch):
    import datetime as dt
    from scripts.roi_explore.asof_connection_164 import contract
    from scripts.roi_explore.model_connection_163 import contract as core
    from scripts.tests.support.asof_fixture_164 import complete_candidate
    root = tmp_path/'inputs'
    candidate, _ = complete_candidate(root, monkeypatch)
    reference = candidate['predictions']['legacy']
    future = (core.time(candidate['decision_at'])+dt.timedelta(microseconds=1)).isoformat()
    _rewrite_ref(root, reference, 'receipt', lambda raw:raw.update(available_by_at=future))
    result = contract.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['reason'] == 'prediction_not_durably_saved_before_decision' for r in result['failures'])


def test_matching_quote_sha_does_not_allow_generation_before_quote(tmp_path, monkeypatch):
    import datetime as dt
    from scripts.roi_explore.asof_connection_164 import contract
    from scripts.roi_explore.model_connection_163 import contract as core
    from scripts.tests.support.asof_fixture_164 import complete_candidate
    root = tmp_path/'inputs'
    candidate, _ = complete_candidate(root, monkeypatch)
    quote_meta = json.loads((root/candidate['quote']['metadata_path']).read_text())
    before = (core.time(quote_meta['received_at'])-dt.timedelta(microseconds=1)).isoformat()
    reference = candidate['features']['mev']
    def early(raw):
        raw.update(generated_at=before, history_available_at=before)
        raw['source_times'] = dict.fromkeys(raw['source_times'], before)
        raw['row_available_at'] = dict.fromkeys(raw['row_available_at'], before)
    _rewrite_ref(root, reference, 'source', early)
    _rewrite_ref(root, reference, 'receipt', lambda raw:raw.update(source_sha256=reference['source_sha256']))
    result = contract.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['reason'] == 'quote_after_mev_generation' for r in result['failures'])
