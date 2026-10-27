"""Complete synthetic query/input fixture, built with real models; never real-race evidence."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from scripts.roi_explore.asof_connection_164 import features, guards
from scripts.roi_explore.asof_connection_164.features import MODE, materialize
from scripts.roi_explore.model_connection_163 import contract as c, models
from scripts.roi_explore.model_connection_163.clock import RealClock
from scripts.roi_explore.model_connection_163.quotes import make_local_fixture, parse_saved
from scripts.roi_explore.model_connection_163.run import input_rows
from scripts.roi_explore.prospective_rehearsal_162.store import publish


def complete_candidate(root, monkeypatch):
    root = Path(root)
    monkeypatch.setattr(features, 'ROOT', root)
    monkeypatch.setattr(guards, 'ROOT', root)
    from scripts.roi_explore.asof_connection_164 import contract
    monkeypatch.setattr(contract, 'ROOT', root)
    clock = RealClock()
    origin = clock.now()
    post = (c.time(origin)+dt.timedelta(seconds=595)).isoformat()
    rid = '202699990001'
    day = c.time(post).astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    field = [dict(horse_id=f'fixture-{i}', horse_number=i) for i in (1,2,3)]
    def save(name, body):
        path = root/name
        checksum = publish(path, body)
        return dict(source_path=name, source_sha256=checksum)
    def ready(name, artifact, source_sha):
        body = dict(artifact_kind=artifact, mode=MODE, eligible_for_prospective=False,
            source_sha256=source_sha, available_by_at=clock.now())
        ref = save(name, body)
        return dict(receipt_path=ref['source_path'], receipt_sha256=ref['source_sha256']), body['available_by_at']
    def guard(stage):
        queries = {}
        for key, rows in [('race', [dict(race_id=rid, race_date=day, post_time=post)]),
            ('entries', [dict(**r, entry_status='started') for r in field]), ('results', [])]:
            queries[key] = dict(started_at=clock.now(), completed_at=clock.now(), rows=rows)
        ref = save(f'guards/{stage}.json', dict(artifact_kind='race_guard_query_evidence_v1',
            schema_version=1, mode=MODE, eligible_for_prospective=False, race_id=rid, stage=stage, observations=queries))
        receipt, at = ready(f'guards/{stage}-ready.json', 'race_guard_ready_v1', ref['source_sha256'])
        return dict(source_kind='guard_observation_v1', **ref, **receipt), at
    before, _ = guard('before')
    qstart = clock.now()
    prototype = make_local_fixture(race_id=rid, rows=[dict(**r, odds=str(p), popularity=i)
        for i,(r,p) in enumerate(zip(field,(2,4,8),strict=True),1)], fetch_started_at=qstart, received_at=clock.now())
    received = clock.now()
    quote = parse_saved(prototype['raw_text'], race_id=rid, expected_field=field,
        source_url=prototype['source_url'], fetch_started_at=qstart, received_at=received, source='local_fixture')
    raw_path = root/'quote/raw.json'
    raw_path.parent.mkdir(parents=True,exist_ok=True)
    raw_path.write_text(quote['raw_text'])
    quote_sha = c.sha_bytes(raw_path.read_bytes())
    metadata = dict(race_id=rid, source_url=quote['source_url'], fetch_started_at=qstart,
        received_at=received, available_by_at=clock.now(), source_sha256=quote_sha, origin='local_fixture')
    meta_ref = save('quote/metadata.json', metadata)
    quote_ref = dict(source_kind='saved_quote_v1', source_path='quote/raw.json', source_sha256=quote_sha,
        metadata_path=meta_ref['source_path'], metadata_sha256=meta_ref['source_sha256'])
    rows = input_rows(c.make_protocol(), quote)
    feature_refs, predictions = {}, {}
    for kind in ('legacy', 'mev'):
        generated = origin if kind == 'legacy' else clock.now()
        body = dict(artifact_kind='model_feature_snapshot_v1', schema_version=1, mode=MODE,
            eligible_for_prospective=False, kind=kind, race_id=rid, race_date=day,
            generated_at=generated, observed_by_at=generated, rows=rows[kind], history=[],
            source_times={key:generated for key in rows[kind][0]['features']},
            row_available_at={r['horse_id']:generated for r in rows[kind]}, history_available_at=generated,
            quote_sha256=quote_sha if kind == 'mev' else None)
        ref = save(f'features/{kind}.json', body)
        receipt, _ = ready(f'features/{kind}-ready.json', 'feature_ready_v1', ref['source_sha256'])
        feature_refs[kind] = dict(source_kind='feature_snapshot_v1', **ref, **receipt)
        packet = materialize(feature_refs[kind])
        available, start = clock.now(), clock.now()
        extra = dict(history=[]) if kind == 'legacy' else {}
        result = (models.infer_legacy if kind == 'legacy' else models.infer_mev)(rows[kind],
            race_id=rid, race_date=day, **extra)
        computed = clock.now()
        p = dict(run_id='fixture-'+kind, producer='actual_model', model_version=c.make_protocol()['model_versions'][kind],
            model_sha256=c.make_protocol()['model_shas'][kind], input_available_at=available,
            started_at=start, computed_at=computed, evidence=result['evidence'], rows=[dict(**r,
                probability=result['probabilities'][r['horse_id']], input_asof=packet['available_by_at'],
                created_at=start, updated_at=computed) for r in rows[kind]])
        ref = save(f'predictions/{kind}.json', dict(artifact_kind='model_prediction_snapshot_v1', mode=MODE,
            eligible_for_prospective=False, kind=kind, race_id=rid, race_date=day,
            feature_packet_sha256=packet['packet_sha256'], prediction=p))
        receipt, _ = ready(f'predictions/{kind}-ready.json', 'prediction_ready_v1', ref['source_sha256'])
        predictions[kind] = dict(**ref, **receipt)
    after, after_anchor = guard('after')
    final, _ = guard('decision')
    return dict(schema_version=1, mode=MODE, eligible_for_prospective=False,
        race_id=rid, race_date=day, post_time=post, decision_at=clock.now(), field=field,
        features=feature_refs, predictions=predictions, quote=quote_ref,
        guards=dict(before=before, after=after, decision=final),
        stage_anchors=dict(before=qstart, after=after_anchor)), clock
