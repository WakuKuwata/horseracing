"""Synthetic audit only: no database, model fitting, or production artifacts.

Run from the repository root with serving/.venv/bin/python.
The real preregistration is read unchanged; records are temporary test fixtures.
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile

from horseracing_serving import paired_prospective as pp

spec = importlib.util.spec_from_file_location(
    'paired_test_fixtures', Path('serving/tests/unit/test_paired_prospective.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
plan = pp.load_preregistration('specs/133-prospective-paired-comparison/preregistration.json')


def run_case(dates, regimes):
    with tempfile.TemporaryDirectory() as directory:
        for i, day in enumerate(dates):
            record = fixture.record(
                str(i), day.isoformat(), {'a': .8, 'b': .2}, {'a': .2, 'b': .8},
                bundle=plan['comparators']['new_bundle_manifest_sha256'],
                anchor=plan['comparators']['old_anchor_sha256'],
                lam=(plan['comparators']['display_lambda2'], plan['comparators']['display_lambda3']))
            pp.publish_json(Path(directory) / f'{i}.json', record)
        chosen, rejected = pp.select_records([directory], plan=plan)
        results = {rid: fixture.bundle({'a': (1, 'finished'), 'b': (2, 'finished')}) for rid in chosen}
        out = {'selected': len(chosen), 'rejected': len(rejected),
               'outside_window_selected': [r['race_day'] for r in chosen.values()
                   if not plan['window']['from'] <= r['race_day'] <= plan['window']['to']],
               'readouts': {}}
        for regime in regimes:
            result = pp.score_records(chosen, results, {}, plan=plan, regime=regime,
                                      mode='final', today=dt.date(2027, 1, 2))
            out['readouts'][regime] = {k: result[k] for k in ('status', 'verdict')}
            out['readouts'][regime]['n_days'] = result['overall']['n_days']
        return out


start = dt.date.fromisoformat(plan['window']['from'])
count = plan['window']['min_eval_days']
output = {
    'scope': 'Synthetic fixtures, real unchanged preregistration; no DB or fits',
    'preregistration_sha256': plan['_sha256'],
    'source_sha256': pp.sha256(pp.__file__),
    'registered_primary': plan['primary']['regime'],
    'registered_min_days': count,
    'outside_window_case': run_case(
        [start - dt.timedelta(days=1)] + [start + dt.timedelta(days=i) for i in range(count - 1)],
        ['preweight']),
    'secondary_verdict_case': run_case(
        [start + dt.timedelta(days=i) for i in range(count)], ['serving']),
}
print(json.dumps(output, ensure_ascii=False, indent=2))
