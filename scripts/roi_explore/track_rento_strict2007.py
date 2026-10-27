"""Diagnostic follow-up of the previously reported, fixed back-to-back winner rule.

This does not add a candidate, change screening thresholds, or alter the primary
1,806-rule result. It reports already-known-period uncertainty for a named rule.
"""
from __future__ import annotations

import replay_strict2007_patterns as strict

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

core, ev, dsl = strict.core, strict.ev, strict.dsl
REPO = strict.REPO
OUT = strict.OUT
PID = 'folklore.interval.rento_lastwon_lt10'


def main():
    source = REPO / 'artifacts/roi_explore/patterns/folklore.json'
    obj = json.loads(source.read_text())
    definitions = obj['patterns'] if isinstance(obj, dict) else obj
    definition = next(p for p in definitions if p['pattern_id'] == PID)
    rows = REPO / 'artifacts/roi_strict2007_20260930/input/rows.parquet'
    main_scores = OUT / 'scores.json'
    freeze = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'pattern_id': PID, 'exact_definition': definition,
        'reason': 'Parent-requested tracking of a previously reported fixed rule, chosen before this follow-up; historical outcomes already inspected.',
        'does_not_change_primary_selection': True, 'bootstrap_reps': 2000, 'seed': 20260923,
        'windows': {'ALL': [2007, 2026], 'C': [2019, 2026]}, 'cutoff': strict.CUTOFF,
        'sha256': {str(p.relative_to(REPO)): core.sha(p) for p in (source, rows, main_scores, Path(__file__))},
    }
    core.save(OUT, 'rento_diagnostic_freeze.json', freeze)
    ev.ART = rows.parent
    ev.WINDOWS = strict.WINDOWS
    arr, _ = core.load_rows()
    p = dsl.parse_pattern(definition, source=source.name)
    r = core.score(p, arr, {})
    yearly = strict.add_all_and_yearly(r, arr)
    previous = next(x for x in json.loads(main_scores.read_text()) if x['pattern_id'] == PID)
    for w in ('ALL', 'D', 'Q', 'C'):
        for key in ('n_bets', 'n_hits', 'stake', 'payout', 'roi'):
            assert np.isclose(r[w][key], previous[w][key], rtol=1e-12), (w, key)
    result = ev.strip(r)
    result.update(
        exact_definition=definition,
        ALL_boot=core.bootstrap(r, 'ALL', arr, 2000),
        C_boot=core.bootstrap(r, 'C', arr, 2000),
        yearly_bets=yearly,
        analysis_role='Previously presented fixed-rule tracking only; no candidate or primary-selection change.',
        primary_screen_pass=False,
        D_failure_reasons=['roi_lt_1', 'hits_lt_20', 'days_lt_100'],
        Q_support_failure_reasons=['hits_lt_20', 'days_lt_100'],
        can_adopt=False, eligible_for_verdict=False,
        limitations=['Known historical periods; percentile intervals are exploratory.',
                     'Payout is stored odds times 100, not independently verified official payout.',
                     'Uses a closing-price condition (odds < 10); decision-time feasibility not demonstrated.'],
    )
    assert not ev.passes(r['D'])[0] and not ev.passes(r['Q'])[0]
    core.save(OUT, 'rento_diagnostic.json', result)
    print(json.dumps({k: result[k] for k in ('ALL_boot', 'C_boot', 'D_failure_reasons', 'Q_support_failure_reasons')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
