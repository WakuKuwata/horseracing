"""Strict local quote parsing with no acquisition path."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import socket
from pathlib import Path

import pytest

from scripts.roi_explore.model_connection_163.quotes import (
    CollectionDisabled, QuoteRejected, StoppedQuoteAdapter, freshness_seconds, from_saved,
    make_local_fixture, parse_saved,
)

ROOT = Path(__file__).resolve().parents[2]
RACE = '202605040907'
URL = f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={RACE}&type=1&action=update'
FIELD = [dict(horse_id='horse-a', horse_number=1), dict(horse_id='nk:horse-b', horse_number=2)]


def document():
    return {'status': 'result', 'data': {'official_datetime': '2026-10-27 13:00:00',
        'odds': {'1': {'01': ['2.1', '0.0', '1'], '02': ['5.2', '0.0', '2']}}}}


def metadata():
    return dict(race_id=RACE, expected_field=copy.deepcopy(FIELD), source_url=URL,
        fetch_started_at='2026-10-27T03:59:59Z', received_at='2026-10-27T04:00:00Z', source='local_fixture')


def parsed():
    return parse_saved(json.dumps(document()), **metadata())


def test_stopped_constructor_and_fetch_cannot_enable_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('network was touched')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    adapter = StoppedQuoteAdapter()
    with pytest.raises(CollectionDisabled, match='new_collection_disabled'):
        adapter.fetch(URL, enabled=True)
    for enabled in (True, 1, 'yes', None):
        with pytest.raises(CollectionDisabled):
            StoppedQuoteAdapter(enabled=enabled)


def test_local_body_and_canonical_mapping_exact():
    raw = json.dumps(document())
    quote = parse_saved(raw, **metadata())
    assert quote['rows'] == [dict(horse_id='horse-a', horse_number=1, odds='2.1', popularity=1),
        dict(horse_id='nk:horse-b', horse_number=2, odds='5.2', popularity=2)]
    assert quote['raw_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert quote['eligible_for_prospective'] is False
    assert quote['source'] == quote['origin'] == 'local_fixture'
    assert quote['provider_at'] == '2026-10-27T04:00:00+00:00'


def test_saved_file_never_rebases_time(tmp_path):
    path = tmp_path/'odds.json'
    path.write_text(json.dumps(document()))
    quote = from_saved(path, **metadata())
    assert quote['received_at'] == '2026-10-27T04:00:00Z'
    with pytest.raises(QuoteRejected, match='quote_not_fresh'):
        freshness_seconds(quote, '2026-10-27T04:30:00.000001Z')


def test_saved_raw_bytes_are_not_newline_normalized(tmp_path):
    body = json.dumps(document(), indent=2).replace('\n', '\r\n').encode('utf-8')
    path = tmp_path/'original.json'
    path.write_bytes(body)
    quote = from_saved(path, **metadata())
    assert quote['raw_sha256'] == hashlib.sha256(body).hexdigest()
    assert quote['raw_text'].encode() == body


def test_local_fixture_maker_keeps_explicit_creation_clock():
    quote = make_local_fixture(race_id=RACE,
        rows=[dict(**FIELD[0], odds='2.1', popularity=1), dict(**FIELD[1], odds='5.2', popularity=2)],
        fetch_started_at='2026-10-27T03:59:59.999998Z', received_at='2026-10-27T04:00:00.000001Z')
    assert quote['source'] == quote['origin'] == 'local_fixture'
    assert quote['received_at'] == '2026-10-27T04:00:00.000001Z'
    assert quote['eligible_for_prospective'] is False
    assert quote['rows'] == parsed()['rows']


@pytest.mark.parametrize('elapsed,good', [(0, True), (1800, True), (1800.000001, False), (-.000001, False)])
def test_inclusive_freshness(elapsed, good):
    quote = parsed()
    stamp = (dt.datetime(2026, 10, 27, 4, tzinfo=dt.UTC)+dt.timedelta(seconds=elapsed)).isoformat()
    if good:
        assert freshness_seconds(quote, stamp) == elapsed
    else:
        with pytest.raises(QuoteRejected, match='quote_not_fresh'):
            freshness_seconds(quote, stamp)


@pytest.mark.parametrize('replacement', ['https://evil.example/api/api_get_jra_odds.html?race_id='+RACE+'&type=1',
    URL.replace(RACE, '202605040908'), URL+'&race_id='+RACE, URL.replace('type=1', 'type=2'),
    URL.replace('https:', 'http:'), URL.replace('race.netkeiba.com', 'user@race.netkeiba.com'),
    URL+'&unused=yes'])
def test_source_binding_rejected(replacement):
    info = metadata()
    info['source_url'] = replacement
    with pytest.raises(QuoteRejected, match='source_url'):
        parse_saved(json.dumps(document()), **info)


@pytest.mark.parametrize('mutate', [
    lambda d: d.__setitem__('status', 'error'),
    lambda d: d.pop('status'),
    lambda d: d.__setitem__('race_id', '202605040908'),
    lambda d: d['data'].__setitem__('race_id', '202605040908'),
    lambda d: d['data']['odds']['1'].pop('02'),
    lambda d: d['data']['odds']['1'].update({'03': ['4.2', '0', '3']}),
    lambda d: d['data']['odds']['1'].update({'1': ['2.1', '0', '1']}),
    lambda d: d['data']['odds']['1']['01'].__setitem__(0, '---.-'),
    lambda d: d['data']['odds']['1']['01'].__setitem__(0, 'NaN'),
    lambda d: d['data']['odds']['1']['01'].__setitem__(0, '1e999999'),
    lambda d: d['data']['odds']['1']['01'].__setitem__(0, '.9'),
    lambda d: d['data']['odds']['1']['01'].__setitem__(2, '0'),
    lambda d: d['data']['odds']['1']['01'].__setitem__(2, True),
    lambda d: d['data']['odds']['1'].__setitem__('01', ['2.1']),
    lambda d: d['data'].__setitem__('official_datetime', 'invalid'),
    lambda d: d['data'].__setitem__('official_datetime', '2026-10-27 13:00:01'),
])
def test_incomplete_or_invalid_book_rejected(mutate):
    doc = document()
    mutate(doc)
    with pytest.raises(QuoteRejected):
        parse_saved(json.dumps(doc), **metadata())


@pytest.mark.parametrize('raw', ['[]', '{}', 'invalid', '{"data":{},"data":{}}', '{"data":NaN}'])
def test_bad_json_rejected(raw):
    with pytest.raises(QuoteRejected):
        parse_saved(raw, **metadata())


def test_duplicate_expected_ids_and_number_rejected():
    for key in ('horse_id', 'horse_number'):
        info = metadata()
        info['expected_field'][1][key] = info['expected_field'][0][key]
        with pytest.raises(QuoteRejected, match='quote_field_duplicate'):
            parse_saved(json.dumps(document()), **info)


def test_saved_real_fixture_retains_stale_provider_and_cannot_be_fresh():
    path = ROOT/'scrape/tests/fixtures/real/odds_202406050911.json'
    info = dict(race_id='202406050911', expected_field=[dict(horse_id=f'explicit-{i}', horse_number=i)
        for i in range(1, 19)], source_url='https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=202406050911&type=1',
        fetch_started_at='2026-06-28T00:00:00Z', received_at='2026-06-28T00:00:01Z')
    quote = from_saved(path, **info)
    assert len(quote['rows']) == 18
    assert quote['provider_at'] == '2024-12-28T06:50:17+00:00'
    with pytest.raises(QuoteRejected, match='quote_provider_not_fresh'):
        freshness_seconds(quote, '2026-06-28T00:00:02Z')


@pytest.mark.parametrize('stamp', ['2026-10-27T04:00:00', '2026-10-27T04:00:00.000000001Z'])
def test_unknown_clock_or_excess_precision_not_silently_accepted(stamp):
    info = metadata()
    info['received_at'] = stamp
    with pytest.raises(QuoteRejected):
        parse_saved(json.dumps(document()), **info)
