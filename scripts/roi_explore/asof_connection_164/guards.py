"""Saved-only race guards with original clocks and explicit partial-evidence boundaries.

Historical capture133 rechecked result count/post, not the field after prediction. A shutuba
page proves its printed field/post only, not a fresh result-query response. Neither is silently
promoted to the complete guard needed for admission. No network/database code exists here.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from scripts.roi_explore.prospective_rehearsal_162.contract import digest, field, require, sha_bytes, time
from scripts.roi_explore.prospective_rehearsal_162.settlement import Node, Tree

ROOT = Path(__file__).resolve().parents[3]
STAGES = ('before', 'after', 'decision')
KINDS = ('capture133', 'shutuba_html', 'guard_observation_v1')
JST = dt.timezone(dt.timedelta(hours=9))
STAMP = re.compile(r'(\d{8}T\d{6}\d{1,6}Z)\.html\.gz')


def _load(path_name, checksum):
    require(isinstance(path_name, str), 'guard_source_path_missing')
    rel = Path(path_name)
    require(not rel.is_absolute() and rel.parts and '..' not in rel.parts, 'guard_source_path_unsafe')
    path = ROOT
    for part in rel.parts:
        path = path/part
        require(not path.is_symlink(), 'guard_source_path_unsafe')
    require(path.resolve().is_relative_to(ROOT.resolve()) and path.is_file(), 'guard_source_path_unsafe')
    data = path.read_bytes()
    require(isinstance(checksum, str) and re.fullmatch('[0-9a-f]{64}', checksum)
        and sha_bytes(data) == checksum, 'guard_source_sha_mismatch')
    return path, data


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'guard_duplicate_json_key')
            result[key] = value
        return result
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('guard_nonfinite_json')))


def _capture(raw, stage):
    require(raw.get('artifact_kind') == 'paired_prospective_record' and raw.get('source') == 'capture'
        and raw.get('classification') == 'prospective', 'guard_capture_kind_unverified')
    timeline = raw.get('timeline', {})
    require(timeline.get('clock') == 'real', 'guard_capture_clock_unverified')
    acquired = time(timeline['acquisition_finished_at'])
    began = time(timeline['acquisition_started_at'])
    checked = time(timeline['post_prediction_check_at'])
    post = time(raw['scheduled_start'])
    require(re.fullmatch(r'\d{12}', raw['race_id']) is not None
        and post.astimezone(JST).date().isoformat() == raw['race_day'], 'guard_capture_race_date_invalid')
    require(began <= acquired <= checked < post and time(raw['input_captured_at']) == began,
        'guard_capture_time_order')
    regime = raw['regimes']['preweight']
    encoded = regime['inputs']
    columns, rows = encoded['columns'], encoded['rows']
    require(isinstance(columns, list) and len(columns) == len(set(columns))
        and isinstance(rows, dict) and set(rows) == set(regime['started_ids'])
        and len(regime['started_ids']) == len(set(regime['started_ids'])), 'guard_capture_field_unverified')
    require('race_id' in columns and 'horse_number' in columns, 'guard_capture_field_unverified')
    result = []
    for horse_id, values in rows.items():
        require(isinstance(values, list) and len(values) == len(columns), 'guard_capture_row_unreadable')
        row = dict(zip(columns, values, strict=True))
        require(row['race_id'] == raw['race_id'], 'guard_capture_race_mismatch')
        require('field_size' in row and row['field_size'] == len(rows), 'guard_capture_field_size_mismatch')
        number = row['horse_number']
        require(not isinstance(number, bool) and isinstance(number, (int, float))
            and float(number).is_integer(), 'guard_capture_horse_number_invalid')
        result.append(dict(horse_id=horse_id, horse_number=int(number)))
    field(result)
    initial = raw.get('n_result_rows_at_capture')
    after = raw.get('n_result_rows_after_prediction')
    require(type(initial) is int and initial >= 0 and type(after) is int and after >= 0,
        'guard_capture_result_count_invalid')
    require(raw.get('pre_result') is (initial == 0 and after == 0), 'guard_capture_pending_flag_inconsistent')
    # Count at capture is from the pre-acquisition facts query. Field came from acquired inputs.
    # Neither record_written_at nor acquisition finish is a post-publish availability receipt.
    observed = acquired if stage == 'before' else checked
    count = initial if stage == 'before' else after
    field_reobserved = stage == 'before'
    reasons = ['durable_guard_receipt_missing']
    if not field_reobserved:
        reasons.insert(0, 'field_not_reobserved')
    if stage == 'decision':
        reasons.insert(0, 'decision_guard_not_saved')
    return dict(race_id=raw['race_id'], post_time=post.isoformat(), observed_at=observed.isoformat(),
        field=result, field_observed_at=acquired.isoformat(), field_reobserved=field_reobserved,
        pending=count == 0, result_rows_count=count,
        pending_observed_by_at=(began if stage == 'before' else checked).isoformat(),
        available_by_at=None, source_status='historical_partial', incomplete_reasons=reasons,
        note='after/decision capture133 facts query rechecked post/result count only; field remains the input capture population')


def _canonical_url(soup, provided):
    links = [n for n in soup.nodes('link') if n.attrs.get('rel') == 'canonical']
    require(len(links) == 1, 'guard_html_race_identity_missing')
    url = links[0].attrs.get('href', '')
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname == 'race.netkeiba.com'
        and parsed.username is None and parsed.password is None and parsed.port is None
        and parsed.path == '/race/shutuba.html' and not parsed.fragment, 'guard_html_source_url_invalid')
    query = parse_qs(parsed.query)
    require(set(query) == {'race_id'} and len(query['race_id']) == 1
        and re.fullmatch(r'\d{12}', query['race_id'][0]), 'guard_html_source_url_invalid')
    if provided is not None:
        require(url == provided.strip(), 'guard_html_source_url_mismatch')
    return query['race_id'][0], url


def _html(path, data, record, stage):
    if path.name.endswith('.gz'):
        data = gzip.decompress(data)
    raw = data.decode(record.get('encoding') or 'utf-8')
    soup = Tree(raw).root
    url = record.get('source_url')
    url_file = path.parent/'url.txt'
    if url_file.is_file():
        stored_url = url_file.read_text().strip()
        require(url is None or url.strip() == stored_url, 'guard_html_source_url_mismatch')
        url = stored_url
    rid, canonical_url = _canonical_url(soup, url)
    title = soup.nodes('title')
    meta = [n for n in soup.nodes('div') if 'RaceData01' in n.attrs.get('class', '').split()]
    require(len(title) == 1 and len(meta) == 1, 'guard_html_post_missing')
    day = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', title[0].text())
    hm = re.search(r'(\d{1,2}):(\d{2})\s*発走', meta[0].text())
    require(day is not None and hm is not None, 'guard_html_post_missing')
    post = dt.datetime(*map(int, day.groups()), *map(int, hm.groups()), tzinfo=JST).astimezone(dt.UTC)
    # The prediction-lap upsell table reuses Shutuba_Table but is not an entry population.
    # Exclude that known auxiliary layout explicitly; reject any other ambiguity.
    tables = [t for t in soup.nodes('table', 'Shutuba_Table')
        if 'PredictRap_Table' not in t.attrs.get('class', '').split()]
    require(len(tables) == 1, 'guard_html_field_missing')
    entries = []
    for tr in tables[0].nodes('tr', 'HorseList'):
        cells = [n for n in tr.children if isinstance(n, Node) and n.tag == 'td']
        horse = [n for n in cells if 'HorseInfo' in n.attrs.get('class', '').split()]
        numbers = [n for n in cells if any(c.startswith('Umaban') for c in n.attrs.get('class', '').split())]
        require(len(horse) == len(numbers) == 1, 'guard_html_field_unreadable')
        links = [a for a in horse[0].nodes('a') if '/horse/' in a.attrs.get('href', '')]
        require(len(links) == 1 and numbers[0].text().isdigit(), 'guard_html_field_unreadable')
        match = re.search(r'/horse/([^/?#]+)', links[0].attrs['href'])
        require(match is not None, 'guard_html_field_unreadable')
        status = 'cancelled' if '取消' in tr.text() else 'excluded' if '除外' in tr.text() else 'started'
        entries.append(dict(horse_id=unquote(match.group(1)), horse_number=int(numbers[0].text()), entry_status=status))
    active = [{k: v for k, v in r.items() if k != 'entry_status'} for r in entries if r['entry_status'] == 'started']
    field(active)
    # Native IDs are retained exactly. nk: or any other mapping requires independent source evidence.
    all_ids = [r['horse_id'] for r in entries]
    all_numbers = [r['horse_number'] for r in entries]
    require(len(all_ids) == len(set(all_ids)) and len(all_numbers) == len(set(all_numbers)), 'guard_html_field_duplicate')
    filename = STAMP.fullmatch(path.name)
    observed = None
    if filename:
        observed = dt.datetime.strptime(filename.group(1), '%Y%m%dT%H%M%S%fZ').replace(tzinfo=dt.UTC)
    if record.get('observed_at') is not None:
        require(observed is not None and time(record['observed_at']) == observed, 'guard_html_timestamp_unverified')
    return dict(race_id=rid, post_time=post.isoformat(), observed_at=observed.isoformat() if observed else None,
        field=active, field_observed_at=observed.isoformat() if observed else None,
        field_reobserved=True, pending=None, result_rows_count=None, pending_observed_by_at=None,
        available_by_at=None, source_status='historical_partial',
        incomplete_reasons=['result_state_not_observed', 'durable_guard_receipt_missing'],
        entries=entries, source_url=canonical_url, decoded_raw_sha256=sha_bytes(data),
        note='printed shutuba population/post only; absence of a result table is not a DB pending observation')


def _query_observation(raw, record, stage):
    require(set(raw) == {'artifact_kind', 'schema_version', 'mode', 'eligible_for_prospective',
        'race_id', 'stage', 'observations'}, 'guard_query_schema_invalid')
    require(raw['artifact_kind'] == 'race_guard_query_evidence_v1' and raw['schema_version'] == 1
        and raw['mode'] == 'offline_asof_connection' and raw['eligible_for_prospective'] is False,
        'guard_query_kind_invalid')
    require(raw['stage'] == stage, 'guard_query_stage_mismatch')
    observations = raw['observations']
    require(set(observations) == {'race', 'entries', 'results'}, 'guard_query_observation_missing')
    clocks, starts = {}, {}
    for kind, query in observations.items():
        require(set(query) == {'started_at', 'completed_at', 'rows'} and isinstance(query['rows'], list),
            'guard_query_observation_schema_invalid')
        start, end = time(query['started_at']), time(query['completed_at'])
        require(start <= end, 'guard_query_clock_reversed')
        clocks[kind] = end
        starts[kind] = start
    races = observations['race']['rows']
    require(len(races) == 1 and set(races[0]) == {'race_id', 'race_date', 'post_time'}
        and races[0]['race_id'] == raw['race_id'], 'guard_query_race_invalid')
    post = time(races[0]['post_time'])
    require(post.astimezone(JST).date().isoformat() == races[0]['race_date'], 'guard_query_race_date_invalid')
    entries = observations['entries']['rows']
    require(all(isinstance(r, dict) and set(r) == {'horse_id', 'horse_number', 'entry_status'}
        and r['entry_status'] in ('started', 'cancelled', 'excluded') for r in entries), 'guard_query_entry_invalid')
    all_field = [{k: v for k, v in r.items() if k != 'entry_status'} for r in entries]
    field(all_field)
    active = [{k: v for k, v in r.items() if k != 'entry_status'} for r in entries if r['entry_status'] == 'started']
    field(active)
    results = observations['results']['rows']
    require(all(isinstance(r, dict) and r.get('race_id') == raw['race_id'] for r in results),
        'guard_query_result_race_invalid')
    receipt_path, receipt_bytes = _load(record.get('receipt_path'), record.get('receipt_sha256'))
    receipt = _json(receipt_bytes)
    require(set(receipt) == {'artifact_kind', 'mode', 'eligible_for_prospective', 'source_sha256', 'available_by_at'}
        and receipt['artifact_kind'] == 'race_guard_ready_v1' and receipt['mode'] == 'offline_asof_connection'
        and receipt['eligible_for_prospective'] is False and receipt['source_sha256'] == record['source_sha256'],
        'guard_query_receipt_invalid')
    available = time(receipt['available_by_at'])
    require(available >= max(clocks.values()), 'guard_query_receipt_before_observation')
    return dict(race_id=raw['race_id'], post_time=post.isoformat(), observed_at=max(clocks.values()).isoformat(),
        field=active, field_observed_at=clocks['entries'].isoformat(), field_reobserved=True,
        pending=len(results) == 0, result_rows_count=len(results),
        pending_observed_by_at=clocks['results'].isoformat(), available_by_at=available.isoformat(),
        source_status='complete_saved_query_fixture', incomplete_reasons=[], entries=entries,
        observation_clocks={key: value.isoformat() for kind in clocks for key, value in
            ((kind+'_started_at', starts[kind]), (kind+'_completed_at', clocks[kind]))},
        receipt_path=str(receipt_path.relative_to(ROOT)),
        receipt_sha256=record['receipt_sha256'])


def inspect_guard(record, *, stage):
    """Return what a pinned saved source proves, including explicitly incomplete observations."""
    require(stage in STAGES, 'guard_stage_invalid')
    require(isinstance(record, dict) and record.get('source_kind') in KINDS, 'guard_source_kind_invalid')
    require(set(record) <= {'source_kind', 'source_path', 'source_sha256', 'observed_at',
        'source_url', 'encoding', 'receipt_path', 'receipt_sha256'}, 'guard_record_schema_invalid')
    path, data = _load(record.get('source_path'), record.get('source_sha256'))
    kind = record['source_kind']
    if kind == 'shutuba_html':
        result = _html(path, data, record, stage)
    elif kind == 'capture133':
        result = _capture(_json(data), stage)
        if record.get('observed_at') is not None:
            require(time(record['observed_at']) == time(result['observed_at']), 'guard_source_clock_mismatch')
    else:
        result = _query_observation(_json(data), record, stage)
        if record.get('observed_at') is not None:
            require(time(record['observed_at']) == time(result['observed_at']), 'guard_source_clock_mismatch')
    result.update(mode='offline_asof_connection', eligible_for_prospective=False, stage=stage,
        source_kind=kind, source_path=record['source_path'], source_sha256=record['source_sha256'])
    result['evidence_sha256'] = digest(result)
    return result


def validate_guard(record, *, expected_field, race_id, decision_at, post_time, stage, max_age_seconds=5):
    """Require a fresh, complete saved observation; never manufacture missing historical checks."""
    require(type(max_age_seconds) is int and 0 <= max_age_seconds <= 5, 'guard_age_policy_invalid')
    evidence = inspect_guard(record, stage=stage)
    require(evidence['race_id'] == race_id, 'guard_race_mismatch')
    require(time(evidence['post_time']) == time(post_time), 'guard_post_changed')
    decision, post = time(decision_at), time(post_time)
    require(decision < post, 'guard_not_prestart')
    if stage == 'decision' and evidence['source_kind'] == 'capture133':
        require(False, 'decision_guard_not_saved')
    require(evidence['field_reobserved'], 'field_not_reobserved')
    require(field(evidence['field']) == field(expected_field), 'guard_field_changed')
    require(evidence['pending'] is True, 'guard_pending_unproven_or_results_present')
    require(evidence['available_by_at'] is not None, 'guard_durable_availability_unproven')
    available = time(evidence['available_by_at'])
    require(available <= decision, 'guard_not_available_before_decision')
    clocks = evidence.get('observation_clocks') or dict(observed_at=evidence['observed_at'])
    for observed in clocks.values():
        require(observed is not None, 'guard_original_clock_missing')
        stamp = time(observed)
        require(stamp <= decision and decision-stamp <= dt.timedelta(seconds=max_age_seconds),
            'guard_stale_or_future')
    return evidence
