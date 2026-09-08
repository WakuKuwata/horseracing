"""File-only paired capture must never backdate a prospective prediction."""
import datetime as dt
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from horseracing_serving import mixture_shadow as s


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 8, 2, tzinfo=UTC)


@pytest.mark.parametrize('fault', ['past', 'equal', 'missing', 'naive', 'result', 'negative', 'future_input', 'wrong_day'])
def test_prospective_fails_without_real_prestart_provenance(fault):
    start = NOW + dt.timedelta(hours=1); captured = NOW - dt.timedelta(seconds=1); count = 0
    if fault == 'past': start = NOW - dt.timedelta(seconds=1)
    elif fault == 'equal': start = NOW
    elif fault == 'missing': start = None
    elif fault == 'naive': start = start.replace(tzinfo=None)
    elif fault == 'result': count = 1
    elif fault == 'negative': count = -1
    elif fault == 'future_input': captured = NOW + dt.timedelta(seconds=1)
    else: start = NOW + dt.timedelta(days=1)
    with pytest.raises(ValueError): s.validate_capture('prospective', dt.date(2026, 9, 8), start, count, captured, NOW)


def test_capture_requires_future_start_but_rehearsal_is_explicit():
    s.validate_capture('prospective', dt.date(2026, 9, 8), NOW + dt.timedelta(minutes=1), 0, NOW, NOW)
    s.validate_capture('rehearsal', dt.date(2026, 8, 23), None, 20, NOW, NOW)
    with pytest.raises(ValueError): s.validate_capture('unknown', dt.date(2026, 8, 23), None, 20, NOW, NOW)


def test_append_only_is_atomic_and_rejects_nonfinite_before_writing(tmp_path):
    p = tmp_path / 'record.json'
    s.write_json_new(p, {'a': 1})
    with pytest.raises(FileExistsError): s.write_json_new(p, {'a': 2})
    assert json.loads(p.read_text()) == {'a': 1}
    with pytest.raises(ValueError): s.write_json_new(tmp_path / 'bad.json', {'x': float('nan')})
    assert not (tmp_path / 'bad.json').exists()
    assert sorted(x.name for x in tmp_path.iterdir()) == ['record.json']


def test_history_includes_started_races_without_results_and_excludes_same_day():
    rows = pd.DataFrame({'horse_id': ['h'], 'race_date': [dt.date(2026, 9, 8)]})
    raw = pd.DataFrame({'race_id': ['r1', 'r2', 'r3', 'r4'], 'horse_id': ['h'] * 4,
                        'race_date': [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 8), dt.date(2026, 9, 9)],
                        'entry_status': ['started', 'excluded', 'started', 'started']})
    # Enum values are uppercase in the database; use the actual contract here.
    raw['entry_status'] = [s.EntryStatus.STARTED, s.EntryStatus.EXCLUDED, s.EntryStatus.STARTED, s.EntryStatus.STARTED]
    actual = s.history_for(rows, raw)
    assert actual.race_id.tolist() == ['r1']
    assert not any('result' in c for c in actual.columns)


def test_anchor_integrity_checked_before_loader(tmp_path, monkeypatch):
    artifact = tmp_path / 'model.txt'; artifact.write_text('old')
    p = tmp_path / 'anchor.json'; p.write_text(json.dumps({'schema_version': 1,
        'artifact_kind': 'mixture_shadow_anchor', 'can_adopt': False,
        'files': {str(artifact): s.sha256(artifact)}}))
    artifact.write_text('changed')
    calls = []; monkeypatch.setattr(s, 'load_serving_model', lambda *a: calls.append(a))
    with pytest.raises(ValueError): s.load_anchor(p)
    assert calls == []


def test_frozen_catalog_can_only_resolve_the_bound_model():
    row = SimpleNamespace(model_version='anchor')
    catalog = s.FrozenCatalog(row)
    assert catalog.get(s.ModelVersion, 'anchor') is row
    assert catalog.get(s.ModelVersion, 'wrong') is None
    with pytest.raises(ValueError): catalog.get(str, 'anchor')
