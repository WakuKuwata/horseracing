"""Unchanged official-only accounting rules for the saved as-of connection mode."""
from scripts.roi_explore.prospective_rehearsal_162.settlement import parse_official
from scripts.roi_explore.model_connection_163.contract import ARMS, Rejected, field, keys, require, sha_bytes, time
from .features import MODE


def settle(decision, evidence):
    """Missing/inconsistent evidence leaves BOTH arms unvalued, including no-bet arms."""
    require(decision['mode'] == MODE and decision['eligible_for_prospective'] is False,
        'offline_decision_required')
    expected_field = field(decision['field'])
    for arm in ARMS:
        tickets = decision['arms'][arm]['tickets']
        require(decision['arms'][arm]['budget_yen'] == 1000, 'invalid_budget')
        ids = set()
        for ticket in tickets:
            pair = (ticket['horse_id'], ticket['horse_number'])
            require(pair in expected_field and pair not in ids and ticket['stake_yen'] == 100
                and type(ticket['stake_yen']) is int, 'invalid_ticket')
            ids.add(pair)
        require(len(tickets) <= 10, 'invalid_ticket_count')
    result = dict(mode=MODE, simulated_clock=False, eligible_for_prospective=False,
        race_id=decision['race_id'], revision=evidence.get('revision'), observed_at=evidence.get('observed_at'),
        status='inconsistent', reason=None, primary_eligible=False, dead_heat=None,
        arms={a: dict(stake_yen=sum(t['stake_yen'] for t in decision['arms'][a]['tickets']),
            payout_yen=None, profit_yen=None) for a in ARMS})
    try:
        keys(evidence, ['race_id', 'revision', 'observed_at', 'source', 'raw_html', 'raw_sha256',
            'results', 'payouts'], 'settlement_evidence')
        require(type(evidence['revision']) is int and evidence['revision'] >= 1, 'invalid_revision')
        require(evidence['race_id'] == decision['race_id'], 'settlement_race_mismatch')
        require(evidence['source'] == 'netkeiba_result', 'unverified_official_source')
        require(time(evidence['observed_at']) > time(decision['sealed_at']), 'settlement_before_seal')
        require(time(evidence['observed_at']) > time(decision['post_time']), 'settlement_before_post')
        require(sha_bytes(evidence['raw_html'].encode()) == evidence['raw_sha256'], 'official_raw_sha_mismatch')
        raw_results, raw_payouts = parse_official(evidence['raw_html'], evidence['race_id'])
        rows = evidence['results']
        if not rows:
            require(not raw_results, 'normalized_results_missing')
            result.update(status='pending', reason='results_pending')
            return result
        actual = field(rows)
        if actual != expected_field:
            result.update(status='incomplete', reason='field_changed_or_results_incomplete')
            return result
        require(sorted(raw_results, key=lambda r: r['horse_id']) == sorted(rows, key=lambda r: r['horse_id']),
            'official_raw_results_mismatch')
        winners = set()
        for r in rows:
            keys(r, ['horse_id', 'horse_number', 'result_status', 'finish_order'], 'official_result_row')
            if r['result_status'] == 'finished':
                rank = r['finish_order']
                require(type(rank) is int and 1 <= rank <= len(rows), 'invalid_finish_order')
                if rank == 1:
                    winners.add(r['horse_number'])
            else:
                require(r['result_status'] in ('stopped', 'disqualified') and r['finish_order'] is None,
                    'invalid_result_status')
        require(bool(winners), 'official_winner_missing')
        payouts = {}
        for r in evidence['payouts']:
            keys(r, ['horse_number', 'payout_yen'], 'official_payout_row')
            n, value = r['horse_number'], r['payout_yen']
            require(type(n) is int and n >= 1 and type(value) is int and value >= 100
                and n not in payouts, 'invalid_official_payout')
            payouts[n] = value
        if not payouts and not raw_payouts:
            result.update(status='incomplete', reason='official_payout_missing')
            return result
        require(payouts == raw_payouts and set(payouts) == winners, 'official_payout_winner_mismatch')
        for arm in ARMS:
            account = result['arms'][arm]
            account['payout_yen'] = sum(t['stake_yen']//100*payouts.get(t['horse_number'], 0)
                for t in decision['arms'][arm]['tickets'])
            account['profit_yen'] = account['payout_yen']-account['stake_yen']
        result.update(status='settled', reason=None, primary_eligible=len(winners) == 1,
            dead_heat=len(winners) > 1)
        return result
    except (Rejected, KeyError, TypeError, ValueError) as exc:
        result['reason'] = str(exc)
        return result
