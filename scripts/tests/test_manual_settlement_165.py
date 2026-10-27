from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.roi_explore.manual_observation_165.settlement import settle
from scripts.roi_explore.model_connection_163.contract import ARMS, sha_bytes
from scripts.roi_explore.prospective_rehearsal_162.fixtures import official
from scripts.roi_explore.prospective_rehearsal_162.settlement import parse_official

NOW = '2026-10-27T03:00:00Z'


def example(*, origin='fixture_rehearsal', winners=None, missing=False, pending=False):
    field = [dict(horse_id=f'horse-{i}',horse_number=i) for i in (1,2,3)]
    base = dict(race_id='202609040611',field=field,post_time='2026-09-20T06:00:00Z')
    decision = dict(**base,mode='manual_observation',origin=origin,
        evidence_admissible=origin=='manual_live',can_adopt_model=False,
        sealed_at='2026-09-20T05:50:00Z',identity_map=[],
        arms={a:dict(budget_yen=1000,tickets=[dict(**r,stake_yen=100) for r in field[:2]]) for a in ARMS})
    return decision, official(base,winners=winners,missing=missing,pending=pending)


@pytest.mark.parametrize('origin', ['manual_live','fixture_rehearsal'])
def test_ordinary_official_accounting_and_origin(origin):
    decision,evidence = example(origin=origin)
    original = copy.deepcopy(decision)
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status'] == 'settled'
    assert result['origin'] == origin and result['evidence_admissible'] is (origin=='manual_live')
    assert result['can_adopt_model'] is False
    assert result['primary_eligible'] is (origin=='manual_live')
    assert all(a==dict(stake_yen=200,payout_yen=210,profit_yen=10) for a in result['arms'].values())
    assert decision == original


def test_dead_heat_all_winners_use_printed_separate_dividends():
    decision,evidence = example(origin='manual_live',winners={1:150,2:280})
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='settled' and result['dead_heat'] is True and result['primary_eligible'] is False
    assert all(a['payout_yen']==430 and a['profit_yen']==230 for a in result['arms'].values())


@pytest.mark.parametrize('kind,status', [('missing','incomplete'),('pending','pending')])
def test_missing_is_not_a_loss_even_for_no_bet_arm(kind,status):
    decision,evidence = example(missing=kind=='missing',pending=kind=='pending')
    decision['arms']['roi_only']['tickets'] = []
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']==status
    assert all(a['payout_yen'] is a['profit_yen'] is None for a in result['arms'].values())


@pytest.mark.parametrize('mutate,reason', [
    (lambda d,e:e.update(observed_at='2026-10-28T00:00:00Z'),'official_observation_from_future'),
    (lambda d,e:e.update(observed_at='2026-09-20T05:49:59Z'),'settlement_before_seal'),
    (lambda d,e:e.update(observed_at='2026-09-20T05:55:00Z'),'settlement_before_post'),
    (lambda d,e:e.update(raw_sha256='wrong'),'official_raw_sha_mismatch'),
    (lambda d,e:e.update(source='closing_odds'),'unverified_official_source'),
    (lambda d,e:e['payouts'][0].update(payout_yen=9999),'official_payout_winner_mismatch'),
    (lambda d,e:e['payouts'].append(copy.deepcopy(e['payouts'][0])),'invalid_official_payout'),
    (lambda d,e:e['results'][0].update(finish_order=True),'invalid_finish_order'),
    (lambda d,e:e.update(revision=True),'invalid_revision'),
])
def test_inconsistent_evidence_never_values_either_arm(mutate,reason):
    decision,evidence = example()
    mutate(decision,evidence)
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='inconsistent' and result['reason']==reason
    assert all(a['payout_yen'] is a['profit_yen'] is None for a in result['arms'].values())


def test_missing_started_result_is_not_refunded():
    decision,evidence = example()
    evidence['results'].pop()
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='incomplete'
    assert all(a['payout_yen'] is None for a in result['arms'].values())


def test_source_id_alias_requires_explicit_frozen_map():
    decision,evidence = example()
    for row in evidence['results']:
        row['horse_id'] = 'nk:'+row['horse_id']
    for row in decision['field']:
        row['horse_id'] = 'nk:'+row['horse_id']
    for arm in ARMS:
        for row in decision['arms'][arm]['tickets']:
            row['horse_id'] = 'nk:'+row['horse_id']
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='inconsistent' and result['reason']=='source_horse_id_mapping_missing'
    decision['identity_map'] = [dict(source_id='horse-'+str(i),canonical_id='nk:horse-'+str(i)) for i in (1,2,3)]
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='settled'
    assert all(a['payout_yen']==210 for a in result['arms'].values())


@pytest.mark.parametrize('mapping', [
    [dict(source_id='source-a',canonical_id='horse-1'),dict(source_id='source-a',canonical_id='horse-2')],
    [dict(source_id='source-a',canonical_id='horse-1'),dict(source_id='source-b',canonical_id='horse-1')],
    [dict(source_id='source-a',canonical_id='outside')],
    [dict(source_id='horse-1',canonical_id='horse-2')],
])
def test_ambiguous_or_conflicting_frozen_map_is_unvalued(mapping):
    decision,evidence = example()
    decision['identity_map'] = mapping
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='inconsistent'
    assert all(a['payout_yen'] is None for a in result['arms'].values())


def test_mapping_cannot_move_a_horse_to_another_number():
    decision,evidence = example()
    evidence['raw_html'] = evidence['raw_html'].replace('/horse/horse-1/','/horse/source-a/')
    evidence['raw_sha256'] = sha_bytes(evidence['raw_html'].encode())
    decision['identity_map'] = [dict(source_id='source-a',canonical_id='horse-2')]
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='inconsistent' and result['reason']=='identity_map_horse_number_mismatch'


def test_no_bet_race_can_be_settled_zero_after_complete_evidence():
    decision,evidence = example()
    for arm in ARMS:
        decision['arms'][arm]['tickets'] = []
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='settled'
    assert all(a==dict(stake_yen=0,payout_yen=0,profit_yen=0) for a in result['arms'].values())


@pytest.mark.parametrize('mutate', [
    lambda d:d.update(mode='offline_rehearsal'),
    lambda d:d.update(origin='unknown'),
    lambda d:d.update(evidence_admissible=True),
    lambda d:d.update(can_adopt_model=True),
    lambda d:d['arms']['roi_only']['tickets'][0].update(stake_yen=200),
    lambda d:d['arms']['roi_only']['tickets'].append(copy.deepcopy(d['arms']['roi_only']['tickets'][0])),
])
def test_bad_decision_cannot_be_settled(mutate):
    decision,evidence = example()
    mutate(decision)
    with pytest.raises(ValueError):
        settle(decision,evidence,observed_now=NOW)


def test_final_odds_columns_never_affect_settlement():
    decision,evidence = example()
    expected = settle(decision,evidence,observed_now=NOW)
    evidence['raw_html'] = evidence['raw_html'].replace('</a></td></tr>','</a></td><td>9999999.0</td></tr>')
    evidence['raw_sha256'] = sha_bytes(evidence['raw_html'].encode())
    assert settle(decision,evidence,observed_now=NOW)==expected


def test_actual_saved_result_fixture_direct_ids_and_explicit_alias():
    path = Path('scrape/tests/fixtures/real/results_202602011206.html')
    raw = path.read_text()
    rid = '202602011206'
    rows,payouts = parse_official(raw,rid)
    assert len(rows)==16 and payouts=={1:340}
    fields = [dict(horse_id=r['horse_id'],horse_number=r['horse_number']) for r in rows]
    winner = next(r for r in fields if r['horse_number']==1)
    decision = dict(mode='manual_observation',origin='fixture_rehearsal',evidence_admissible=False,
        can_adopt_model=False,race_id=rid,field=fields,post_time='2026-01-01T00:00:00Z',
        sealed_at='2025-12-31T23:50:00Z',identity_map=[],
        arms={a:dict(budget_yen=1000,tickets=[dict(**winner,stake_yen=100)]) for a in ARMS})
    evidence = dict(race_id=rid,revision=1,source='netkeiba_result',observed_at='2026-10-26T00:00:00Z',
        raw_html=raw,raw_sha256=sha_bytes(raw.encode()),results=rows,
        payouts=[dict(horse_number=n,payout_yen=v) for n,v in payouts.items()])
    result = settle(decision,evidence,observed_now=NOW)
    assert result['status']=='settled' and all(a['payout_yen']==340 for a in result['arms'].values())
    assert result['primary_eligible'] is False
