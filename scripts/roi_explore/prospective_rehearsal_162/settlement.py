"""Official printed win dividends only; no closing odds are parsed or used."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

from .contract import ARMS, MODE, Rejected, field, keys, require, sha_bytes, time


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def nodes(self, tag, css=None):
        result = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag and (css is None or css in child.attrs.get('class', '').split()):
                    result.append(child)
                result.extend(child.nodes(tag, css))
        return result

    def strings(self):
        result = []
        for child in self.children:
            result.extend(child.strings() if isinstance(child, Node) else [child.strip()] if child.strip() else [])
        return result

    def text(self):
        return ''.join(self.strings())


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'wbr'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_official(html, race_id):
    soup = Tree(html).root
    canonicals = [n for n in soup.nodes('link') if n.attrs.get('rel') == 'canonical']
    require(len(canonicals) == 1, 'official_race_identity_missing')
    url = urlsplit(canonicals[0].attrs.get('href', ''))
    require(url.scheme == 'https' and url.hostname == 'race.netkeiba.com'
        and url.path == '/race/result.html' and parse_qs(url.query).get('race_id') == [race_id],
        'official_race_identity_missing')
    # Explicitly parse rank/number/horse link only, never the final-odds column.
    results = []
    tables = soup.nodes('table', 'RaceTable01')
    require(len(tables) <= 1, 'official_result_tables_ambiguous')
    if tables:
        for tr in tables[0].nodes('tr'):
            cells = [n for n in tr.children if isinstance(n, Node) and n.tag == 'td']
            if not cells:
                continue
            require(len(cells) >= 4, 'official_result_unreadable')
            rank_text = cells[0].text()
            if rank_text.startswith(('取', '除')):
                continue
            n = cells[2].text()
            links = [a for a in cells[3].nodes('a') if '/horse/' in a.attrs.get('href', '')]
            require(n.isdigit() and len(links) == 1, 'official_result_unreadable')
            match = re.search(r'/horse/([^/?#]+)', links[0].attrs['href'])
            require(match is not None, 'official_horse_id_missing')
            status = 'finished' if rank_text.isdigit() else 'stopped' if rank_text.startswith('中') else (
                'disqualified' if rank_text.startswith('失') else None)
            require(status is not None, 'official_result_status_unknown')
            results.append(dict(horse_id=unquote(match.group(1)), horse_number=int(n),
                result_status=status, finish_order=int(rank_text) if status == 'finished' else None))
    versions = []
    for tr in [r for table in soup.nodes('table', 'Payout_Detail_Table') for r in table.nodes('tr', 'Tansho')]:
        result, payout = tr.nodes('td', 'Result'), tr.nodes('td', 'Payout')
        require(len(result) == 1 and len(payout) == 1, 'official_payout_unreadable')
        numbers = [span.text() for div in result[0].nodes('div') for span in div.nodes('span') if span.text()]
        amounts = payout[0].strings()
        require(len(numbers) == len(amounts) and len(numbers) > 0, 'official_payout_unreadable')
        pairs = []
        for n, amount in zip(numbers, amounts, strict=True):
            match = re.fullmatch(r'([0-9,]+)円', amount)
            require(n.isdigit() and match is not None, 'official_payout_unreadable')
            value = int(match.group(1).replace(',', ''))
            require(int(n) > 0 and value >= 100, 'official_payout_invalid')
            pairs.append((int(n), value))
        require(len({n for n, _ in pairs}) == len(pairs), 'official_payout_duplicate')
        versions.append(tuple(sorted(pairs)))
    require(len(set(versions)) <= 1, 'official_payout_conflicting_rows')
    return results, dict(versions[0]) if versions else {}


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
    result = dict(mode=MODE, simulated_clock=True, eligible_for_prospective=False,
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
