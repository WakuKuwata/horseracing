from __future__ import annotations

import copy
import datetime as dt
import json
import socket
import subprocess

import pytest

from scripts.roi_explore.prospective_rehearsal_162 import contract as c
from scripts.roi_explore.prospective_rehearsal_162 import fixtures as f
from scripts.roi_explore.prospective_rehearsal_162 import store as storage
from scripts.roi_explore.prospective_rehearsal_162.rehearsal import offline_only, run, verify
from scripts.roi_explore.prospective_rehearsal_162.settlement import settle


@pytest.fixture(scope='module')
def protocol():
    return c.make_protocol()


@pytest.fixture
def bundle(protocol):
    return f.bundle(protocol)


def prepared(tmp_path, protocol, bundle):
    clock = storage.SimulatedClock('2026-10-25T05:49:00Z')
    store = storage.EvidenceStore(tmp_path/'evidence', protocol, clock)
    ready = store.prepare(bundle)
    return store, clock, ready


def sealed(tmp_path, protocol, bundle):
    store, clock, _ = prepared(tmp_path, protocol, bundle)
    clock.advance_to('2026-10-25T05:50:01Z')
    decision = store.seal(bundle['race_id'], f.final_guard(bundle))
    return store, decision


@pytest.mark.parametrize('mutation,reason', [
    (lambda b: b.update(race_id=None), 'invalid_race_id'),
    (lambda b: b.update(session_id=42), 'invalid_session_id'),
    (lambda b: b.update(closing_odds=[1, 2, 3]), 'invalid_bundle_schema'),
    (lambda b: b['quote'].update(source='unknown'), 'offline_quote_adapter_required'),
    (lambda b: b['quote'].update(cache='hit'), 'offline_quote_adapter_required'),
    (lambda b: b['quote'].update(raw_text='{}'), 'quote_raw_sha_mismatch'),
    (lambda b: b['quote'].update(received_at=b['post_time']), 'quote_time_order'),
    (lambda b: b['quote'].update(received_at='2026-10-25T05:45:01'), 'timestamp_timezone'),
    (lambda b: b['predictions']['mev'].update(model_sha256='bad'), 'model_identity'),
    (lambda b: b['predictions']['mev'].update(computed_at=b['post_time']), 'prediction_time'),
    (lambda b: b['predictions']['legacy']['rows'].pop(), 'prediction_field'),
    (lambda b: b['predictions']['legacy']['rows'][0].update(probability=.9), 'legacy_not_normalized'),
    (lambda b: b['predictions']['legacy']['rows'][0]['features'].update(weight=480), 'not_preweight'),
    (lambda b: b['predictions']['mev']['rows'][0]['features'].pop('distance'), 'incomplete_input'),
    (lambda b: b['predictions']['mev']['rows'][0]['features'].update(odds=999), 'mev_quote_input'),
    (lambda b: b['predictions']['mev']['rows'][0]['features'].update(q=.99), 'market_feature'),
    (lambda b: b['predictions']['legacy']['rows'][0].update(updated_at=b['post_time']), 'row_unavailable'),
    (lambda b: b['predictions']['legacy']['rows'][0].update(input_asof=b['post_time']), 'future_input'),
    (lambda b: b['predictions']['legacy']['rows'][0].update(probability=float('nan')), 'invalid_number'),
    (lambda b: b.update(eligible_for_prospective=True), 'offline_mode'),
])
def test_invalid_evidence_rejected(bundle, protocol, mutation, reason):
    mutation(bundle)
    with pytest.raises(ValueError, match=reason):
        c.validate_inputs(bundle, protocol)


@pytest.mark.parametrize('timestamp', ['2026-10-25T05:50:00.000000001Z',
    '2026-10-25T05:50:00', '2026-10-25T05:50:00.1234567Z'])
def test_no_silent_timestamp_precision_loss(timestamp):
    with pytest.raises(ValueError):
        c.time(timestamp)


def test_mev_binary_vector_not_forced_to_one(bundle, protocol):
    assert sum(r['probability'] for r in bundle['predictions']['mev']['rows']) != 1
    c.validate_inputs(bundle, protocol)


@pytest.mark.parametrize('age,accepted', [(0, True), (1800, True), (1800.000001, False)])
def test_exact_freshness_gate(bundle, protocol, age, accepted):
    at = c.time('2026-10-25T05:50:01Z')
    quote = at-dt.timedelta(seconds=age)
    bundle['quote'].update(fetch_started_at=quote.isoformat(), received_at=quote.isoformat())
    bundle['guard_before']['at'] = quote.isoformat()
    for p in bundle['predictions'].values():
        p.update(input_available_at=quote.isoformat(), started_at=quote.isoformat(), computed_at=at.isoformat())
        for r in p['rows']:
            r.update(input_asof=quote.isoformat(), created_at=quote.isoformat(), updated_at=at.isoformat())
    if accepted:
        c.gate(bundle, f.final_guard(bundle), at.isoformat(), at.isoformat(), protocol)
    else:
        with pytest.raises(ValueError, match='stale_quote'):
            c.gate(bundle, f.final_guard(bundle), at.isoformat(), at.isoformat(), protocol)


@pytest.mark.parametrize('at,allowed', [('2026-10-25T05:49:59.999999Z', False),
    ('2026-10-25T05:50:00Z', True), ('2026-10-25T05:51:00Z', False), ('2026-10-25T06:00:00Z', False)])
def test_window_boundaries(bundle, protocol, at, allowed):
    guard = dict(f.final_guard(bundle), at=at)
    if allowed:
        c.gate(bundle, guard, '2026-10-25T05:49:00Z', at, protocol)
    else:
        with pytest.raises(ValueError, match='outside_decision_window'):
            c.gate(bundle, guard, '2026-10-25T05:49:00Z', at, protocol)


def test_ready_receipt_after_real_publication_order(tmp_path, protocol, bundle, monkeypatch):
    events = []
    real_publish = storage.publish
    def observe(path, value):
        result = real_publish(path, value)
        events.append(path.name)
        return result
    monkeypatch.setattr(storage, 'publish', observe)
    store, clock, ready = prepared(tmp_path, protocol, bundle)
    assert events.index('inputs.json') < events.index('ready.json')
    assert c.time(ready['available_by_at']) > c.time(bundle['predictions']['legacy']['computed_at'])
    clock.advance_to('2026-10-25T05:50:01Z')
    store.seal(bundle['race_id'], f.final_guard(bundle))
    assert events.index('decision.json') < events.index('seal.json') < events.index('accepted.json')


def test_duplicate_and_conflicting_rerun(tmp_path, protocol, bundle):
    store, decision = sealed(tmp_path, protocol, bundle)
    assert store.seal(bundle['race_id'], f.final_guard(bundle)) == decision
    assert store.prepare(bundle)['input_sha256'] == c.digest(bundle)
    different = copy.deepcopy(bundle)
    different['session_id'] = 'replacement'
    with pytest.raises(ValueError, match='immutable_record_conflict'):
        store.prepare(different)


def test_ready_missing_recovery_uses_new_ack(tmp_path, protocol, bundle, monkeypatch):
    clock = storage.SimulatedClock('2026-10-25T05:49:00Z')
    store = storage.EvidenceStore(tmp_path/'evidence', protocol, clock)
    real_publish = storage.publish
    def crash(path, value):
        if path.name == 'ready.json':
            raise OSError('simulated process failure')
        return real_publish(path, value)
    monkeypatch.setattr(storage, 'publish', crash)
    with pytest.raises(OSError):
        store.prepare(bundle)
    assert not (store._race(bundle['race_id'])/'ready.json').exists()
    monkeypatch.setattr(storage, 'publish', real_publish)
    clock.advance_to('2026-10-25T05:49:10Z')
    assert c.time(store.prepare(bundle)['available_by_at']) >= c.time('2026-10-25T05:49:10Z')


def test_seal_crash_never_admitted_and_abort_removes_full_quote(tmp_path, protocol, bundle, monkeypatch):
    store, clock, _ = prepared(tmp_path, protocol, bundle)
    clock.advance_to('2026-10-25T05:50:01Z')
    real_publish = storage.publish
    def crash(path, value):
        if path.name == 'seal.json':
            raise OSError('simulated process failure')
        return real_publish(path, value)
    monkeypatch.setattr(storage, 'publish', crash)
    with pytest.raises(OSError):
        store.seal(bundle['race_id'], f.final_guard(bundle))
    assert not (store._race(bundle['race_id'])/'accepted.json').exists()
    with pytest.raises(ValueError, match='incomplete_seal'):
        store.seal(bundle['race_id'], f.final_guard(bundle))
    monkeypatch.setattr(storage, 'publish', real_publish)
    store.abort(bundle['race_id'], 'interrupted_seal')
    assert not (store._race(bundle['race_id'])/'inputs.json').exists()
    with pytest.raises(ValueError, match='session_already_aborted'):
        store.prepare(bundle)


def test_seal_completion_crosses_window_is_not_accepted(tmp_path, protocol, bundle):
    store, clock, _ = prepared(tmp_path, protocol, bundle)
    clock.advance_to('2026-10-25T05:50:59.999999Z')
    guard = dict(f.final_guard(bundle), at='2026-10-25T05:50:59Z')
    with pytest.raises(ValueError, match='outside_decision_window'):
        store.seal(bundle['race_id'], guard)
    assert not (store._race(bundle['race_id'])/'accepted.json').exists()


def test_readback_tamper_rejected(tmp_path, protocol, bundle):
    store, _ = sealed(tmp_path, protocol, bundle)
    path = store._race(bundle['race_id'])/'inputs.json'
    value = storage.read(path)
    value['predictions']['legacy']['rows'][0]['probability'] = .9
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        store.verify(bundle['race_id'])


def test_clock_reversal():
    clock = storage.SimulatedClock('2026-10-25T05:50:01Z')
    clock.now()
    clock.value -= dt.timedelta(seconds=1)
    with pytest.raises(ValueError, match='clock_reversed'):
        clock.now()


@pytest.mark.parametrize('probe', [lambda: socket.create_connection(('127.0.0.1', 1)),
    lambda: socket.getaddrinfo('example.com', 443), lambda: subprocess.Popen(['false'])])
def test_network_subprocess_blocked(probe):
    with offline_only(), pytest.raises(RuntimeError, match='disabled'):
        probe()


def test_synthetic_cli_and_manifest(tmp_path):
    destination = tmp_path/'run'
    with offline_only():
        run(destination)
        verify(destination)
    report = storage.read(destination/'report.json')
    assert (report['accepted'], report['rejected']) == (3, 8)
    assert report['historical_inventory']['complete_contract_accepted'] == 0
    assert report['eligible_for_prospective'] is False
    with pytest.raises(FileExistsError):
        run(destination)


@pytest.mark.parametrize('kind,status', [('normal', 'settled'), ('missing', 'incomplete'),
    ('pending', 'pending'), ('dead_heat', 'settled')])
def test_official_settlement(tmp_path, protocol, bundle, kind, status):
    store, decision = sealed(tmp_path, protocol, bundle)
    evidence = f.official(bundle, missing=kind == 'missing', pending=kind == 'pending',
        winners={1: 150, 2: 280} if kind == 'dead_heat' else None)
    original = copy.deepcopy(decision)
    result = store.add_settlement(bundle['race_id'], evidence)
    assert result['status'] == status
    assert decision == original
    for arm in c.ARMS:
        account = result['arms'][arm]
        if status != 'settled':
            assert account['payout_yen'] is account['profit_yen'] is None
        else:
            dividends = {r['horse_number']: r['payout_yen'] for r in evidence['payouts']}
            assert account['payout_yen'] == sum(dividends.get(t['horse_number'], 0)
                for t in decision['arms'][arm]['tickets'])
    assert result['primary_eligible'] == (status == 'settled' and kind != 'dead_heat')
    assert store.verify_settlements(bundle['race_id']) == [result]


@pytest.mark.parametrize('mutate', [
    lambda e: e.update(raw_sha256='bad'),
    lambda e: e['payouts'][0].update(payout_yen=999),
    lambda e: e['payouts'].append(copy.deepcopy(e['payouts'][0])),
    lambda e: e.update(source='closing_odds'),
    lambda e: e.update(race_id='202605040999'),
    lambda e: e.update(observed_at='2026-10-25T05:55:00Z'),
    lambda e: e['results'][0].update(finish_order=True),
])
def test_bad_settlement_unvalued(tmp_path, protocol, bundle, mutate):
    _, decision = sealed(tmp_path, protocol, bundle)
    evidence = f.official(bundle)
    mutate(evidence)
    result = settle(decision, evidence)
    assert result['status'] == 'inconsistent'
    assert all(a['payout_yen'] is None and a['profit_yen'] is None for a in result['arms'].values())


def test_result_missing_not_a_loser_or_refund(tmp_path, protocol, bundle):
    _, decision = sealed(tmp_path, protocol, bundle)
    evidence = f.official(bundle)
    evidence['results'].pop()
    result = settle(decision, evidence)
    assert result['status'] == 'incomplete'
    assert all(a['profit_yen'] is None for a in result['arms'].values())


def test_corrections_immutable_revision_chain(tmp_path, protocol, bundle):
    store, _ = sealed(tmp_path, protocol, bundle)
    e1 = f.official(bundle)
    r1 = store.add_settlement(bundle['race_id'], e1)
    e2 = f.official(bundle, revision=2, winners={2: 440})
    r2 = store.add_settlement(bundle['race_id'], e2)
    assert r2['previous_sha256'] == c.digest(r1)
    assert store.add_settlement(bundle['race_id'], e1) == r1
    assert store.verify_settlements(bundle['race_id']) == [r1, r2]
    with pytest.raises(ValueError, match='immutable_record_conflict'):
        store.add_settlement(bundle['race_id'], f.official(bundle, winners={1: 800}))
    with pytest.raises(ValueError, match='revision_gap'):
        store.add_settlement(bundle['race_id'], f.official(bundle, revision=4))


def test_no_bet_race_remains_settled_zero_budget_usage(tmp_path, protocol, bundle):
    for r in bundle['quote']['rows']:
        r['odds'] = '1.0'
    raw = json.dumps(dict(race_id=bundle['race_id'], horses=bundle['quote']['rows']))
    bundle['quote'].update(raw_text=raw, raw_sha256=c.sha_bytes(raw.encode()))
    derived = c.market_features(bundle['quote']['rows'])
    for r in bundle['predictions']['mev']['rows']:
        r['features'].update({k: v for k, v in derived[r['horse_id']].items() if k in r['features']})
    store, decision = sealed(tmp_path, protocol, bundle)
    result = store.add_settlement(bundle['race_id'], f.official(bundle))
    assert all(a['tickets'] == [] for a in decision['arms'].values())
    assert result['status'] == 'settled'
    assert all(a['stake_yen'] == a['payout_yen'] == a['profit_yen'] == 0 for a in result['arms'].values())


@pytest.mark.parametrize('rank_text,status', [('中', 'stopped'), ('失', 'disqualified')])
def test_nonfinished_starter_is_not_missing_result(tmp_path, protocol, bundle, rank_text, status):
    _, decision = sealed(tmp_path, protocol, bundle)
    evidence = f.official(bundle)
    evidence['results'][1].update(result_status=status, finish_order=None)
    # Second synthetic row is the first loser, with rank 2 in the fixture.
    evidence['raw_html'] = evidence['raw_html'].replace('<tr><td>2</td>', f'<tr><td>{rank_text}</td>', 1)
    evidence['raw_sha256'] = c.sha_bytes(evidence['raw_html'].encode())
    result = settle(decision, evidence)
    assert result['status'] == 'settled'


def test_large_number_rejected_cleanly(bundle, protocol):
    bundle['predictions']['legacy']['rows'][0]['probability'] = '1e999999'
    with pytest.raises(ValueError, match='invalid_number_range'):
        c.validate_inputs(bundle, protocol)


def test_datagram_and_database_guards():
    import sqlite3
    with offline_only(), pytest.raises(RuntimeError, match='disabled'):
        sqlite3.connect(':memory:')
    with offline_only(), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, pytest.raises(RuntimeError, match='disabled'):
        sock.sendto(b'test', ('127.0.0.1', 1))
