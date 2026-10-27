"""Saved real-race as-of admission. Missing facts cannot become an accepted paired judgment."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.model_connection_163 import models
from scripts.roi_explore.model_connection_163.quotes import parse_saved, freshness_seconds
from .features import MODE, ROOT, read_ref, validate_features
from .guards import validate_guard


def validate_quote(reference, *, race_id, expected_field, decision_at):
    if reference['source_kind'] == 'chaos158':
        snapshot = ROOT/'artifacts/158-prerace-roi-comparison/snapshot'
        manifest = json.loads((snapshot/'manifest.json').read_text())
        entry = manifest['tables']['chaos']
        path = ROOT/entry['path']
        c.require(c.sha_bytes(path.read_bytes()) == entry['sha256'] == reference['source_sha256'], 'chaos_source_changed')
        table = pd.read_parquet(path)
        selected = table.loc[table.race_id.eq(race_id) & table.chaos_snapshot_id.eq(reference['snapshot_id'])]
        c.require(len(selected) == 1, 'chaos_snapshot_missing')
        row = selected.iloc[0]
        rows = json.loads(row.field_json)
        c.require(c.digest(rows) == row.content_digest and len(rows) == row.n, 'chaos_digest_mismatch')
        c.require(row.status == 'active' and row.capture_strength == 'confirmatory'
            and row.capture_policy_version == 'capture_policy_v1' and row.source == 'netkeiba', 'chaos_unverified')
        c.require(c.field(rows) == c.field(expected_field), 'quote_field_mismatch')
        observed, available = pd.Timestamp(row.captured_at), max(pd.Timestamp(row.captured_at), pd.Timestamp(row.created_at))
        when = c.time(decision_at)
        c.require(available.to_pydatetime() <= when, 'quote_not_available_by_decision')
        age = when-observed.to_pydatetime()
        c.require(dt.timedelta(0) <= age <= dt.timedelta(seconds=1800), 'quote_not_fresh')
        for r in rows:
            c.number(r['odds'], 1)
        return dict(mode=MODE, race_id=race_id, rows=rows, received_at=observed.isoformat(),
            fetch_started_at=None,
            available_by_at=available.isoformat(), quote_age_seconds=age.total_seconds(),
            raw_response_retained=False, source_ref=dict(reference), raw_sha256=row.content_digest)
    c.require(reference['source_kind'] == 'saved_quote_v1', 'unknown_quote_source')
    _, metadata = read_ref(reference, 'metadata')
    c.keys(metadata, ['race_id', 'source_url', 'fetch_started_at', 'received_at', 'available_by_at',
        'source_sha256', 'origin'], 'saved_quote_metadata')
    path = ROOT/reference['source_path']
    # Safe source path and digest validation: bytes here are a JSON odds response.
    _, raw = read_ref(reference)
    del raw
    c.require(metadata['race_id'] == race_id and metadata['source_sha256'] == reference['source_sha256'],
        'quote_metadata_mismatch')
    quote = parse_saved(path.read_bytes().decode('utf-8'), race_id=race_id, expected_field=expected_field,
        source_url=metadata['source_url'], fetch_started_at=metadata['fetch_started_at'],
        received_at=metadata['received_at'], source=metadata['origin'])
    age = freshness_seconds(quote, decision_at)
    c.require(c.time(quote['received_at']) <= c.time(metadata['available_by_at']) <= c.time(decision_at),
        'quote_durable_after_decision')
    return dict(mode=MODE, race_id=race_id, rows=quote['rows'], received_at=quote['received_at'],
        fetch_started_at=quote['fetch_started_at'],
        available_by_at=metadata['available_by_at'], quote_age_seconds=age, raw_response_retained=True,
        source_ref=dict(reference), raw_sha256=quote['raw_sha256'])


def validate_prediction(reference, *, kind, packet, race_id, race_date, expected_field, decision_at):
    _, raw = read_ref(reference)
    c.keys(raw, ['artifact_kind', 'mode', 'eligible_for_prospective', 'kind', 'race_id', 'race_date',
        'feature_packet_sha256', 'prediction'], 'prediction_snapshot')
    c.require(raw['artifact_kind'] == 'model_prediction_snapshot_v1' and raw['mode'] == MODE
        and raw['eligible_for_prospective'] is False and raw['kind'] == kind
        and raw['race_id'] == race_id and raw['race_date'] == race_date, 'prediction_identity_mismatch')
    c.require(raw['feature_packet_sha256'] == packet['packet_sha256'], 'prediction_feature_binding_mismatch')
    p = raw['prediction']
    c.keys(p, ['run_id', 'producer', 'model_version', 'model_sha256', 'input_available_at',
        'started_at', 'computed_at', 'rows', 'evidence'], 'prediction')
    protocol = c.make_protocol()
    c.require(p['producer'] == 'actual_model' and p['model_version'] == protocol['model_versions'][kind]
        and p['model_sha256'] == protocol['model_shas'][kind], 'prediction_model_mismatch')
    c.require(isinstance(p['run_id'], str) and bool(p['run_id']), 'prediction_run_id_missing')
    c.require(packet['durable_available_at'] is not None, 'feature_durable_receipt_missing')
    c.require(c.time(packet['durable_available_at']) <= c.time(p['input_available_at']) <= c.time(p['started_at'])
        <= c.time(p['computed_at']) <= c.time(decision_at), 'prediction_not_before_decision')
    c.require(c.field(p['rows']) == c.field(expected_field), 'prediction_field_mismatch')
    source_rows = {r['horse_id']: r for r in packet['rows']}
    for row in p['rows']:
        c.require(row['features'] == source_rows[row['horse_id']]['features'], 'prediction_features_changed')
        # Original feature availability is allowed to precede the quote; no date rebasing.
        c.require(c.time(row['input_asof']) == c.time(packet['available_by_at'])
            and c.time(p['started_at']) <= c.time(row['created_at']) <= c.time(row['updated_at'])
            <= c.time(p['computed_at']), 'prediction_row_unavailable')
    models.validate_evidence_binding(kind, p, race_id=race_id, race_date=race_date)
    if kind == 'legacy':
        h = p['evidence']['started_history']
        history = [dict(zip(h['columns'], values, strict=True)) for values in h['rows']]
        c.require(c.digest(sorted(history, key=lambda r:(r['horse_id'], r['race_date'], r['race_id'])))
            == c.digest(sorted(packet['history'], key=lambda r:(r['horse_id'], r['race_date'], r['race_id']))),
            'prediction_history_changed')
    _, receipt = read_ref(reference, 'receipt')
    c.keys(receipt, ['artifact_kind', 'mode', 'eligible_for_prospective', 'source_sha256',
        'available_by_at'], 'prediction_ready')
    c.require(receipt['artifact_kind'] == 'prediction_ready_v1' and receipt['mode'] == MODE
        and receipt['eligible_for_prospective'] is False and receipt['source_sha256'] == reference['source_sha256'],
        'prediction_ready_mismatch')
    c.require(c.time(p['computed_at']) <= c.time(receipt['available_by_at']) <= c.time(decision_at),
        'prediction_not_durably_saved_before_decision')
    return dict(p, durable_available_at=receipt['available_by_at'])


def inspect_candidate(candidate):
    """Evaluate all independent gates, without silently dropping failed or missing races."""
    c.keys(candidate, ['schema_version', 'mode', 'eligible_for_prospective', 'race_id', 'race_date',
        'post_time', 'decision_at', 'field', 'features', 'guards', 'stage_anchors', 'quote', 'predictions'], 'candidate')
    c.require(candidate['schema_version'] == 1 and candidate['mode'] == MODE
        and candidate['eligible_for_prospective'] is False, 'saved_mode_required')
    rid, day, field = candidate['race_id'], candidate['race_date'], candidate['field']
    c.field(field)
    c.require(len(rid) == 12 and rid.isdigit() and day[:4] == '2026', 'invalid_race_identity')
    post, when = c.time(candidate['post_time']), c.time(candidate['decision_at'])
    c.keys(candidate['features'], ['legacy', 'mev'], 'candidate_features')
    c.keys(candidate['predictions'], ['legacy', 'mev'], 'candidate_predictions')
    c.keys(candidate['guards'], ['before', 'after', 'decision'], 'candidate_guards')
    c.keys(candidate['stage_anchors'], ['before', 'after'], 'stage_anchors')
    checks, packets, observations, predictions = [], {}, {}, {}
    def check(name, fn):
        try:
            value = fn()
            checks.append(dict(gate=name, passed=True, reason=None))
            return value
        except (ValueError, KeyError, TypeError) as error:
            checks.append(dict(gate=name, passed=False, reason=str(error)))
            return None
    def window():
        c.require(post-dt.timedelta(seconds=600) <= when < post-dt.timedelta(seconds=540), 'outside_fixed_decision_window')
        c.require(when < post and post.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat() == day,
            'not_proven_prestart')
        return True
    check('decision_window', window)
    def anchors():
        c.require(c.time(candidate['stage_anchors']['before']) <= c.time(candidate['stage_anchors']['after'])
            <= when, 'guard_stage_anchor_order')
        return True
    check('stage_anchors', anchors)
    for kind in ('legacy', 'mev'):
        def features(kind=kind):
            ref = candidate['features'][kind]
            c.require(ref is not None, 'feature_source_missing:'+kind)
            return validate_features(ref, kind=kind, race_id=rid, race_date=day,
                expected_field=field, decision_at=candidate['decision_at'], require_durable=True)
        packets[kind] = check('features_'+kind, features)
    for stage in ('before', 'after', 'decision'):
        def guard(stage=stage):
            ref = candidate['guards'][stage]
            c.require(ref is not None, 'guard_missing:'+stage)
            anchor = candidate['decision_at'] if stage == 'decision' else candidate['stage_anchors'][stage]
            return validate_guard(ref, expected_field=field, race_id=rid, decision_at=anchor,
                post_time=candidate['post_time'], stage=stage)
        observations[stage] = check('guard_'+stage, guard)
    def quote():
        c.require(candidate['quote'] is not None, 'quote_source_missing')
        value = validate_quote(candidate['quote'], race_id=rid, expected_field=field, decision_at=candidate['decision_at'])
        c.require(value['raw_response_retained'], 'quote_original_response_missing')
        return value
    quote_value = check('quote_fresh_full_response', quote)
    for kind in ('legacy', 'mev'):
        def prediction(kind=kind):
            ref = candidate['predictions'][kind]
            c.require(ref is not None, 'prediction_durable_evidence_missing:'+kind)
            c.require(packets[kind] is not None, 'feature_binding_failed:'+kind)
            return validate_prediction(ref, kind=kind, packet=packets[kind], race_id=rid, race_date=day,
                expected_field=field, decision_at=candidate['decision_at'])
        predictions[kind] = check('prediction_'+kind, prediction)
    def market():
        c.require(packets['mev'] is not None and quote_value is not None, 'market_input_binding_incomplete')
        c.require(packets['mev']['quote_sha256'] == quote_value['raw_sha256'], 'feature_quote_source_changed')
        c.require(c.time(quote_value['available_by_at']) <= c.time(packets['mev']['available_by_at']),
            'quote_after_mev_materialization')
        c.require(c.time(quote_value['received_at']) <= c.time(packets['mev']['generated_at']),
            'quote_after_mev_generation')
        expected = c.market_features(quote_value['rows'])
        for row in packets['mev']['rows']:
            for name, value in expected[row['horse_id']].items():
                if name in row['features']:
                    c.require(c.time(packets['mev']['source_times'][name]) >= c.time(quote_value['received_at']),
                        'market_feature_before_quote')
                    actual = row['features'][name]
                    c.require(actual is not None and abs(float(actual)-float(value)) <= 1e-12,
                        'mev_quote_input_mismatch')
        return True
    check('mev_market_binding', market)
    def causality():
        c.require(all(observations.values()) and all(predictions.values()) and quote_value is not None,
            'stage_causality_evidence_incomplete')
        before, after, final = [observations[k] for k in ('before', 'after', 'decision')]
        qstart = c.time(quote_value['fetch_started_at'])
        c.require(c.time(before['available_by_at']) <= c.time(candidate['stage_anchors']['before']) == qstart,
            'before_guard_after_quote_start')
        completed = max(c.time(p['computed_at']) for p in predictions.values())
        after_starts = [c.time(v) for k,v in after['observation_clocks'].items() if k.endswith('_started_at')]
        c.require(min(after_starts) >= completed, 'after_guard_before_predictions')
        ready = max(c.time(p['durable_available_at']) for p in predictions.values())
        final_starts = [c.time(v) for k,v in final['observation_clocks'].items() if k.endswith('_started_at')]
        c.require(min(final_starts) >= max(ready, c.time(candidate['stage_anchors']['after'])),
            'decision_guard_before_ready_or_after_check')
        c.require(c.time(quote_value['available_by_at']) <= c.time(predictions['mev']['input_available_at']),
            'quote_after_mev_prediction')
        return True
    check('stage_causality', causality)
    return dict(mode=MODE, eligible_for_prospective=False, race_id=rid, race_date=day,
        reference_decision_at=candidate['decision_at'], contract_ready=all(r['passed'] for r in checks),
        checks=checks, failures=[r for r in checks if not r['passed']],
        feature_packets={k: p['packet_sha256'] if p else None for k,p in packets.items()},
        candidate_sha256=c.digest(candidate))
