"""Stopped network adapter and strict, local-only decoding of preserved win-odds responses.

The odds API carries horse numbers, not horse IDs or race IDs.  A saved response therefore
requires its original source URL and an explicit number-to-canonical-ID field.  Reading a file
never supplies or updates its capture timestamp.  Local fixtures are labelled separately and
can never become prospective evidence.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class CollectionDisabled(RuntimeError):
    """Network acquisition is deliberately unavailable in this implementation."""


class QuoteRejected(ValueError):
    pass


def _require(ok, reason):
    if not ok:
        raise QuoteRejected(reason)


def _time(value):
    _require(isinstance(value, str) and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value),
        'quote_timestamp_timezone_or_precision_invalid')
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise QuoteRejected('quote_timestamp_invalid') from exc
    _require(stamp.tzinfo is not None and stamp.utcoffset() is not None, 'quote_timestamp_naive')
    return stamp.astimezone(dt.UTC)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def _field(expected_field):
    _require(isinstance(expected_field, list) and len(expected_field) >= 2, 'quote_full_field_required')
    by_number, seen_ids = {}, set()
    for row in expected_field:
        _require(isinstance(row, dict) and set(row) == {'horse_id', 'horse_number'}, 'quote_field_schema')
        horse_id, number = row['horse_id'], row['horse_number']
        _require(isinstance(horse_id, str) and horse_id and type(number) is int and 1 <= number <= 99,
            'quote_field_identity_invalid')
        _require(horse_id not in seen_ids and number not in by_number, 'quote_field_duplicate')
        seen_ids.add(horse_id)
        by_number[number] = horse_id
    return by_number


def _source_url(url, race_id):
    _require(isinstance(race_id, str) and re.fullmatch(r'\d{12}', race_id), 'quote_race_id_invalid')
    _require(isinstance(url, str), 'quote_source_url_required')
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise QuoteRejected('quote_source_url_invalid') from exc
    query = parse_qs(parsed.query, keep_blank_values=True)
    _require(parsed.scheme == 'https' and parsed.hostname == 'race.netkeiba.com'
        and port is None and parsed.username is None and parsed.password is None
        and parsed.path == '/api/api_get_jra_odds.html' and not parsed.fragment
        and query.get('race_id') == [race_id] and query.get('type') == ['1']
        and set(query) <= {'race_id', 'type', 'action'}
        and ('action' not in query or query['action'] == ['update']), 'quote_source_url_invalid')


def _price(value):
    _require(isinstance(value, (str, int, float)) and not isinstance(value, bool), 'quote_price_invalid')
    try:
        price = Decimal(str(value))
        floating = float(price)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise QuoteRejected('quote_price_invalid') from exc
    _require(price.is_finite() and price >= 1 and math.isfinite(floating), 'quote_price_invalid')
    return str(price)


def _popularity(value):
    _require(not isinstance(value, bool) and isinstance(value, (str, int)), 'quote_popularity_invalid')
    text = str(value)
    _require(re.fullmatch(r'[0-9]+', text) is not None and 1 <= int(text) <= 99, 'quote_popularity_invalid')
    return int(text)


def parse_saved(raw_text, *, race_id, expected_field, source_url, fetch_started_at,
                received_at, source='saved_response'):
    """Decode local bytes using ORIGINAL clocks; never calls a transport or a wall clock.

    ``source=local_fixture`` is reserved for explicitly generated local data.  It does not mean
    a fresh remote observation; all outputs unconditionally remain non-prospective.
    """
    _require(source in ('saved_response', 'local_fixture'), 'quote_origin_invalid')
    _source_url(source_url, race_id)
    by_number = _field(expected_field)
    start, received = _time(fetch_started_at), _time(received_at)
    _require(start <= received, 'quote_capture_time_reversed')
    _require(isinstance(raw_text, str), 'quote_raw_text_required')
    try:
        document = json.loads(raw_text, object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(QuoteRejected('quote_nonfinite_json')))
    except (json.JSONDecodeError, TypeError) as exc:
        raise QuoteRejected('quote_raw_json_invalid') from exc
    _require(isinstance(document, dict) and isinstance(document.get('data'), dict), 'quote_data_missing')
    # "result" means successful response, not that racing results are final.
    _require(document.get('status') == 'result', 'quote_response_not_successful')
    data = document['data']
    for claimed in (document.get('race_id'), data.get('race_id')):
        _require(claimed is None or claimed == race_id, 'quote_body_race_mismatch')
    odds = data.get('odds')
    _require(isinstance(odds, dict) and isinstance(odds.get('1'), dict) and odds['1'], 'quote_win_book_missing')
    seen, rows = set(), []
    for key, values in odds['1'].items():
        _require(isinstance(key, str) and re.fullmatch(r'[0-9]{1,2}', key) is not None,
            'quote_horse_number_invalid')
        n = int(key)
        _require(n in by_number and n not in seen, 'quote_full_field_mismatch')
        _require(isinstance(values, list) and len(values) >= 3, 'quote_row_unreadable')
        seen.add(n)
        rows.append(dict(horse_id=by_number[n], horse_number=n, odds=_price(values[0]),
            popularity=_popularity(values[2])))
    _require(seen == set(by_number), 'quote_full_field_mismatch')
    rows.sort(key=lambda row: (row['horse_number'], row['horse_id']))
    provider = data.get('official_datetime')
    if provider is None or provider == '':
        provider_at = None
    else:
        _require(isinstance(provider, str), 'quote_provider_time_invalid')
        try:
            provider_time = dt.datetime.strptime(provider, '%Y-%m-%d %H:%M:%S').replace(
                tzinfo=dt.timezone(dt.timedelta(hours=9))).astimezone(dt.UTC)
        except ValueError as exc:
            raise QuoteRejected('quote_provider_time_invalid') from exc
        _require(provider_time <= received, 'quote_provider_time_future')
        provider_at = provider_time.isoformat()
    return dict(format='netkeiba_win_json_v1', source=source, origin=source,
        eligible_for_prospective=False, fetch_started_at=fetch_started_at, received_at=received_at,
        provider_at=provider_at, cache='bypass', source_url=source_url,
        raw_text=raw_text, raw_sha256=hashlib.sha256(raw_text.encode()).hexdigest(), rows=rows)


def from_saved(path, **metadata):
    """Read a preserved UTF-8 body, preserving caller-supplied original capture metadata."""
    # read_text() performs universal-newline translation; preserving raw body SHA forbids that.
    return parse_saved(Path(path).read_bytes().decode('utf-8'), **metadata)


def make_local_fixture(*, race_id, rows, fetch_started_at, received_at):
    """Generate a labelled local response from explicit rows, never a remote quote.

    Clocks must come from the caller's observed local-fixture creation interval.  This helper
    cannot re-label saved remote data: its only supported origin/source is local_fixture.
    """
    _require(isinstance(rows, list), 'quote_full_field_required')
    expected = []
    win = {}
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {'horse_id', 'horse_number', 'odds', 'popularity'},
            'local_fixture_row_schema')
        expected.append(dict(horse_id=row['horse_id'], horse_number=row['horse_number']))
        key = str(row['horse_number']).zfill(2)
        _require(key not in win, 'quote_field_duplicate')
        win[key] = [_price(row['odds']), '0.0', str(_popularity(row['popularity']))]
    provider = _time(received_at).astimezone(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
    raw = json.dumps(dict(status='result', data=dict(official_datetime=provider, odds={'1': win})),
        ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return parse_saved(raw, race_id=race_id, expected_field=expected,
        source_url=f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=1&action=update',
        fetch_started_at=fetch_started_at, received_at=received_at, source='local_fixture')


def freshness_seconds(quote, decision_at):
    """Thirty-minute admission check; file read time is deliberately irrelevant."""
    _require(isinstance(quote, dict) and quote.get('eligible_for_prospective') is False,
        'offline_quote_required')
    decision, received = _time(decision_at), _time(quote['received_at'])
    elapsed = decision-received
    _require(dt.timedelta(0) <= elapsed <= dt.timedelta(seconds=1800), 'quote_not_fresh')
    if quote.get('provider_at') is not None:
        provider = _time(quote['provider_at'])
        _require(provider <= received and decision-provider <= dt.timedelta(seconds=1800),
            'quote_provider_not_fresh')
    return elapsed.total_seconds()


class StoppedQuoteAdapter:
    """No transport is accepted or constructed. Enabling acquisition always fails closed."""
    def __init__(self, *, enabled=False):
        if enabled is not False:
            raise CollectionDisabled('new_collection_disabled')

    def fetch(self, *args, **kwargs):
        raise CollectionDisabled('new_collection_disabled')

    parse_saved = staticmethod(parse_saved)
    from_saved = staticmethod(from_saved)
    make_local_fixture = staticmethod(make_local_fixture)
