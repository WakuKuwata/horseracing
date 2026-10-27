"""Read synthetic rehearsal files directly; do not import the main contract/store/settler."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load(p):
    return json.loads(p.read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def canonical_sha(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(body).hexdigest()


def at(value):
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value)
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(dt.UTC)


def field(rows):
    pairs = {(r['horse_id'], r['horse_number']) for r in rows}
    assert len(pairs) == len(rows)
    assert len({h for h, _ in pairs}) == len(rows)
    assert len({n for _, n in pairs}) == len(rows)
    return pairs


def audit(path):
    manifest = load(path/'manifest.json')
    assert set(manifest['files']) | {'manifest.json'} == {
        str(p.relative_to(path)) for p in path.rglob('*') if p.is_file()}
    for name, checksum in manifest['files'].items():
        assert sha(path/name) == checksum, name
    for name, checksum in manifest['source_files'].items():
        assert sha(ROOT/name) == checksum, name
    protocol, report = load(path/'protocol.json'), load(path/'report.json')
    assert protocol == load(path/'sessions/protocol.json')
    assert protocol['mode'] == 'offline_rehearsal' and protocol['simulated_clock'] is True
    assert protocol['eligible_for_prospective'] is False
    for name, checksum in protocol['model_files'].items():
        assert sha(ROOT/name) == checksum, name
    frozen_coefficients = load(ROOT/'artifacts/151-roi-prediction-ablation/comparison/summary.json')['coefficients']['2026']
    assert protocol['coefficients'] == {a: frozen_coefficients[a] for a in ['roi_only', 'plus_prediction']}
    assert report['eligible_for_prospective'] is False
    assert report['historical_inventory']['complete_contract_accepted'] == 0
    assert report['historical_inventory']['races'] == 9
    accepted, rejected, settled, incomplete = 0, 0, 0, 0
    outcomes = []
    assert load(path/'schedule.json')['races'] == [
        {'race_id': r['race_id'], 'scenario': r['scenario']} for r in report['coverage']]
    for row in report['coverage']:
        p = path/'sessions/races'/row['race_id']
        if not row['accepted']:
            assert not (p/'accepted.json').exists() and not (p/'inputs.json').exists()
            failure = load(p/'failure.json')
            assert failure['reason'] == row['reason'] and failure['eligible_for_prospective'] is False
            rejected += 1
            continue
        accepted += 1
        inputs, ready, decision, seal, admission = [load(p/name) for name in
            ['inputs.json', 'ready.json', 'decision.json', 'seal.json', 'accepted.json']]
        assert inputs['mode'] == decision['mode'] == admission['mode'] == 'offline_rehearsal'
        assert inputs['eligible_for_prospective'] is decision['eligible_for_prospective'] is False
        assert ready['input_sha256'] == canonical_sha(inputs)
        assert decision['input_sha256'] == canonical_sha(inputs)
        assert decision['ready_sha256'] == canonical_sha(ready)
        assert seal['decision_sha256'] == canonical_sha(decision)
        assert admission['seal_sha256'] == canonical_sha(seal)
        post, receive = at(inputs['post_time']), at(inputs['quote']['received_at'])
        decision_at, sealed_at = at(decision['decision_at']), at(admission['acknowledged_at'])
        assert at(ready['available_by_at']) <= decision_at <= at(seal['observed_saved_at']) <= sealed_at
        assert post-dt.timedelta(seconds=600) <= decision_at <= sealed_at < post-dt.timedelta(seconds=540)
        assert dt.timedelta(0) <= decision_at-receive <= dt.timedelta(seconds=1800)
        assert dt.timedelta(0) <= sealed_at-receive <= dt.timedelta(seconds=1800)
        assert decision['guard']['pending'] is True
        assert dt.timedelta(0) <= sealed_at-at(decision['guard']['at']) <= dt.timedelta(seconds=5)
        ids = field(inputs['field'])
        quote = inputs['quote']
        assert field(quote['rows']) == ids
        assert sha_text(quote['raw_text']) == quote['raw_sha256']
        raw = json.loads(quote['raw_text'])
        assert raw['race_id'] == inputs['race_id'] and raw['horses'] == quote['rows']
        maps = {}
        quoted = {r['horse_id']: r for r in quote['rows']}
        for kind, prediction in inputs['predictions'].items():
            assert field(prediction['rows']) == ids
            assert prediction['model_sha256'] == protocol['model_shas'][kind]
            assert receive <= at(prediction['input_available_at']) <= at(prediction['started_at']) <= at(prediction['computed_at']) <= at(ready['available_by_at'])
            for r in prediction['rows']:
                assert at(prediction['started_at']) <= at(r['created_at']) <= at(r['updated_at']) <= at(prediction['computed_at'])
                assert set(r['features']) == set(protocol['feature_columns'][kind])
                if kind == 'legacy':
                    assert all(k in r['features'] and r['features'][k] is None for k in ['weight', 'weight_diff', 'carried_weight_ratio'])
                else:
                    assert float(r['features']['odds']) == float(quoted[r['horse_id']]['odds'])
                    assert r['features']['popularity'] == quoted[r['horse_id']]['popularity']
            maps[kind] = {r['horse_id']: float(r['probability']) for r in prediction['rows']}
        quotes = sorted(quote['rows'], key=lambda r: (r['horse_number'], r['horse_id']))
        inv = [1/float(r['odds']) for r in quotes]
        market = [v/sum(inv) for v in inv]
        for arm in ['roi_only', 'plus_prediction']:
            beta = protocol['coefficients'][arm]['coefficients']
            score = []
            for q, r in zip(market, quotes, strict=True):
                values = {'q': q, 'p_mev': min(1-1e-6, max(1e-6, maps['mev'][r['horse_id']])),
                          'p_pred_preweight': maps['legacy'][r['horse_id']]}
                score.append(sum(b*math.log(values[k]) for k, b in beta.items()))
            exp = [math.exp(s-max(score)) for s in score]
            probs = [v/sum(exp) for v in exp]
            candidates = []
            for r, probability, actual in zip(quotes, probs, decision['arms'][arm]['rows'], strict=True):
                assert math.isclose(actual['probability'], probability, rel_tol=0, abs_tol=1e-14)
                ev = probability*float(r['odds'])
                assert math.isclose(actual['ev'], ev, rel_tol=0, abs_tol=1e-14)
                if ev > 1:
                    candidates.append((ev, r))
            selected = sorted(candidates, key=lambda x: (-x[0], x[1]['horse_number'], x[1]['horse_id']))[:10]
            expected = [dict(horse_id=r['horse_id'], horse_number=r['horse_number'], stake_yen=100) for _, r in selected]
            assert decision['arms'][arm]['tickets'] == expected
        evidence, result = [load(p/'settlements/0001'/name) for name in ['evidence.json', 'result.json']]
        assert result['evidence_sha256'] == canonical_sha(evidence)
        assert sha_text(evidence['raw_html']) == evidence['raw_sha256']
        assert at(evidence['observed_at']) > post
        html = evidence['raw_html']
        assert f'https://race.netkeiba.com/race/result.html?race_id={inputs["race_id"]}' in html
        raw_rows = re.findall(r'<tr><td>(\d+)</td><td>\d+</td><td>(\d+)</td><td><a href="/horse/([^"/]+)/">', html)
        assert len(raw_rows) == len(ids)
        assert {(h, int(n)) for _, n, h in raw_rows} == ids
        winners = {int(n) for rank, n, _ in raw_rows if int(rank) == 1}
        amounts = re.search(r'<td class="Payout">(.*?)</td>', html)
        if amounts is None:
            incomplete += 1
            assert result['status'] == 'incomplete'
            assert all(a['payout_yen'] is a['profit_yen'] is None for a in result['arms'].values())
        else:
            numbers = re.findall(r'<div><span>(\d+)</span></div>', html)
            values = re.findall(r'([\d,]+)円', amounts.group(1))
            payout = {int(n): int(v.replace(',', '')) for n, v in zip(numbers, values, strict=True)}
            assert set(payout) == winners
            assert result['status'] == 'settled'
            assert result['dead_heat'] == (len(winners) > 1)
            for arm in ['roi_only', 'plus_prediction']:
                tickets = decision['arms'][arm]['tickets']
                expected = sum(payout.get(t['horse_number'], 0) for t in tickets)
                assert result['arms'][arm]['payout_yen'] == expected
                assert result['arms'][arm]['profit_yen'] == expected-100*len(tickets)
            settled += 1
        outcomes.append(dict(race_id=row['race_id'], status=result['status'], dead_heat=result['dead_heat']))
    assert (accepted, rejected, settled, incomplete) == (3, 8, 2, 1)
    return dict(status='passed', mode='offline_rehearsal', eligible_for_prospective=False,
        accepted=accepted, rejected=rejected, settled=settled, incomplete=incomplete,
        historical_contract_accepted=0, main_selector_imported=False, main_settler_imported=False,
        model_inference=False, network_or_database_access=False,
        source_manifest_sha256=sha(path/'manifest.json'), outcomes=outcomes)


def sha_text(value):
    return hashlib.sha256(value.encode()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.out)
    destination = Path(__file__).with_name('independent-rehearsal-audit.json')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
