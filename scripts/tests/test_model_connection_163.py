from __future__ import annotations

import copy
import datetime as dt
import json
import socket

import pytest

from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.model_connection_163.clock import RealClock, verify_observations
from scripts.roi_explore.model_connection_163.quotes import StoppedQuoteAdapter, CollectionDisabled
from scripts.roi_explore.model_connection_163.replay import historical_official_replay
from scripts.roi_explore.model_connection_163.run import connected_bundle, connection_disabled, run, verify
from scripts.roi_explore.model_connection_163.store import EvidenceStore
from scripts.roi_explore.prospective_rehearsal_162.store import read


@pytest.fixture(scope='module')
def protocol():
    return c.make_protocol()


@pytest.fixture(scope='module')
def connected(protocol):
    with connection_disabled():
        return connected_bundle(protocol, RealClock())


def test_actual_model_connection_roundtrip(connected, protocol):
    assert len(protocol['feature_columns']['mev']) == 77
    assert len(protocol['feature_columns']['legacy']) == 138
    restored = json.loads(c.canonical(connected))
    assert c.validate_inputs(restored, protocol) == c.validate_inputs(connected, protocol)
    assert all(p['producer'] == 'actual_model' for p in connected['predictions'].values())


@pytest.mark.parametrize('kind,key,value', [
    ('mev', 'producer', 'synthetic_fixture'),
    ('legacy', 'model_sha256', 'wrong'),
    ('mev', 'model_version', 'unregistered'),
])
def test_wrong_model_identity(connected, protocol, kind, key, value):
    b = copy.deepcopy(connected)
    b['predictions'][kind][key] = value
    with pytest.raises(ValueError):
        c.validate_inputs(b, protocol)


def test_actual_ready_and_seal_with_real_clock(tmp_path, connected, protocol):
    store = EvidenceStore(tmp_path, protocol, RealClock())
    ready = store.prepare(connected)
    guard = dict(at=store.clock.now(), pending=True, field=connected['field'], post_time=connected['post_time'])
    decision = store.seal(connected['race_id'], guard)
    assert c.time(ready['available_by_at']) <= c.time(decision['decision_at']) <= c.time(decision['sealed_at'])
    assert decision['mode'] == c.MODE and decision['simulated_clock'] is False
    assert decision['synthetic_post'] and decision['eligible_for_prospective'] is False
    assert store.verify(connected['race_id']) == decision


def test_second_session_cannot_replace_input(tmp_path, connected, protocol):
    store = EvidenceStore(tmp_path, protocol, RealClock())
    store.prepare(connected)
    b = copy.deepcopy(connected)
    b['session_id'] = 'different'
    with pytest.raises(ValueError, match='immutable_record_conflict'):
        store.prepare(b)


def test_ready_tamper_cannot_leave_admission(tmp_path, connected, protocol):
    store = EvidenceStore(tmp_path, protocol, RealClock())
    store.prepare(connected)
    path = store._race(connected['race_id'])/'ready.json'
    receipt = read(path)
    receipt['protocol_sha256'] = 'wrong'
    path.write_text(json.dumps(receipt))
    guard = dict(at=store.clock.now(), pending=True, field=connected['field'], post_time=connected['post_time'])
    with pytest.raises(ValueError, match='ready_tampered'):
        store.seal(connected['race_id'], guard)
    assert not (path.parent/'accepted.json').exists()


@pytest.mark.parametrize('mode_key,value', [('simulated_clock', True), ('eligible_for_prospective', True),
    ('synthetic_post', False), ('mode', 'live')])
def test_no_promotion_or_simulated_clock(connected, protocol, mode_key, value):
    b = copy.deepcopy(connected)
    b[mode_key] = value
    with pytest.raises(ValueError, match='offline_mode_required'):
        c.validate_inputs(b, protocol)


def test_past_quote_never_becomes_fresh(connected, protocol):
    b = copy.deepcopy(connected)
    old = c.time(b['quote']['received_at'])-dt.timedelta(hours=1)
    b['quote']['received_at'] = old.isoformat()
    b['quote']['fetch_started_at'] = old.isoformat()
    # The provider raw body still reports the newer original time; reject instead of retiming it.
    with pytest.raises(ValueError):
        c.validate_inputs(b, protocol)


def test_mutated_probabilities_or_consumed_inputs(connected, protocol):
    b = copy.deepcopy(connected)
    b['predictions']['mev']['rows'][0]['probability'] *= .9
    with pytest.raises(ValueError, match='bound_probability'):
        c.validate_inputs(b, protocol)
    b = copy.deepcopy(connected)
    b['predictions']['mev']['rows'][0]['features']['distance'] = 999
    with pytest.raises(ValueError, match='bound_evidence_mismatch'):
        c.validate_inputs(b, protocol)


def test_final_field_or_pending_recheck(tmp_path, connected, protocol):
    store = EvidenceStore(tmp_path, protocol, RealClock())
    store.prepare(connected)
    guard = dict(at=store.clock.now(), pending=False, field=connected['field'], post_time=connected['post_time'])
    with pytest.raises(ValueError, match='final_guard_failed'):
        store.seal(connected['race_id'], guard)
    assert not (store._race(connected['race_id'])/'accepted.json').exists()


def test_real_clock_log_and_reversal():
    clock = RealClock()
    first, second = clock.now(), clock.now()
    assert c.time(first) <= c.time(second)
    verify_observations(clock.observations)
    altered = copy.deepcopy(clock.observations)
    altered[-1]['monotonic_ns'] = altered[0]['monotonic_ns']-1
    with pytest.raises(ValueError):
        verify_observations(altered)
    clock.observations[-1]['at'] = (dt.datetime.now(dt.UTC)+dt.timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match='clock_reversed'):
        clock.now()


def test_future_prediction_cannot_be_ready_now(tmp_path, connected, protocol):
    b = copy.deepcopy(connected)
    p = b['predictions']['legacy']
    # Even internally consistent claimed later clocks cannot pass a real-clock save gate.
    later = (dt.datetime.now(dt.UTC)+dt.timedelta(minutes=1)).isoformat()
    p['computed_at'] = later
    p['rows'] = [dict(r, updated_at=later) for r in p['rows']]
    store = EvidenceStore(tmp_path, protocol, RealClock())
    with pytest.raises(ValueError, match='input_from_future'):
        store.prepare(b)


def test_historical_official_roundtrip_preserves_original_clocks():
    result = historical_official_replay()
    assert json.loads(c.canonical(result)) == result
    assert result['races'] == 9 and not result['current_session_linked']
    assert result['original_timestamps_preserved'] and not result['fabricated_past_receipts']
    assert all(a['payout_yen'] == 2590 for a in result['accounts'].values())


def test_transport_always_stopped_and_runtime_guard():
    with pytest.raises(CollectionDisabled):
        StoppedQuoteAdapter(enabled=True)
    with pytest.raises(CollectionDisabled):
        StoppedQuoteAdapter().fetch('any-url', enabled=True)
    with connection_disabled(), pytest.raises(RuntimeError):
        socket.create_connection(('127.0.0.1', 1))
    with connection_disabled(), pytest.raises(RuntimeError):
        import sqlalchemy
        sqlalchemy.create_engine('sqlite://').connect()


def test_full_run_and_verify_with_no_new_collection(tmp_path):
    output = tmp_path/'run'
    with connection_disabled():
        report = run(output)
        verified = verify(output)
    assert report['actual_model_inference'] is True
    assert report['current_settlement'] == 'pending'
    assert report['comparison_data_added'] == 0
    assert verified['status'] == 'verified'
    assert all(v['payout_yen'] is None for v in read(output/'pending-settlement.json')['arms'].values())
    with pytest.raises(FileExistsError):
        run(output)
