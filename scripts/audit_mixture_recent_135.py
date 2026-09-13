"""Independent135 recent metric audit, after completed bounded outcome scoring."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import audit_mixture_reproducibility_135 as audit

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'artifacts/135-mixture-reproducibility/recent'
OUT = ROOT/'specs/135-mixture-reproducibility/evidence/recent-numeric-review.json'


def run():
    assert not OUT.exists()
    summary = audit.read(WORK/'summary.json')
    execution = audit.read(WORK/'execution-receipt.json')
    inputs = audit.read(WORK/'input-receipt.json')
    assert summary['status'] == 'RECENT_CONFIRMATION_COMPLETE'
    assert summary['execution_receipt_sha256'] == audit.sha(WORK/'execution-receipt.json')
    assert not summary['can_adopt'] and not summary['eligible_for_verdict'] and not summary['untouched_holdout']
    assert execution['status'] == 'PREDICTIONS_FROZEN_BEFORE_OUTCOMES' and execution['no_outcomes_read']
    assert inputs['n_target_races'] == 144
    assert inputs['n_included_races'] + len(inputs['excluded']) == 144
    for path, expected in execution['files'].items():
        assert audit.sha(path) == expected, path
    saved = audit.sealed_pickle(WORK/'predictions.pkl', execution['files'][str(WORK/'predictions.pkl')])
    outcomes = audit.sealed_pickle(WORK/'outcomes.pkl', summary['outcomes_sha256'])
    records = outcomes['records']
    assert len(records) == summary['n_included_races'] == inputs['n_included_races']
    assert set(saved['ids']) == {r.race_id for r in records}
    assert set(saved['dates']) == set(saved['ids'])
    assert set(saved['predictions']) == {'full', 'preweight'}
    for arms in saved['predictions'].values():
        assert set(arms) == {'baseline', 'candidate'}
        assert all(set(v) == set(saved['ids']) for v in arms.values())
    assert all('2026-08-29' <= r.race_date.isoformat() <= '2026-09-06' for r in records)
    assert len({r.race_date for r in records}) <= 4
    for r in records:
        assert saved['ids'][r.race_id] == r.ids and saved['dates'][r.race_id] == r.race_date
    cfg = audit.read(ROOT/'specs/135-mixture-reproducibility/experiment.json')['bootstrap']
    checks = 0
    def check(a, b):
        nonlocal checks
        if a is None or b is None:
            assert a is b, (a, b)
        else:
            assert np.allclose(a, b, rtol=0, atol=2e-12), (a, b)
        checks += 1
    for regime in ('full', 'preweight'):
        result = summary['reports'][regime]
        days = {}
        for arm in ('baseline', 'candidate'):
            values, days[arm] = audit.calculate(records, saved['predictions'][regime][arm], through='2026-09-06')
            for k in ('winner_nll', 'n_races', 'n_complete_races', 'n_eligible_races'):
                check(values[k], result[arm][k])
            assert set(values['heads']) == set(result[arm]['heads'])
            for h in values['heads']:
                for k in ('logloss', 'brier', 'ece', 'n_rows'):
                    check(values['heads'][h][k], result[arm]['heads'][h][k])
        paired = audit.day_pair(days['baseline'], [days['candidate']], cfg)
        for m in audit.METRICS:
            for k in ('point', 'ci', 'n_days', 'n_rows'):
                check(paired[m][k], result['paired'][m][k])
    output = {'status':'PASS', 'summary_sha256':audit.sha(WORK/'summary.json'),
              'execution_receipt_sha256':audit.sha(WORK/'execution-receipt.json'),
              'auditor_sha256':audit.sha(__file__), 'metric_auditor_sha256':audit.sha(audit.__file__),
              'checks':checks, 'n_races':len(records), 'n_dates':len({r.race_date for r in records}),
              'can_adopt':False, 'eligible_for_verdict':False}
    with OUT.open('x') as f:
        json.dump(output, f, indent=2); f.write('\n')
    print(json.dumps(output))


if __name__ == '__main__':
    run()
