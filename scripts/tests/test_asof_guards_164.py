from __future__ import annotations

import copy
import gzip
from pathlib import Path

import pandas as pd
import pytest

from scripts.roi_explore.asof_connection_164 import guards as g
from scripts.roi_explore.prospective_rehearsal_162.contract import canonical, sha_bytes, time

ROOT = Path(__file__).resolve().parents[2]
FIELD = [dict(horse_id='canonical-a', horse_number=1), dict(horse_id='nk:canonical-b', horse_number=2)]
POST = '2026-10-25T06:00:00+00:00'
AT = '2026-10-25T05:50:01+00:00'
OBS = '2026-10-25T05:50:00+00:00'


def _persist(root, name, value):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))
    return str(path.relative_to(root)), sha_bytes(path.read_bytes())


@pytest.fixture
def query(tmp_path, monkeypatch):
    monkeypatch.setattr(g, 'ROOT', tmp_path)
    def make(stage='decision', mutate=None, receipt_mutate=None):
        raw = dict(artifact_kind='race_guard_query_evidence_v1', schema_version=1,
            mode='offline_asof_connection', eligible_for_prospective=False,
            race_id='202605040907', stage=stage, observations=dict(
                race=dict(started_at=OBS, completed_at=OBS, rows=[dict(race_id='202605040907',
                    race_date='2026-10-25', post_time=POST)]),
                entries=dict(started_at=OBS, completed_at=OBS,
                    rows=[dict(**r, entry_status='started') for r in FIELD]),
                results=dict(started_at=OBS, completed_at=OBS, rows=[])))
        if mutate:
            mutate(raw)
        source_path, source_sha256 = _persist(tmp_path, 'query.json', raw)
        receipt = dict(artifact_kind='race_guard_ready_v1', mode='offline_asof_connection',
            eligible_for_prospective=False, source_sha256=source_sha256, available_by_at=OBS)
        if receipt_mutate:
            receipt_mutate(receipt)
        receipt_path, receipt_sha256 = _persist(tmp_path, 'ready.json', receipt)
        return dict(source_kind='guard_observation_v1', source_path=source_path,
            source_sha256=source_sha256, receipt_path=receipt_path, receipt_sha256=receipt_sha256)
    return make


def validate(record, **overrides):
    args = dict(expected_field=FIELD, race_id='202605040907', decision_at=AT,
        post_time=POST, stage='decision')
    args.update(overrides)
    return g.validate_guard(record, **args)


@pytest.mark.parametrize('stage', ['before', 'after', 'decision'])
def test_complete_saved_query_guard(query, stage):
    evidence = validate(query(stage), stage=stage)
    assert evidence['pending'] is True and evidence['field_reobserved']
    assert evidence['result_rows_count'] == 0 and evidence['eligible_for_prospective'] is False
    assert set(evidence['observation_clocks']) == {
        f'{k}_{t}_at' for k in ('race', 'entries', 'results') for t in ('started', 'completed')}


def test_guard_payload_cannot_self_declare_pending(query):
    def mutate(raw):
        raw['observations']['results']['rows'] = [dict(race_id='202605040907', horse_id='canonical-a')]
    with pytest.raises(ValueError, match='pending_unproven_or_results_present'):
        validate(query(mutate=mutate))


def test_field_change_and_no_id_alias_guess(query):
    record = query()
    changed = copy.deepcopy(FIELD)
    changed[1]['horse_id'] = 'canonical-b'
    with pytest.raises(ValueError, match='guard_field_changed'):
        validate(record, expected_field=changed)


def test_cancelled_entry_is_removed_from_active_field(query):
    def mutate(raw):
        rows = raw['observations']['entries']['rows']
        rows.append(dict(horse_id='cancelled-c', horse_number=3, entry_status='cancelled'))
    evidence = validate(query(mutate=mutate))
    assert len(evidence['field']) == 2 and len(evidence['entries']) == 3


def test_duplicate_or_unknown_entry_rejected(query):
    def mutate(raw):
        raw['observations']['entries']['rows'][1]['entry_status'] = 'unknown'
    with pytest.raises(ValueError, match='entry_invalid'):
        validate(query(mutate=mutate))


def test_stale_query_start_is_not_fresh_at_completion(query):
    def mutate(raw):
        raw['observations']['results']['started_at'] = '2026-10-25T04:50:00+00:00'
    with pytest.raises(ValueError, match='stale_or_future'):
        validate(query(mutate=mutate))


def test_future_query_or_receipt_rejected(query):
    with pytest.raises(ValueError, match='available_before_decision'):
        validate(query(receipt_mutate=lambda r: r.update(available_by_at='2026-10-25T05:50:02+00:00')))


def test_receipt_cannot_precede_observation(query):
    with pytest.raises(ValueError, match='receipt_before_observation'):
        validate(query(receipt_mutate=lambda r: r.update(available_by_at='2026-10-25T05:49:59+00:00')))


def test_receipt_and_source_hashes_bound(query, tmp_path):
    record = query()
    (tmp_path/'query.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='source_sha_mismatch'):
        validate(record)


def test_guard_stage_cannot_be_reused(query):
    with pytest.raises(ValueError, match='stage_mismatch'):
        validate(query('after'), stage='decision')


def test_guard_age_cannot_be_relaxed(query):
    with pytest.raises(ValueError, match='age_policy_invalid'):
        validate(query(), max_age_seconds=1800)


def test_guard_post_change_rejected(query):
    with pytest.raises(ValueError, match='post_changed'):
        validate(query(), post_time='2026-10-25T06:01:00+00:00')


def test_rebased_metadata_clock_rejected(query):
    record = query()
    record['observed_at'] = '2026-10-25T05:50:01+00:00'
    with pytest.raises(ValueError, match='source_clock_mismatch'):
        validate(record)


def test_guard_path_traversal_and_symlink(query, tmp_path):
    record = query()
    record['source_path'] = '../query.json'
    with pytest.raises(ValueError, match='path_unsafe'):
        validate(record)
    record = query()
    (tmp_path/'alias.json').symlink_to(tmp_path/'query.json')
    record['source_path'] = 'alias.json'
    with pytest.raises(ValueError, match='path_unsafe'):
        validate(record)


def capture_record():
    path = ROOT/'artifacts/133-prospective-paired-comparison/records/capture/202609040611.json'
    return dict(source_kind='capture133', source_path=str(path.relative_to(ROOT)),
        source_sha256=sha_bytes(path.read_bytes()))


def test_real_capture_field_at_acquisition_only():
    record = capture_record()
    before, after = [g.inspect_guard(record, stage=stage) for stage in ('before', 'after')]
    assert len(before['field']) == 16 and before['field_reobserved'] is True
    assert before['pending'] is True and after['pending'] is True
    assert after['field'] == before['field'] and after['field_reobserved'] is False
    assert before['available_by_at'] is after['available_by_at'] is None
    assert time(before['observed_at']) < time(after['observed_at']) < time(before['post_time'])
    assert after['source_status'] == 'historical_partial'
    with pytest.raises(ValueError, match='field_not_reobserved'):
        g.validate_guard(record, expected_field=before['field'], race_id=before['race_id'],
            decision_at=after['observed_at'], post_time=before['post_time'], stage='after')
    with pytest.raises(ValueError, match='decision_guard_not_saved'):
        g.validate_guard(record, expected_field=before['field'], race_id=before['race_id'],
            decision_at=after['observed_at'], post_time=before['post_time'], stage='decision')
    with pytest.raises(ValueError, match='durable_availability_unproven'):
        g.validate_guard(record, expected_field=before['field'], race_id=before['race_id'],
            decision_at=before['observed_at'], post_time=before['post_time'], stage='before')


def real_html_record():
    inventory = pd.read_parquet(ROOT/'specs/160-saved-timepoint-expansion/evidence/saved_shutuba_inventory.parquet')
    row = inventory.loc[inventory.prepost_158.fillna(False)].iloc[0]
    return dict(source_kind='shutuba_html', source_path=row.archive_path, source_sha256=row.archive_sha256,
        observed_at=row.captured_at.isoformat(), encoding='utf-8')


def test_actual_archived_html_printed_field_and_post_only():
    record = real_html_record()
    evidence = g.inspect_guard(record, stage='decision')
    assert evidence['race_id'] == '202609040109' and len(evidence['field']) == 11
    assert evidence['pending'] is None and evidence['available_by_at'] is None
    assert time(evidence['observed_at']) < time(evidence['post_time'])
    with pytest.raises(ValueError, match='pending_unproven'):
        g.validate_guard(record, expected_field=evidence['field'], race_id=evidence['race_id'],
            decision_at=evidence['observed_at'], post_time=evidence['post_time'], stage='decision')


def test_actual_html_time_cannot_be_loaded_as_current():
    record = real_html_record()
    record['observed_at'] = '2026-10-27T01:00:00+00:00'
    with pytest.raises(ValueError, match='html_timestamp_unverified'):
        g.inspect_guard(record, stage='decision')


def test_unknown_duplicate_entry_table_is_not_silently_ignored(tmp_path, monkeypatch):
    original = real_html_record()
    raw = gzip.decompress((ROOT/original['source_path']).read_bytes()).decode('utf-8')
    # A known prediction table is harmless; an unknown second entry table is ambiguous.
    changed = raw.replace('PredictRap_Table', 'Unrecognized_Table')
    path = tmp_path/Path(original['source_path']).name
    path.write_bytes(gzip.compress(changed.encode()))
    monkeypatch.setattr(g, 'ROOT', tmp_path)
    record = dict(source_kind='shutuba_html', source_path=path.name,
        source_sha256=sha_bytes(path.read_bytes()), observed_at=original['observed_at'], encoding='utf-8')
    with pytest.raises(ValueError, match='guard_html_field_missing'):
        g.inspect_guard(record, stage='decision')
