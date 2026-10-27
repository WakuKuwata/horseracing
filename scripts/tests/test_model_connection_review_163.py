"""Independent fail-closed checks for the offline real-model connection."""
from __future__ import annotations

import hashlib
import json
import socket

import pytest

from scripts.roi_explore.model_connection_163.quotes import (
    CollectionDisabled, QuoteRejected, StoppedQuoteAdapter, freshness_seconds, parse_saved,
)

RACE = '202605040907'
FIELD = [dict(horse_id='canonical-a', horse_number=1), dict(horse_id='nk:canonical-b', horse_number=2)]


def _quote_metadata():
    return dict(race_id=RACE, expected_field=FIELD,
        source_url=f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={RACE}&type=1',
        fetch_started_at='2026-10-27T03:59:59Z', received_at='2026-10-27T04:00:00Z')


def _raw():
    return dict(status='result', data=dict(official_datetime='2026-10-27 13:00:00',
        odds={'1': {'01': ['2.1', '0', '1'], '02': ['5.2', '0', '2']}}))


def test_stopped_adapter_state_mutation_still_cannot_fetch(monkeypatch):
    def no_socket(*args, **kwargs):
        pytest.fail('network adapter constructed a socket')
    monkeypatch.setattr(socket, 'socket', no_socket)
    adapter = StoppedQuoteAdapter()
    adapter.enabled = True
    with pytest.raises(CollectionDisabled):
        adapter.fetch('https://race.netkeiba.com', enabled=True)


def test_saved_quote_not_rebased_or_promoted_on_read(tmp_path):
    raw = json.dumps(_raw(), indent=2).replace('\n', '\r\n')
    path = tmp_path/'raw.json'
    path.write_bytes(raw.encode())
    quote = StoppedQuoteAdapter.from_saved(path, **_quote_metadata())
    assert quote['raw_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert quote['received_at'] == '2026-10-27T04:00:00Z'
    assert quote['source'] == 'saved_response'
    assert quote['eligible_for_prospective'] is False
    with pytest.raises(QuoteRejected):
        freshness_seconds(quote, '2026-10-27T04:30:00.000001Z')


def test_error_payload_with_full_quote_data_is_rejected():
    value = _raw()
    value['status'] = 'error'
    with pytest.raises(QuoteRejected):
        parse_saved(json.dumps(value), **_quote_metadata())


def test_conflicting_explicit_race_id_rejected():
    value = _raw()
    value['data']['race_id'] = '202605040908'
    with pytest.raises(QuoteRejected):
        parse_saved(json.dumps(value), **_quote_metadata())


def test_real_clock_observations_are_current_and_monotonic():
    import datetime as dt
    from scripts.roi_explore.model_connection_163.clock import RealClock, verify_observations
    before = dt.datetime.now(dt.UTC)
    clock = RealClock()
    first, second = clock.now(), clock.now()
    after = dt.datetime.now(dt.UTC)
    assert before <= dt.datetime.fromisoformat(first) <= dt.datetime.fromisoformat(second) <= after
    assert clock.simulated is False
    verify_observations(clock.observations)


def test_real_clock_saved_trace_rejects_epoch_or_wall_reversal():
    import copy
    import datetime as dt
    from scripts.roi_explore.model_connection_163.clock import RealClock, verify_observations
    clock = RealClock()
    clock.now()
    clock.now()
    changed = copy.deepcopy(clock.observations)
    changed[-1]['epoch_id'] = 'different-process'
    with pytest.raises(ValueError):
        verify_observations(changed)
    changed = copy.deepcopy(clock.observations)
    changed[-1]['at'] = (dt.datetime.fromisoformat(changed[0]['at'])-dt.timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        verify_observations(changed)


def test_current_session_cannot_save_future_observed_official_result(tmp_path, monkeypatch):
    import datetime as dt
    from scripts.roi_explore.model_connection_163 import contract as c
    from scripts.roi_explore.model_connection_163.clock import RealClock
    from scripts.roi_explore.model_connection_163.store import EvidenceStore
    from scripts.roi_explore.prospective_rehearsal_162.fixtures import official
    now = dt.datetime.now(dt.UTC)
    post = (now+dt.timedelta(minutes=10)).isoformat()
    field = [dict(horse_id='fixture-a', horse_number=1), dict(horse_id='fixture-b', horse_number=2)]
    decision = dict(mode=c.MODE, simulated_clock=False, eligible_for_prospective=False,
        synthetic_post=True, race_id=RACE, session_id='fixture-session', post_time=post,
        field=field, sealed_at=(now-dt.timedelta(seconds=1)).isoformat(),
        arms={a:dict(budget_yen=1000, tickets=[dict(**field[0], stake_yen=100)]) for a in c.ARMS})
    store = EvidenceStore(tmp_path, c.make_protocol(), RealClock())
    monkeypatch.setattr(store, 'verify', lambda rid: decision)
    decision_file = tmp_path/'races'/RACE/'decision.json'
    decision_file.parent.mkdir(parents=True)
    decision_file.write_text(json.dumps(decision))
    evidence = official(dict(race_id=RACE, field=field, post_time=post))
    with pytest.raises(ValueError):
        store.add_settlement(RACE, evidence)
    assert not (tmp_path/'races'/RACE/'settlements').exists()


@pytest.fixture(scope='module')
def actual_connection():
    from scripts.roi_explore.model_connection_163 import contract as c
    from scripts.roi_explore.model_connection_163.clock import RealClock
    from scripts.roi_explore.model_connection_163.run import connected_bundle, connection_disabled
    protocol = c.make_protocol()
    clock = RealClock()
    with connection_disabled():
        value = connected_bundle(protocol, clock)
    return protocol, value


def test_actual_connection_has_all_members_and_full_inputs(actual_connection):
    from scripts.roi_explore.model_connection_163 import contract as c
    protocol, value = actual_connection
    c.validate_inputs(value, protocol)
    assert len(value['predictions']['mev']['evidence']['member_probabilities']) == 15
    assert len(value['predictions']['legacy']['evidence']['members']) == 6
    assert all(len(r['features']) == 77 for r in value['predictions']['mev']['rows'])
    assert all(len(r['features']) == 138 for r in value['predictions']['legacy']['rows'])
    assert value['quote']['source'] == 'local_fixture'
    assert value['eligible_for_prospective'] is False
    assert value['simulated_clock'] is False


def test_probability_change_is_not_hidden_by_model_sha(actual_connection):
    import copy
    from scripts.roi_explore.model_connection_163 import contract as c
    protocol, original = actual_connection
    value = copy.deepcopy(original)
    value['predictions']['mev']['rows'][0]['probability'] *= .9
    with pytest.raises(ValueError, match='probability_sha'):
        c.validate_inputs(value, protocol)


def test_encoded_matrix_change_cannot_be_hidden_by_rehash(actual_connection):
    import copy
    from scripts.roi_explore.model_connection_163 import contract as c
    protocol, original = actual_connection
    value = copy.deepcopy(original)
    evidence = value['predictions']['mev']['evidence']
    evidence['encoded_input']['rows'][0][0] = 999
    evidence['encoded_input_sha256'] = c.digest(evidence['encoded_input'])
    with pytest.raises(ValueError, match='encoded_matrix'):
        c.validate_inputs(value, protocol)


def test_full_input_change_requires_matching_consumed_evidence(actual_connection):
    import copy
    from scripts.roi_explore.model_connection_163 import contract as c
    protocol, original = actual_connection
    value = copy.deepcopy(original)
    value['predictions']['mev']['rows'][0]['features']['distance'] = 1234
    with pytest.raises(ValueError, match='evidence_mismatch'):
        c.validate_inputs(value, protocol)


def test_old_one_future_row_is_not_available_by_run_time(actual_connection):
    import copy
    from scripts.roi_explore.model_connection_163 import contract as c
    protocol, original = actual_connection
    value = copy.deepcopy(original)
    value['predictions']['legacy']['rows'][0]['updated_at'] = value['post_time']
    with pytest.raises(ValueError, match='row_unavailable'):
        c.validate_inputs(value, protocol)


def test_old_preweight_cannot_acquire_weight_after_actual_inference(actual_connection):
    import copy
    from scripts.roi_explore.model_connection_163 import contract as c
    protocol, original = actual_connection
    value = copy.deepcopy(original)
    value['predictions']['legacy']['rows'][0]['features']['weight'] = 480
    with pytest.raises(ValueError, match='not_preweight'):
        c.validate_inputs(value, protocol)


def test_canonical_json_roundtrip_keeps_actual_consumed_matrix(actual_connection):
    from scripts.roi_explore.model_connection_163 import contract as c
    protocol, original = actual_connection
    restored = json.loads(c.canonical(original))
    c.validate_inputs(restored, protocol)
