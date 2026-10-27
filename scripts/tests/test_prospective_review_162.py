"""Independent adversarial checks of the offline storage contract."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math

import pytest

from scripts.roi_explore.prospective_rehearsal_162 import contract as c
from scripts.roi_explore.prospective_rehearsal_162 import fixtures
from scripts.roi_explore.prospective_rehearsal_162.store import EvidenceStore, SimulatedClock, read


@pytest.fixture(scope='module')
def protocol():
    return c.make_protocol()


@pytest.fixture
def sample(protocol):
    return fixtures.bundle(protocol)


def prepared(tmp_path, protocol, sample):
    clock = SimulatedClock((c.time(sample['post_time'])-dt.timedelta(minutes=11)).isoformat())
    store = EvidenceStore(tmp_path, protocol, clock)
    store.prepare(sample)
    guard = fixtures.final_guard(sample)
    clock.advance_to((c.time(guard['at'])+dt.timedelta(seconds=1)).isoformat())
    return store, clock, guard


@pytest.mark.parametrize('key,value', [('mode', 'live'), ('simulated_clock', False),
    ('eligible_for_prospective', True)])
def test_fixture_cannot_masquerade_as_prospective(protocol, sample, key, value):
    sample[key] = value
    with pytest.raises(c.Rejected, match='offline_mode_required'):
        c.validate_inputs(sample, protocol)


def test_future_row_is_not_hidden_by_earlier_run_clock(protocol, sample):
    sample['predictions']['legacy']['rows'][1]['updated_at'] = sample['post_time']
    with pytest.raises(c.Rejected, match='prediction_row_unavailable'):
        c.validate_inputs(sample, protocol)


def test_second_valid_run_cannot_replace_race_input(tmp_path, protocol, sample):
    store, _, _ = prepared(tmp_path, protocol, sample)
    changed = copy.deepcopy(sample)
    changed['session_id'] = 'replacement-session'
    changed['predictions']['legacy']['run_id'] = 'replacement-run'
    with pytest.raises(c.Rejected, match='immutable_record_conflict'):
        store.prepare(changed)
    assert read(store._race(sample['race_id'])/'inputs.json') == sample


def test_missing_old_body_weight_key_is_not_preweight(protocol, sample):
    sample['predictions']['legacy']['rows'][0]['features'].pop('weight')
    with pytest.raises(c.Rejected, match='incomplete_input_evidence'):
        c.validate_inputs(sample, protocol)


def test_duplicate_prediction_id_is_not_full_field(protocol, sample):
    sample['predictions']['mev']['rows'][1]['horse_id'] = sample['predictions']['mev']['rows'][0]['horse_id']
    with pytest.raises(c.Rejected, match='duplicate_field'):
        c.validate_inputs(sample, protocol)


def test_raw_quote_and_vector_cannot_diverge(protocol, sample):
    sample['quote']['rows'][0]['odds'] = '999'
    with pytest.raises(c.Rejected, match='quote_raw_rows_mismatch'):
        c.validate_inputs(sample, protocol)


def test_exact_freshness_at_decision_does_not_allow_late_seal(tmp_path, protocol, sample):
    post = c.time(sample['post_time'])
    sample['quote']['received_at'] = (post-dt.timedelta(seconds=2400)).isoformat()
    sample['quote']['fetch_started_at'] = (post-dt.timedelta(seconds=2401)).isoformat()
    sample['guard_before']['at'] = (post-dt.timedelta(seconds=2402)).isoformat()
    clock = SimulatedClock((post-dt.timedelta(seconds=660)).isoformat())
    store = EvidenceStore(tmp_path, protocol, clock)
    ready = store.prepare(sample)
    guard = fixtures.final_guard(sample)
    clock.advance_to((post-dt.timedelta(seconds=600)).isoformat())
    # At decision age is exactly1800s, but saving/sealing needs additional time.
    c.gate(sample, guard, ready['available_by_at'], (post-dt.timedelta(seconds=600)).isoformat(), protocol)
    with pytest.raises(c.Rejected, match='stale_quote'):
        store.seal(sample['race_id'], guard)
    assert not (store._race(sample['race_id'])/'accepted.json').exists()


def test_corrupt_ready_must_be_rejected_before_admission(tmp_path, protocol, sample):
    store, _, guard = prepared(tmp_path, protocol, sample)
    path = store._race(sample['race_id'])/'ready.json'
    ready = read(path)
    ready['protocol_sha256'] = '0'*64
    path.write_text(json.dumps(ready))
    with pytest.raises(c.Rejected):
        store.seal(sample['race_id'], guard)
    assert not (path.parent/'accepted.json').exists()


def test_both_arm_vectors_and_tickets_match_independent_calculation(tmp_path, protocol, sample):
    store, _, guard = prepared(tmp_path, protocol, sample)
    decision = store.seal(sample['race_id'], guard)
    quote = sorted(sample['quote']['rows'], key=lambda r: (r['horse_number'], r['horse_id']))
    maps = {kind: {row['horse_id']: float(row['probability']) for row in p['rows']}
            for kind, p in sample['predictions'].items()}
    inverse = [1/float(row['odds']) for row in quote]
    market = [v/sum(inverse) for v in inverse]
    for arm in ('roi_only', 'plus_prediction'):
        beta = protocol['coefficients'][arm]['coefficients']
        logits = []
        for row, q in zip(quote, market, strict=True):
            inputs = {'q': q, 'p_mev': min(1-1e-6, max(1e-6, maps['mev'][row['horse_id']])),
                      'p_pred_preweight': maps['legacy'][row['horse_id']]}
            logits.append(sum(coef*math.log(inputs[key]) for key, coef in beta.items()))
        exp = [math.exp(v-max(logits)) for v in logits]
        probabilities = [v/sum(exp) for v in exp]
        for actual, probability in zip(decision['arms'][arm]['rows'], probabilities, strict=True):
            assert actual['probability'] == pytest.approx(probability, abs=1e-14)
        candidates = [(p*float(row['odds']), row) for row, p in zip(quote, probabilities, strict=True)
                      if p*float(row['odds']) > 1]
        selected = sorted(candidates, key=lambda pair: (-pair[0], pair[1]['horse_number'], pair[1]['horse_id']))[:10]
        assert decision['arms'][arm]['tickets'] == [dict(horse_id=row['horse_id'],
            horse_number=row['horse_number'], stake_yen=100) for _, row in selected]
    assert decision['eligible_for_prospective'] is False


def test_non_official_canonical_domain_is_unvalued(tmp_path, protocol, sample):
    from scripts.roi_explore.prospective_rehearsal_162.settlement import settle
    store, _, guard = prepared(tmp_path, protocol, sample)
    decision = store.seal(sample['race_id'], guard)
    evidence = fixtures.official(sample)
    evidence['raw_html'] = evidence['raw_html'].replace('race.netkeiba.com', 'evil.example')
    evidence['raw_sha256'] = c.sha_bytes(evidence['raw_html'].encode())
    result = settle(decision, evidence)
    assert result['status'] == 'inconsistent'
    assert all(a['payout_yen'] is None for a in result['arms'].values())


def test_prestart_official_evidence_is_not_settled(tmp_path, protocol, sample):
    from scripts.roi_explore.prospective_rehearsal_162.settlement import settle
    store, _, guard = prepared(tmp_path, protocol, sample)
    decision = store.seal(sample['race_id'], guard)
    evidence = fixtures.official(sample)
    evidence['observed_at'] = (c.time(sample['post_time'])-dt.timedelta(minutes=8)).isoformat()
    result = settle(decision, evidence)
    assert result['status'] != 'settled'
    assert all(a['payout_yen'] is None for a in result['arms'].values())


def test_dead_heat_uses_both_printed_official_dividends(tmp_path, protocol, sample):
    store, _, guard = prepared(tmp_path, protocol, sample)
    decision = store.seal(sample['race_id'], guard)
    evidence = fixtures.official(sample, winners={1: 170, 2: 390})
    result = store.add_settlement(sample['race_id'], evidence)
    assert result['status'] == 'settled'
    assert result['dead_heat'] is True
    assert result['primary_eligible'] is False
    for arm in ('roi_only', 'plus_prediction'):
        expected = sum({1: 170, 2: 390}.get(t['horse_number'], 0)
                       for t in decision['arms'][arm]['tickets'])
        assert result['arms'][arm]['payout_yen'] == expected
    assert store.verify_settlements(sample['race_id']) == [result]


def test_payout_correction_adds_revision_without_changing_decision(tmp_path, protocol, sample):
    store, _, guard = prepared(tmp_path, protocol, sample)
    decision = store.seal(sample['race_id'], guard)
    decision_path = store._race(sample['race_id'])/'decision.json'
    original = decision_path.read_bytes()
    first = store.add_settlement(sample['race_id'], fixtures.official(sample, winners={2: 220}))
    second = store.add_settlement(sample['race_id'], fixtures.official(sample, revision=2, winners={2: 350}))
    assert store.verify_settlements(sample['race_id']) == [first, second]
    assert decision_path.read_bytes() == original
    for arm in ('roi_only', 'plus_prediction'):
        count = sum(t['horse_number'] == 2 for t in decision['arms'][arm]['tickets'])
        assert second['arms'][arm]['payout_yen']-first['arms'][arm]['payout_yen'] == count*130


def test_final_odds_column_cannot_affect_official_account(tmp_path, protocol, sample):
    from scripts.roi_explore.prospective_rehearsal_162.settlement import settle
    store, _, guard = prepared(tmp_path, protocol, sample)
    decision = store.seal(sample['race_id'], guard)
    evidence = fixtures.official(sample, winners={2: 220})
    expected = settle(decision, evidence)
    evidence['raw_html'] = evidence['raw_html'].replace('</a></td></tr>', '</a></td><td>9999999</td></tr>')
    evidence['raw_sha256'] = c.sha_bytes(evidence['raw_html'].encode())
    assert settle(decision, evidence) == expected
