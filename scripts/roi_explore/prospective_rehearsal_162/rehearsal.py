"""Run/verify a synthetic offline rehearsal. No collect, live or scheduler command exists."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import sqlite3
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.roi_explore.prospective_rehearsal_162 import contract as c  # noqa: E402
from scripts.roi_explore.prospective_rehearsal_162 import fixtures  # noqa: E402
from scripts.roi_explore.prospective_rehearsal_162.store import (  # noqa: E402
    EvidenceStore, SimulatedClock, publish, read,
)


@contextmanager
def offline_only():
    def denied(*args, **kwargs):
        raise RuntimeError('network_and_subprocess_disabled')
    with ExitStack() as stack:
        for name in ('connect', 'connect_ex', 'send', 'sendall', 'sendto', 'recv', 'recvfrom',
            'bind', 'listen', 'accept'):
            stack.enter_context(patch.object(socket.socket, name, denied))
        for owner, name in [(socket, 'create_connection'), (socket, 'getaddrinfo'),
            (subprocess, 'Popen'), (os, 'system'), (sqlite3, 'connect')]:
            stack.enter_context(patch.object(owner, name, denied))
        yield


def historical_inventory():
    """Do not fabricate missing inputs or past durability receipts for the old nine races."""
    base = ROOT/'artifacts/159-prerace-fresh30'
    manifest = json.loads((base/'output-sha256.json').read_text())['files']
    path = base/'prepared/judgments.parquet'
    c.require(c.sha_bytes(path.read_bytes()) == manifest[str(path.relative_to(ROOT))], 'historical_source_changed')
    import pandas as pd
    frame = pd.read_parquet(path)
    c.require(len(frame) == 129 and frame.race_id.nunique() == 9, 'historical_source_population_changed')
    return dict(mode='historical_saved_inventory', eligible_for_prospective=False,
        source_sha256=c.sha_bytes(path.read_bytes()), races=9, horses=129,
        complete_contract_accepted=0, reasons=['mev_full_input_matrix_not_saved',
            'quote_response_body_not_saved', 'durable_ready_and_seal_receipts_not_saved'],
        fabricated_past_receipts=False, new_inference=False)


def run(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    protocol = c.make_protocol()
    publish(root/'protocol.json', protocol)
    scenarios = ['normal', 'missing_payout', 'dead_heat', 'stale_quote', 'late_prediction',
        'quote_future', 'not_preweight', 'missing_input', 'field_changed', 'raw_tamper', 'result_appeared']
    schedule = [dict(race_id=f'2026050409{i:02}', scenario=s) for i, s in enumerate(scenarios, 1)]
    publish(root/'schedule.json', dict(mode=c.MODE, synthetic=True, races=schedule))
    coverage = []
    for target in schedule:
        b = fixtures.bundle(protocol, target['race_id'])
        scenario = target['scenario']
        if scenario == 'stale_quote':
            for key in ('received_at', 'fetch_started_at'):
                b['quote'][key] = (c.time(b['quote'][key])-dt.timedelta(minutes=31)).isoformat()
            b['guard_before']['at'] = b['quote']['fetch_started_at']
        elif scenario == 'late_prediction':
            b['predictions']['legacy']['computed_at'] = b['post_time']
        elif scenario == 'quote_future':
            b['quote']['received_at'] = b['post_time']
        elif scenario == 'not_preweight':
            b['predictions']['legacy']['rows'][0]['features']['weight'] = 480
        elif scenario == 'missing_input':
            b['predictions']['mev']['rows'][0]['features'].pop('odds')
        elif scenario == 'raw_tamper':
            b['quote']['raw_text'] += 'changed'
        clock = SimulatedClock((c.time(b['post_time'])-dt.timedelta(minutes=11)).isoformat())
        store = EvidenceStore(root/'sessions', protocol, clock)
        guard = fixtures.final_guard(b)
        if scenario == 'field_changed':
            guard['field'] = b['field'][:-1]
        elif scenario == 'result_appeared':
            guard['pending'] = False
        row = dict(**target, accepted=False, settlement_status=None)
        try:
            store.prepare(b)
            clock.advance_to((c.time(guard['at'])+dt.timedelta(seconds=1)).isoformat())
            store.seal(b['race_id'], guard)
            official = fixtures.official(b, missing=scenario == 'missing_payout',
                winners={1: 150, 2: 280} if scenario == 'dead_heat' else None)
            result = store.add_settlement(b['race_id'], official)
            store.verify_settlements(b['race_id'])
            row.update(accepted=True, settlement_status=result['status'])
        except (ValueError, KeyError) as exc:
            row['reason'] = str(exc)
            store.abort(b['race_id'], str(exc))
        coverage.append(row)
    summary = dict(mode=c.MODE, simulated_clock=True, eligible_for_prospective=False,
        collection_enabled=False, database_access=False, new_inference=False,
        synthetic_races=len(schedule), accepted=sum(r['accepted'] for r in coverage),
        rejected=sum(not r['accepted'] for r in coverage), coverage=coverage,
        historical_inventory=historical_inventory())
    c.require(summary['accepted'] == 3 and summary['rejected'] == 8, 'rehearsal_expectation_failed')
    publish(root/'report.json', summary)
    freeze_outputs(root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def freeze_outputs(root):
    publish(root/'manifest.json', dict(mode=c.MODE, eligible_for_prospective=False,
        source_files={str(p.relative_to(ROOT)): c.sha_bytes(p.read_bytes()) for p in sorted(Path(__file__).parent.glob('*.py'))},
        files={str(p.relative_to(root)): c.sha_bytes(p.read_bytes()) for p in sorted(root.rglob('*'))
            if p.is_file() and p.name != 'manifest.json'}))


def verify(destination):
    root = Path(destination)
    manifest = read(root/'manifest.json')
    expected = set(manifest['files']) | {'manifest.json'}
    c.require(expected == {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}, 'file_inventory_changed')
    for name, checksum in manifest['files'].items():
        c.require(c.sha_bytes((root/name).read_bytes()) == checksum, 'output_changed:'+name)
    for name, checksum in manifest['source_files'].items():
        c.require(c.sha_bytes((ROOT/name).read_bytes()) == checksum, 'source_changed:'+name)
    protocol = read(root/'protocol.json')
    store = EvidenceStore(root/'sessions', protocol, SimulatedClock('2026-10-25T00:00:00Z'))
    report = read(root/'report.json')
    c.require(report['mode'] == c.MODE and report['eligible_for_prospective'] is False, 'offline_mode_required')
    c.require(read(root/'schedule.json')['races'] == [dict(race_id=r['race_id'], scenario=r['scenario'])
        for r in report['coverage']], 'coverage_schedule_mismatch')
    for row in report['coverage']:
        if row['accepted']:
            store.verify(row['race_id'])
            results = store.verify_settlements(row['race_id'])
            c.require(len(results) == 1 and results[0]['status'] == row['settlement_status'], 'settlement_report_mismatch')
        else:
            race_path = store._race(row['race_id'])
            c.require(not (race_path/'accepted.json').exists() and not (race_path/'inputs.json').exists(), 'failed_session_has_input')
            c.require(read(race_path/'failure.json')['reason'] == row['reason'], 'failure_report_mismatch')
    print(json.dumps(dict(status='verified', mode=c.MODE, files=len(manifest['files']),
        accepted=sum(r['accepted'] for r in report['coverage']), eligible_for_prospective=False)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('rehearse', 'verify'):
        p = sub.add_parser(command)
        p.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    with offline_only():
        (run if args.command == 'rehearse' else verify)(args.out)


if __name__ == '__main__':
    main()
