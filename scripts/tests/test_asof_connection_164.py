from __future__ import annotations

import datetime as dt
import json

import pytest

from scripts.roi_explore.asof_connection_164 import contract as ac
from scripts.roi_explore.asof_connection_164 import sources
from scripts.roi_explore.asof_connection_164.features import materialize, validate_features
from scripts.roi_explore.asof_connection_164.run import candidate_from_source, run, verify
from scripts.roi_explore.asof_connection_164.store import AsOfEvidenceStore
from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.model_connection_163.clock import RealClock
from scripts.roi_explore.model_connection_163.run import connection_disabled
from scripts.roi_explore.prospective_rehearsal_162.store import read
from scripts.tests.support.asof_fixture_164 import complete_candidate


def rewrite(root, ref, prefix, mutate):
    path = root/ref[prefix+'_path']
    value = json.loads(path.read_text())
    mutate(value)
    body = c.canonical(value)
    path.write_bytes(body)
    ref[prefix+'_sha256'] = c.sha_bytes(body)
    return value


@pytest.fixture
def complete(tmp_path, monkeypatch):
    with connection_disabled():
        candidate, clock = complete_candidate(tmp_path/'inputs', monkeypatch)
    return tmp_path/'inputs', candidate, clock


def test_complete_saved_contract_can_pass_all_gates(complete):
    _, candidate, _ = complete
    result = ac.inspect_candidate(candidate)
    assert result['contract_ready'], result['failures']
    assert result['eligible_for_prospective'] is False
    # The old snapshot's original availability predates the fresh quote and is valid.
    old = materialize(candidate['features']['legacy'])
    assert c.time(old['available_by_at']) < c.time(candidate['stage_anchors']['before'])


def test_real_clock_store_keeps_fixture_origin_offline(complete, tmp_path):
    _, candidate, clock = complete
    store = AsOfEvidenceStore(tmp_path/'journal', clock)
    decision = store.seal_now(candidate)
    assert decision['eligible_for_prospective'] is False
    assert decision['real_race_verified'] is False
    assert decision['schedule_origin'] == 'saved_guard_packet'
    assert store.verify_decision(candidate['race_id']) == decision
    assert store.seal_now(candidate) == decision


@pytest.mark.parametrize('kind', ['legacy', 'mev'])
def test_missing_feature_source_cannot_pass(complete, kind):
    _, candidate, _ = complete
    candidate['features'][kind] = None
    result = ac.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['gate'] == 'features_'+kind for r in result['failures'])


@pytest.mark.parametrize('kind', ['legacy', 'mev'])
def test_missing_durable_prediction_cannot_pass(complete, kind):
    _, candidate, _ = complete
    candidate['predictions'][kind] = None
    result = ac.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['gate'] == 'prediction_'+kind for r in result['failures'])


def test_feature_after_reference_rejected(complete):
    _, candidate, _ = complete
    p = materialize(candidate['features']['legacy'])
    when = (c.time(p['available_by_at'])-dt.timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValueError, match='features_not_available'):
        validate_features(candidate['features']['legacy'], kind='legacy', race_id=candidate['race_id'],
            race_date=candidate['race_date'], expected_field=candidate['field'], decision_at=when)


def test_mev_wrong_quote_digest_cannot_pass_even_with_updated_receipt(complete):
    root, candidate, _ = complete
    ref = candidate['features']['mev']
    rewrite(root, ref, 'source', lambda b:b.update(quote_sha256='a'*64))
    rewrite(root, ref, 'receipt', lambda b:b.update(source_sha256=ref['source_sha256']))
    result = ac.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['reason'] == 'feature_quote_source_changed' for r in result['failures'])


def test_column_availability_future_rejected(complete):
    root, candidate, _ = complete
    ref = candidate['features']['legacy']
    rewrite(root, ref, 'source', lambda b:b['source_times'].update(distance=candidate['post_time']))
    rewrite(root, ref, 'receipt', lambda b:b.update(source_sha256=ref['source_sha256']))
    with pytest.raises(ValueError, match='future_feature_dependency'):
        materialize(ref)


def test_mismatched_feature_row_model_binding(complete):
    root, candidate, _ = complete
    ref = candidate['predictions']['legacy']
    rewrite(root, ref, 'source', lambda b:b['prediction']['rows'][0]['features'].update(distance=999))
    rewrite(root, ref, 'receipt', lambda b:b.update(source_sha256=ref['source_sha256']))
    result = ac.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['reason'] == 'prediction_features_changed' for r in result['failures'])


def test_guard_after_must_follow_prediction_completion(complete):
    root, candidate, _ = complete
    ref = candidate['guards']['after']
    old = candidate['stage_anchors']['before']
    def earlier(b):
        for query in b['observations'].values():
            query.update(started_at=old, completed_at=old)
    rewrite(root, ref, 'source', earlier)
    rewrite(root, ref, 'receipt', lambda b:b.update(source_sha256=ref['source_sha256']))
    # stage itself is fresh within five seconds, but its causal position is wrong.
    result = ac.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['reason'] == 'after_guard_before_predictions' for r in result['failures']), result['failures']


def test_final_guard_cannot_precede_both_durable_predictions(complete):
    root, candidate, _ = complete
    ref = candidate['guards']['decision']
    old = candidate['stage_anchors']['before']
    def earlier(b):
        for query in b['observations'].values():
            query.update(started_at=old, completed_at=old)
    rewrite(root, ref, 'source', earlier)
    rewrite(root, ref, 'receipt', lambda b:b.update(source_sha256=ref['source_sha256']))
    result = ac.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['reason'] == 'decision_guard_before_ready_or_after_check' for r in result['failures']), result['failures']


def test_reference_window_and_post_changes_fail(complete):
    _, candidate, _ = complete
    candidate['decision_at'] = candidate['post_time']
    result = ac.inspect_candidate(candidate)
    assert not result['contract_ready']
    assert any(r['gate'] == 'decision_window' for r in result['failures'])


def test_historical_real_race_is_not_given_a_new_seal(tmp_path):
    source = sources.load_capture(sources.CAPTURES/'202609040611.json')
    candidate = candidate_from_source(source, None)
    store = AsOfEvidenceStore(tmp_path, RealClock())
    with pytest.raises(ValueError, match='historical_reference_cannot_be_sealed_now'):
        store.seal_now(candidate)
    assert not (tmp_path/'decisions'/candidate['race_id']/'accepted.json').exists()


def test_global_capture_pin_cannot_be_replaced_by_own_hash(tmp_path):
    source = sources.load_capture(sources.CAPTURES/'202609040611.json')
    ref = dict(source_kind='capture133', source_path=source['source_ref']['path'], source_sha256='a'*64)
    with pytest.raises(ValueError, match='capture_reference_mismatch'):
        materialize(ref)


def test_current_replay_receipt_is_not_a_historical_receipt(tmp_path):
    source = sources.load_capture(sources.CAPTURES/'202609040611.json')
    ref = dict(source_kind='capture133', source_path=source['source_ref']['path'], source_sha256=source['source_ref']['sha256'])
    store = AsOfEvidenceStore(tmp_path, RealClock())
    result = store.replay_component(ref)
    assert result['historical_ready_receipt_created'] is result['usable_as_original_prediction'] is False
    assert c.time(result['computed_at']) > c.time(source['post_time'])
    expected = source['original_prediction']
    assert result['result']['probabilities'] == pytest.approx(expected, abs=1e-14)


def test_protocol_tamper_before_seal_rejected(complete, tmp_path):
    _, candidate, clock = complete
    store = AsOfEvidenceStore(tmp_path, clock)
    value = read(tmp_path/'protocol.json')
    value['max_quote_age_seconds'] = 999999
    (tmp_path/'protocol.json').write_text(json.dumps(value))
    with pytest.raises(ValueError, match='protocol_changed'):
        store.seal_now(candidate)


def test_full_saved_source_audit_and_verify(tmp_path):
    out = tmp_path/'audit'
    with connection_disabled():
        report = run(out)
        result = verify(out)
    assert report['races'] == 46 and report['horses'] == 621
    assert report['legacy_memory_available_by_fixed_target'] == 45
    assert report['contract_ready'] == report['new_comparison_races'] == 0
    assert result['status'] == 'verified'
    assert not list((out/'journal').glob('decisions/*/accepted.json'))
    with pytest.raises(FileExistsError):
        run(out)


@pytest.mark.parametrize('kind,expected', [('normal','settled'),('dead_heat','settled'),
    ('missing','incomplete'),('pending','pending')])
def test_asof_mode_retains_official_only_accounting(kind, expected):
    from scripts.roi_explore.asof_connection_164.features import MODE
    from scripts.roi_explore.asof_connection_164.settlement import settle
    from scripts.roi_explore.prospective_rehearsal_162.fixtures import official
    fields = [dict(horse_id='test-'+str(i),horse_number=i) for i in (1,2,3)]
    base = dict(race_id='202609040611',field=fields,post_time='2026-09-20T06:00:00Z')
    decision = dict(**base, mode=MODE, eligible_for_prospective=False, sealed_at='2026-09-20T05:50:00Z',
        arms={a:dict(budget_yen=1000,tickets=[dict(**r,stake_yen=100) for r in fields[:2]]) for a in c.ARMS})
    evidence = official(base,missing=kind=='missing',pending=kind=='pending',
        winners={1:150,2:280} if kind=='dead_heat' else None)
    result = settle(decision,evidence)
    assert result['status'] == expected
    if expected != 'settled':
        assert all(a['payout_yen'] is a['profit_yen'] is None for a in result['arms'].values())
    else:
        assert all(a['payout_yen'] == (430 if kind=='dead_heat' else 210) for a in result['arms'].values())
        assert result['primary_eligible'] is (kind!='dead_heat')


def test_official_wrong_dividend_is_unvalued():
    from scripts.roi_explore.asof_connection_164.features import MODE
    from scripts.roi_explore.asof_connection_164.settlement import settle
    from scripts.roi_explore.prospective_rehearsal_162.fixtures import official
    fields = [dict(horse_id='test-'+str(i),horse_number=i) for i in (1,2)]
    base = dict(race_id='202609040611',field=fields,post_time='2026-09-20T06:00:00Z')
    decision = dict(**base,mode=MODE,eligible_for_prospective=False,sealed_at='2026-09-20T05:50:00Z',
        arms={a:dict(budget_yen=1000,tickets=[dict(**fields[0],stake_yen=100)]) for a in c.ARMS})
    evidence = official(base)
    evidence['payouts'][0]['payout_yen'] = 9999
    result = settle(decision,evidence)
    assert result['status'] == 'inconsistent'
    assert all(a['payout_yen'] is None for a in result['arms'].values())
