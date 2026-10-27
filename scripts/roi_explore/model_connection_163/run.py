"""Offline real-model connection and real-clock storage check; acquisition stays disabled."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.roi_explore.prospective_rehearsal_162.rehearsal import offline_only  # noqa: E402
from scripts.roi_explore.prospective_rehearsal_162.store import publish, read  # noqa: E402
from scripts.roi_explore.model_connection_163 import contract as c  # noqa: E402
from scripts.roi_explore.model_connection_163.clock import RealClock, verify_observations  # noqa: E402
from scripts.roi_explore.model_connection_163.quotes import (  # noqa: E402
    CollectionDisabled, StoppedQuoteAdapter, make_local_fixture, parse_saved,
)
from scripts.roi_explore.model_connection_163.replay import historical_official_replay  # noqa: E402
from scripts.roi_explore.model_connection_163.store import EvidenceStore  # noqa: E402


@contextmanager
def connection_disabled():
    def denied(*args, **kwargs):
        raise RuntimeError('database_connection_disabled')
    with offline_only(), patch('sqlalchemy.engine.Engine.connect', denied), \
        patch('sqlalchemy.engine.Engine.raw_connection', denied):
        yield


def input_rows(protocol, quote):
    """Explicit synthetic unknowns; never fetch or rebuild current mutable history."""
    derived = c.market_features(quote['rows'])
    result = {kind: [] for kind in ('mev', 'legacy')}
    for quote_row in quote['rows']:
        h, n = quote_row['horse_id'], quote_row['horse_number']
        for kind in result:
            features = dict.fromkeys(protocol['feature_columns'][kind])
            features.update(horse_number=n, field_size=len(quote['rows']))
            if kind == 'mev':
                features.update({k: v for k, v in derived[h].items() if k in features})
            else:
                features.update(career_starts=0, days_since_last=None, sex='牡')
            result[kind].append(dict(horse_id=h, horse_number=n, features=features))
    return result


def connected_bundle(protocol, clock):
    from scripts.roi_explore.model_connection_163.models import infer_legacy, infer_mev
    began = c.time(clock.now())
    # Explicitly synthetic schedule. It is never looked up or registered in a race database.
    post = began+dt.timedelta(seconds=595)
    day = post.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    rid = f'{post.year}99990001'
    fields = [dict(horse_id=f'local-fixture-{i}', horse_number=i) for i in range(1, 4)]
    guard = dict(at=clock.now(), pending=True, field=fields, post_time=post.isoformat())
    fetch_started = clock.now()
    prototype = make_local_fixture(race_id=rid, rows=[dict(**r, odds=str(o), popularity=i)
        for i, (r, o) in enumerate(zip(fields, (2, 4, 8), strict=True), 1)],
        fetch_started_at=fetch_started, received_at=clock.now())
    # Observe completion AFTER the local raw body exists. It is not a remote acquisition.
    quote = parse_saved(prototype['raw_text'], race_id=rid, expected_field=fields,
        source_url=prototype['source_url'], fetch_started_at=fetch_started,
        received_at=clock.now(), source='local_fixture')
    inputs = input_rows(protocol, quote)
    available = clock.now()
    predictions = {}
    for kind in ('mev', 'legacy'):
        start = clock.now()
        if kind == 'mev':
            output = infer_mev(inputs[kind], race_id=rid, race_date=day)
        else:
            output = infer_legacy(inputs[kind], race_id=rid, race_date=day, history=[])
        computed = clock.now()
        predictions[kind] = dict(run_id=uuid.uuid4().hex, producer='actual_model',
            model_version=protocol['model_versions'][kind], model_sha256=protocol['model_shas'][kind],
            input_available_at=available, started_at=start, computed_at=computed,
            rows=[dict(**r, probability=output['probabilities'][r['horse_id']], input_asof=available,
                created_at=start, updated_at=computed) for r in inputs[kind]], evidence=output['evidence'])
    return dict(schema_version=1, mode=c.MODE, simulated_clock=False, eligible_for_prospective=False,
        synthetic_post=True, race_id=rid, session_id=uuid.uuid4().hex, post_time=post.isoformat(),
        race_date=day, field=fields, guard_before=guard, quote=quote, predictions=predictions)


def recompute(bundle):
    from scripts.roi_explore.model_connection_163.models import infer_legacy, infer_mev
    checks = {}
    for kind in ('mev', 'legacy'):
        prediction = bundle['predictions'][kind]
        rows = [dict(horse_id=r['horse_id'], horse_number=r['horse_number'], features=r['features'])
            for r in prediction['rows']]
        args = dict(race_id=bundle['race_id'], race_date=bundle['race_date'])
        if kind == 'legacy':
            history = prediction['evidence']['started_history']
            records = [dict(zip(history['columns'], r, strict=True)) for r in history['rows']]
            output = infer_legacy(rows, **args, history=records)
        else:
            output = infer_mev(rows, **args)
        expected = {r['horse_id']: r['probability'] for r in prediction['rows']}
        c.require(output['probabilities'] == expected and output['evidence'] == prediction['evidence'],
            'actual_model_replay_mismatch')
        checks[kind] = dict(horses=len(rows), probabilities_sha256=c.digest(expected),
            actual_model_replay=True)
    return checks


def freeze(root):
    dependencies = [ROOT/'scripts/roi_explore/prospective_rehearsal_162'/n for n in
        ('contract.py', 'store.py', 'settlement.py', 'rehearsal.py')]
    dependencies += list(Path(__file__).parent.glob('*.py'))
    dependencies += [ROOT/p for p in (
        'serving/src/horseracing_serving/predictor.py',
        'training/src/horseracing_training/artifacts.py',
        'training/src/horseracing_training/calibration.py',
        'eval/src/horseracing_eval/baselines.py',
        'eval/src/horseracing_eval/predictor.py',
        'features/src/horseracing_features/registry.py',
        'features/src/horseracing_features/race_class_canon.py',
        'features/src/horseracing_features/weight_mask.py')]
    # Pin existing producers/feature-code dependencies as recorded by model adapters too.
    b = read(root/'sessions/races'/read(root/'report.json')['race_id']/'inputs.json')
    for prediction in b['predictions'].values():
        dependencies += [ROOT/p for p in prediction['evidence']['source_files']]
    publish(root/'manifest.json', dict(mode=c.MODE, eligible_for_prospective=False,
        files={str(p.relative_to(root)): c.sha_bytes(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()},
        source_files={str(p.relative_to(ROOT)): c.sha_bytes(p.read_bytes()) for p in sorted(set(dependencies))}))


def run(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    protocol = c.make_protocol()
    clock = RealClock()
    publish(root/'protocol.json', protocol)
    try:
        StoppedQuoteAdapter().fetch('https://race.netkeiba.com/')
        raise AssertionError('collection unexpectedly enabled')
    except CollectionDisabled:
        pass
    b = connected_bundle(protocol, clock)
    store = EvidenceStore(root/'sessions', protocol, clock)
    store.prepare(b)
    guard = dict(at=clock.now(), pending=True, field=b['field'], post_time=b['post_time'])
    decision = store.seal(b['race_id'], guard)
    pending = dict(mode=c.MODE, eligible_for_prospective=False, race_id=b['race_id'],
        status='pending', reason='official_result_not_available_for_synthetic_future_post',
        recorded_at=clock.now(), arms={a: dict(payout_yen=None, profit_yen=None) for a in c.ARMS})
    publish(root/'pending-settlement.json', pending)
    model_checks = recompute(b)
    replay = historical_official_replay()
    publish(root/'historical-official-replay.json', replay)
    verify_observations(clock.observations)
    publish(root/'clock.json', dict(simulated_clock=False, epoch_id=clock.epoch_id,
        observations=clock.observations))
    report = dict(mode=c.MODE, simulated_clock=False, synthetic_post=True, eligible_for_prospective=False,
        new_collection=False, database_access=False, new_fits=0, actual_model_inference=True,
        race_id=b['race_id'], field_size=len(b['field']), prediction_members=dict(mev=15, legacy=6),
        storage_root=str(root.resolve()), decision_at=decision['decision_at'], sealed_at=decision['sealed_at'],
        quote_age_seconds=(c.time(decision['sealed_at'])-c.time(b['quote']['received_at'])).total_seconds(),
        model_replay=model_checks, current_settlement='pending', historical_replay_races=replay['races'],
        collection_adapter='disabled_unconditionally', comparison_data_added=0)
    report['runtime'] = runtime()
    publish(root/'report.json', report)
    freeze(root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def verify(destination):
    root = Path(destination)
    manifest = read(root/'manifest.json')
    c.require(set(manifest['files']) | {'manifest.json'} == {str(p.relative_to(root))
        for p in root.rglob('*') if p.is_file()}, 'output_inventory_changed')
    for path, checksum in manifest['files'].items():
        c.require(c.sha_bytes((root/path).read_bytes()) == checksum, 'output_tampered:'+path)
    for path, checksum in manifest['source_files'].items():
        c.require(c.sha_bytes((ROOT/path).read_bytes()) == checksum, 'source_changed:'+path)
    report = read(root/'report.json')
    c.require(report['runtime'] == runtime(), 'runtime_changed')
    clock = read(root/'clock.json')
    c.require(clock['simulated_clock'] is False and clock['epoch_id'] == clock['observations'][0]['epoch_id'],
        'clock_metadata_changed')
    verify_observations(clock['observations'])
    protocol = read(root/'protocol.json')
    store = EvidenceStore(root/'sessions', protocol, RealClock())
    decision = store.verify(report['race_id'])
    path = root/'sessions/races'/report['race_id']
    bundle, ready, seal = read(path/'inputs.json'), read(path/'ready.json'), read(path/'seal.json')
    observed = {o['at'] for o in clock['observations']}
    for stamp in [bundle['quote']['fetch_started_at'], bundle['quote']['received_at'],
        bundle['guard_before']['at'], decision['guard']['at'], ready['available_by_at'],
        decision['decision_at'], seal['observed_saved_at'], decision['sealed_at']]:
        c.require(stamp in observed, 'timestamp_not_in_clock_observations')
    for prediction in bundle['predictions'].values():
        for key in ('input_available_at', 'started_at', 'computed_at'):
            c.require(prediction[key] in observed, 'prediction_clock_missing')
    c.require(report['model_replay'] == recompute(bundle), 'model_report_changed')
    c.require(read(root/'historical-official-replay.json') == historical_official_replay(),
        'historical_replay_changed')
    pending = read(root/'pending-settlement.json')
    c.require(pending['status'] == 'pending' and all(v['payout_yen'] is None and v['profit_yen'] is None
        for v in pending['arms'].values()), 'pending_settlement_was_valued')
    c.require(report['eligible_for_prospective'] is False and report['comparison_data_added'] == 0
        and report['new_collection'] is False, 'offline_report_required')
    result = dict(status='verified', mode=c.MODE, actual_model_replay=True,
        files=len(manifest['files']), eligible_for_prospective=False)
    print(json.dumps(result))
    return result


def runtime():
    return dict(python=sys.version, platform=sys.platform, machine=os.uname().machine,
        packages={name: version(name) for name in ('numpy', 'pandas', 'lightgbm', 'scipy', 'scikit-learn')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('rehearse', 'verify'):
        child = sub.add_parser(name)
        child.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with connection_disabled():
        (run if args.command == 'rehearse' else verify)(args.out)


if __name__ == '__main__':
    main()
