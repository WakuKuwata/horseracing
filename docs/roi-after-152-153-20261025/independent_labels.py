"""Verify label counts independently with minimal source columns and scalar races."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT/'artifacts/market_ev/rows_2007.parquet'
INVENTORY = ROOT/'artifacts/roi-after-152-153-20261025/label_inventory.json'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    before = sha(SOURCE)
    inventory = json.loads(INVENTORY.read_text())
    assert before == inventory['input_sha256']
    cols = ['race_id', 'horse_id', 'race_date', 'year', 'race_ok', 'dead_heat', 'finish_order', 'result_status']
    table = pq.read_table(SOURCE, columns=cols,
        filters=[('race_date', '>=', '2007-01-01'), ('race_date', '<=', '2026-06-26')])
    races = defaultdict(list)
    for row in table.to_pylist():
        if row['race_id'] != '202504040407':
            races[row['race_id']].append(row)
    years = {}
    nonfinished_top3 = 0
    keys = set()
    for rid, rows in races.items():
        if not all(r['race_ok'] and not r['dead_heat'] for r in rows):
            continue
        year = rows[0]['year']
        d = years.setdefault(year, dict(year=year, rows=0, races=0,
            valid_stage1=0, valid_stage2=0, valid_stage3=0))
        d['rows'] += len(rows)
        d['races'] += 1
        for row in rows:
            key = (rid, row['horse_id'])
            assert key not in keys
            keys.add(key)
            if row['finish_order'] in (1, 2, 3) and row['result_status'] != 'finished':
                nonfinished_top3 += 1
        for stage in (1, 2, 3):
            if len(rows) - (stage-1) < 2:
                break
            ranked = [r for r in rows if r['result_status'] == 'finished' and r['finish_order'] == stage]
            if len(ranked) != 1:
                break
            d['valid_stage'+str(stage)] += 1
    records = [years[y] for y in sorted(years)]
    assert records == inventory['by_year'], (records, inventory['by_year'])
    assert nonfinished_top3 == 0
    assert before == sha(SOURCE)
    out = dict(status='passed', blockers=[], input_sha256=before,
        inventory_sha256=sha(INVENTORY), script_sha256=sha(Path(__file__)),
        source_read='pyarrow minimal columns; independent race loop without142/root imports',
        year_records_checked=len(records), numeric_counts_checked=6*len(records),
        rows=len(keys), races=sum(v['races'] for v in records),
        nonfinished_rows_with_top3_finish_order=nonfinished_top3,
        by_year=records, new_fits=0, new_policies_scored=0)
    (HERE/'independent_label_verification.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k != 'by_year'}))


if __name__ == '__main__':
    main()
