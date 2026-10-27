"""Repeat the fixed scorer vs frozen interims without reading DB or changing evidence."""
import hashlib
import json
from pathlib import Path

from horseracing_serving import paired_prospective as pp

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
inputs = json.loads((EVIDENCE / 'interim-input-snapshot.json').read_text())
plan = pp.load_preregistration(ROOT / 'specs/133-prospective-paired-comparison/preregistration.json')
assert plan['_sha256'] == inputs['preregistration_sha256']
chosen, rejected = pp.select_records([ROOT / d for d in inputs['record_dirs']], plan=plan)
# Selection metadata contains the caller's paths; normalize to the original relative spelling.
for record in chosen.values():
    for key in ('_path',):
        if key in record:
            record[key] = str(Path(record[key]).relative_to(ROOT))
    if '_paths' in record:
        record['_paths'] = [str(Path(p).relative_to(ROOT)) for p in record['_paths']]
assert not rejected and len(chosen) == 72
assert hashlib.sha256(json.dumps(chosen, sort_keys=True).encode()).hexdigest() == inputs['selected_records_sha256']
results = {rid: {'results': data['results'], 'started_now': set(data['started_now'])}
           for rid, data in inputs['results'].items()}
proof = json.loads((EVIDENCE / 'interim-parity.json').read_text())
for regime in pp.REGIMES:
    saved_path = ROOT / f'specs/133-prospective-paired-comparison/evidence/interim-{regime}-2026-09-20.json'
    assert pp.sha256(saved_path) == proof['comparisons'][regime]['saved_interim_sha256']
    saved = json.loads(saved_path.read_text())
    current = pp.score_records(chosen, results, inputs['attrs'], plan=plan, regime=regime, mode='interim')
    del current['computed_at']
    for key, value in current.items():
        assert saved[key] == value, (regime, key)
    assert hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest() == proof['comparisons'][regime]['score_excluding_timestamp_sha256']
    print(f"{regime}: EXACT_MATCH ({current['overall']['n_races']} races, {current['overall']['n_days']} days)")
