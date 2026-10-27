"""Read preserved 133 source records directly; never fabricate clocks or infer features."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def stamp(value):
    at = dt.datetime.fromisoformat(value)
    assert at.tzinfo is not None
    return at.astimezone(dt.UTC)


def main():
    profile = json.loads((ROOT/'artifacts/129-candidate-mixture-serving/bundle.json').read_text())
    columns = set(profile['feature_profile']['full_columns'])
    rows = []
    for path in sorted((ROOT/'artifacts/133-prospective-paired-comparison/records/capture').glob('*.json')):
        raw = path.read_bytes()
        record = json.loads(raw)
        regime = record['regimes']['preweight']
        encoded = regime['inputs']
        assert set(encoded['columns']) == columns | {'race_id', 'race_date'}
        assert len(encoded['columns']) == len(set(encoded['columns']))
        assert set(encoded['rows']) == set(regime['started_ids'])
        field = []
        for horse, values in encoded['rows'].items():
            assert len(values) == len(encoded['columns'])
            source = dict(zip(encoded['columns'], values, strict=True))
            assert source['race_id'] == record['race_id'] and source['race_date'] == record['race_day']
            assert all(source[k] is None for k in ('weight', 'weight_diff', 'carried_weight_ratio'))
            field.append((horse, source['horse_number']))
        assert len(field) == len(set(field)) and len({n for _, n in field}) == len(field)
        start = stamp(record['timeline']['acquisition_started_at'])
        acquired = stamp(record['timeline']['acquisition_finished_at'])
        predicted = stamp(regime['predicted_at']['new'])
        checked = stamp(record['timeline']['post_prediction_check_at'])
        written, post = stamp(record['record_written_at']), stamp(record['scheduled_start'])
        assert start <= acquired <= predicted <= checked <= written < post
        assert record['n_result_rows_at_capture'] == record['n_result_rows_after_prediction'] == 0
        lead = (post-checked).total_seconds()
        rows.append(dict(race_id=record['race_id'], horses=len(field), source_path=str(path.relative_to(ROOT)),
            source_sha256=hashlib.sha256(raw).hexdigest(), record_written_at=str(written),
            old_prediction_computed_at=str(predicted), post_check_at=str(checked), scheduled_post=str(post),
            post_check_lead_seconds=lead, post_check_in_fixed_window=540 < lead <= 600))
    assert len(rows) == 46
    result = dict(status='passed', scope='raw-source-only spot-check; not a complete paired as-of proof',
        records=len(rows), horses=sum(r['horses'] for r in rows),
        recorded_prestart_input_clocks=True, durable_publication_proven=False, before_after_db_results_zero=True,
        post_check_in_fixed_window=sum(r['post_check_in_fixed_window'] for r in rows),
        min_post_check_lead_seconds=min(r['post_check_lead_seconds'] for r in rows),
        max_post_check_lead_seconds=max(r['post_check_lead_seconds'] for r in rows),
        new_collection=False, database_access=False, fabricated_receipts=False,
        primary_pair_eligibility_asserted=False, rows=rows)
    (OUT/'independent-source-spotcheck.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
