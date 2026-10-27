"""Read-only saved real-race as-of connection audit; no collect/live/DB command."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.roi_explore.model_connection_163 import contract as c  # noqa: E402
from scripts.roi_explore.model_connection_163.clock import RealClock, verify_observations  # noqa: E402
from scripts.roi_explore.model_connection_163.replay import historical_official_replay  # noqa: E402
from scripts.roi_explore.model_connection_163.run import connection_disabled  # noqa: E402
from scripts.roi_explore.prospective_rehearsal_162.store import publish, read  # noqa: E402
from scripts.roi_explore.asof_connection_164 import sources  # noqa: E402
from scripts.roi_explore.asof_connection_164.contract import inspect_candidate, validate_quote  # noqa: E402
from scripts.roi_explore.asof_connection_164.features import MODE, materialize, validate_features  # noqa: E402
from scripts.roi_explore.asof_connection_164.guards import inspect_guard  # noqa: E402
from scripts.roi_explore.asof_connection_164.store import AsOfEvidenceStore  # noqa: E402


def chaos_references():
    import pandas as pd
    manifest = json.loads((ROOT/'artifacts/158-prerace-roi-comparison/snapshot/manifest.json').read_text())
    entry = manifest['tables']['chaos']
    path = ROOT/entry['path']
    c.require(c.sha_bytes(path.read_bytes()) == entry['sha256'], 'chaos_source_changed')
    frame = pd.read_parquet(path)
    return {r.race_id: dict(source_kind='chaos158', source_sha256=entry['sha256'],
        snapshot_id=r.chaos_snapshot_id) for r in frame.itertuples()}


def candidate_from_source(component, quote):
    reference = dict(source_kind='capture133', source_path=component['source_ref']['path'],
        source_sha256=component['source_ref']['sha256'])
    raw = json.loads((ROOT/reference['source_path']).read_text())
    decision = (c.time(component['post_time'])-dt.timedelta(seconds=600)).isoformat()
    return dict(schema_version=1, mode=MODE, eligible_for_prospective=False,
        race_id=component['race_id'], race_date=component['race_date'], post_time=component['post_time'],
        decision_at=decision, field=component['field'], features=dict(legacy=reference, mev=None),
        guards={s: reference for s in ('before', 'after', 'decision')},
        stage_anchors=dict(before=raw['timeline']['acquisition_started_at'],
            after=raw['timeline']['post_prediction_check_at']), quote=quote,
        predictions=dict(legacy=None, mev=None))


def _freeze(root):
    source_files = list(Path(__file__).parent.glob('*.py'))
    source_files += list((ROOT/'scripts/roi_explore/model_connection_163').glob('*.py'))
    source_files += [ROOT/'scripts/roi_explore/prospective_rehearsal_162'/n for n in
        ('contract.py', 'store.py', 'settlement.py', 'rehearsal.py')]
    input_files = [*sources.CAPTURES.glob('*.json'), sources.PIN_INDEX, sources.PIN_RECEIPT,
        ROOT/'artifacts/158-prerace-roi-comparison/snapshot/manifest.json',
        ROOT/'artifacts/158-prerace-roi-comparison/snapshot/chaos.parquet']
    replay = read(root/'journal/component-replays/202609040611/legacy/prediction.json')
    source_files += [ROOT/name for name in replay['result']['evidence']['source_files']]
    input_files += [ROOT/name for name in c.make_protocol()['model_files']]
    publish(root/'manifest.json', dict(mode=MODE, eligible_for_prospective=False,
        files={str(p.relative_to(root)): c.sha_bytes(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()},
        source_files={str(p.relative_to(ROOT)): c.sha_bytes(p.read_bytes()) for p in sorted(set(source_files))},
        inputs={str(p.relative_to(ROOT)): c.sha_bytes(p.read_bytes()) for p in sorted(input_files)}))


def run(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    clock = RealClock()
    store = AsOfEvidenceStore(root/'journal', clock)
    inventory = sources.inventory()
    publish(root/'source-inventory.json', inventory)
    quotes = chaos_references()
    ledger = []
    for path in sorted(sources.CAPTURES.glob('*.json')):
        source = sources.load_capture(path)
        candidate = candidate_from_source(source, quotes.get(source['race_id']))
        report = store.record_candidate(candidate)
        ref = candidate['features']['legacy']
        packet = materialize(ref)
        publish(root/'components'/source['race_id']/'legacy-features.json', packet)
        partial = {stage: inspect_guard(candidate['guards'][stage], stage=stage)
            for stage in ('before', 'after', 'decision')}
        publish(root/'components'/source['race_id']/'guard-components.json', partial)
        try:
            validate_features(ref, kind='legacy', race_id=source['race_id'], race_date=source['race_date'],
                expected_field=source['field'], decision_at=candidate['decision_at'])
            memory_by_target = True
        except ValueError:
            memory_by_target = False
        quote_status = 'missing'
        if candidate['quote']:
            try:
                q = validate_quote(candidate['quote'], race_id=source['race_id'],
                    expected_field=source['field'], decision_at=candidate['decision_at'])
                quote_status = 'fresh_field_only' if not q['raw_response_retained'] else 'complete'
            except ValueError as error:
                quote_status = str(error)
        ledger.append(dict(race_id=source['race_id'], race_date=source['race_date'],
            horses=len(source['field']), original_feature_available_at=source['feature_available_at'],
            reference_decision_at=candidate['decision_at'], legacy_memory_available_by_target=memory_by_target,
            quote_status=quote_status, contract_ready=report['contract_ready'], failures=report['failures']))
    # Execute a real saved 16-horse/340-history legacy input; record CURRENT clocks only.
    representative = sources.load_capture(sources.CAPTURES/'202609040611.json')
    selected = candidate_from_source(representative, quotes.get(representative['race_id']))
    replay = store.replay_component(selected['features']['legacy'])
    maximum = max(abs(replay['result']['probabilities'][h]-p) for h,p in representative['original_prediction'].items())
    c.require(maximum <= 1e-14, 'saved_legacy_replay_changed')
    try:
        store.seal_now(selected)
        raise AssertionError('historical_source_received_a_new_seal')
    except ValueError as error:
        c.require(str(error) == 'historical_reference_cannot_be_sealed_now', 'unexpected_historical_gate')
    official = historical_official_replay()
    publish(root/'historical-official-replay.json', official)
    failure_counts = Counter(r['gate']+':'+r['reason'] for row in ledger for r in row['failures'])
    summary = dict(mode=MODE, eligible_for_prospective=False, new_collection=False, database_access=False,
        races=len(ledger), horses=sum(r['horses'] for r in ledger), legacy_feature_components_verified=len(ledger),
        legacy_memory_available_by_fixed_target=sum(r['legacy_memory_available_by_target'] for r in ledger),
        complete_mev_materialized_tables=sum(r['complete_mev_columns'] for r in inventory['mev_materialized_tables']),
        mev_asof_availability_proven=0, contract_ready=sum(r['contract_ready'] for r in ledger),
        new_comparison_races=0, historical_seals_created=0,
        current_replay=dict(race_id=representative['race_id'], horses=len(representative['field']),
            history_rows=len(representative['legacy_history']), max_abs_original_probability_difference=maximum,
            computed_at=replay['computed_at'], usable_as_original_prediction=False),
        official_settlement_replay_races=official['races'], failure_counts=dict(sorted(failure_counts.items())))
    c.require(summary['races'] == 46 and summary['horses'] == 621 and summary['contract_ready'] == 0,
        'saved_source_coverage_changed')
    publish(root/'ledger.json', ledger)
    publish(root/'report.json', summary)
    publish(root/'clock.json', dict(simulated_clock=False, observations=clock.observations))
    _freeze(root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def verify(destination):
    root = Path(destination)
    manifest = read(root/'manifest.json')
    c.require(set(manifest['files']) | {'manifest.json'} == {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()},
        'output_inventory_changed')
    for section in ('files', 'source_files', 'inputs'):
        for name, checksum in manifest[section].items():
            path = (root if section == 'files' else ROOT)/name
            c.require(c.sha_bytes(path.read_bytes()) == checksum, 'frozen_file_changed:'+name)
    report = read(root/'report.json')
    c.require(report['mode'] == MODE and report['eligible_for_prospective'] is False
        and report['new_comparison_races'] == report['historical_seals_created'] == 0, 'saved_mode_changed')
    c.require(read(root/'source-inventory.json') == sources.inventory(), 'source_inventory_changed')
    for candidate_path in sorted((root/'journal/candidates').glob('*/reference.json')):
        candidate = read(candidate_path)
        c.require(inspect_candidate(candidate) == read(candidate_path.with_name('checks.json')), 'candidate_replay_changed')
        packet = materialize(candidate['features']['legacy'])
        c.require(packet == read(root/'components'/candidate['race_id']/'legacy-features.json'), 'feature_packet_changed')
        for stage, original in read(root/'components'/candidate['race_id']/'guard-components.json').items():
            c.require(inspect_guard(candidate['guards'][stage], stage=stage) == original, 'guard_component_changed')
    prediction_path = root/'journal/component-replays/202609040611/legacy/prediction.json'
    replay = read(prediction_path)
    from scripts.roi_explore.model_connection_163.models import infer_legacy
    packet = materialize(replay['source_ref'])
    result = infer_legacy(packet['rows'], race_id=packet['race_id'], race_date=packet['race_date'], history=packet['history'])
    c.require(replay['result'] == result and replay['historical_ready_receipt_created'] is False
        and replay['usable_as_original_prediction'] is False, 'current_component_replay_changed')
    ready = read(prediction_path.with_name('current-ready.json'))
    c.require(ready['source_sha256'] == c.digest(replay) and c.time(ready['available_by_at']) >= c.time(replay['computed_at'])
        and ready['historical'] is False, 'current_replay_receipt_invalid')
    clock = read(root/'clock.json')
    c.require(clock['simulated_clock'] is False, 'current_clock_relabelled')
    verify_observations(clock['observations'])
    stamps = {r['at'] for r in clock['observations']}
    c.require({replay['started_at'], replay['computed_at'], ready['available_by_at']} <= stamps,
        'current_replay_clock_missing')
    c.require(read(root/'historical-official-replay.json') == historical_official_replay(), 'official_replay_changed')
    c.require(not list((root/'journal').glob('decisions/*/accepted.json')), 'historical_admission_created')
    result = dict(status='verified', mode=MODE, races=46, actual_saved_legacy_replay=True,
        eligible_for_prospective=False, new_comparison_races=0)
    print(json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('rehearse', 'verify'):
        child = sub.add_parser(command)
        child.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with connection_disabled():
        (run if args.command == 'rehearse' else verify)(args.out)


if __name__ == '__main__':
    main()
