"""Read-only regression probes. Run with api/.venv/bin/python.

No database connections, writes, training, network requests, or subprocess execution.
The cancellation probe supplies production SQL helpers with a fixture representing the
currently STARTED field after cancellation; router and probability logic are real.
The manifest probe compiles the exact current function AST without importing ops.
"""
import ast
import datetime as dt
import json
import os
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from horseracing_api.routers import predictions as router
from horseracing_api import selection
from horseracing_eval.baselines import harville_topk
from horseracing_db.enums import EntryStatus

REPO = Path('/Users/kuwatawaku/workspace/horseracing')

def cancellation_probe():
    original_win = [0.4, 0.3, 0.2, 0.1]
    top2, top3 = harville_topk(original_win)
    # Persisted run has all four horses. Horse 1 has subsequently been cancelled.
    # Current production queries filter by current STARTED, therefore return 2..4.
    remaining = [
        (i + 1, str(i + 1), EntryStatus.STARTED, original_win[i], top2[i], top3[i], None)
        for i in range(1, 4)
    ]
    fake_query_session = SimpleNamespace(execute=lambda query: SimpleNamespace(
        all=lambda: [(row[0], row[3]) for row in remaining]))
    run_id = uuid.UUID('00000000-0000-0000-0000-000000000001')
    race_id = '202601010101'
    pmap = selection.canonical_win_probs(fake_query_session, run_id=run_id, race_id=race_id)
    with patch.object(selection, 'canonical_win_odds', return_value={2: 2, 3: 3, 4: 6}):
        qmap, consistent = selection.market_win_probs(
            None, race_id=race_id, p_numbers=set(pmap))
    run = SimpleNamespace(
        prediction_run_id=run_id, model_version='mix-129-nj6', logic_version='test',
        computed_at=dt.datetime(2026, 9, 6, tzinfo=dt.UTC))
    returns = {
        'get_race': SimpleNamespace(race_date=dt.date(2026, 9, 6)),
        'build_race_chaos': None, 'select_prediction_run': run,
        'canonical_win_probs': pmap, 'market_win_probs': (qmap, consistent),
        'prior_start_counts': {}, 'run_predictions': remaining,
        'available_models_for_race': [], 'win_odds_as_of': None,
        'race_has_results': False, 'load_activation_calibrator': None,
        'load_p_calibrator': None, 'build_race_dispersion': None,
        'build_race_divergence': None, 'load_boundary': None,
    }
    with ExitStack() as stack:
        for name, value in returns.items():
            stack.enter_context(patch.object(router, name, return_value=value))
        response = router.predictions(
            race_id, bet_type=None, top=20, model_version=None, session=None)
    sums = {
        'win': sum(h.win for h in response.horses),
        'top2': sum(h.top2 for h in response.horses),
        'top3': sum(h.top3 for h in response.horses),
        'market_q': sum(h.market_win_prob for h in response.horses),
    }
    return {
        'fixture': {'original_win': original_win, 'cancelled_horse_number': 1,
                    'remaining_horse_numbers': [2, 3, 4]},
        'actual_sums': sums,
        'expected_sums_current_three_horse_field': {'win': 1, 'top2': 2, 'top3': 3},
        'canonical_consistent': response.canonical_consistent,
        'horses': [
            {'horse_number': h.horse_number, 'win': h.win, 'top2': h.top2,
             'top3': h.top3, 'market_q': h.market_win_prob, 'divergence': h.divergence}
            for h in response.horses
        ],
        'reproduced': abs(sums['win'] - 0.6) < 1e-12 and response.canonical_consistent,
        'scope': 'Real canonical and router logic with mocked database/I/O return values; not a DB integration test',
    }

def manifest_probe():
    path = REPO / 'ops/src/horseracing_ops/runner.py'
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_calib_argv')
    namespace = {'os': os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    cases = []
    for values in [
        {'PREDICT_CALIB_MODE': 'manifest-required'},
        {'PREDICT_CALIB_MODE': 'manifest-required', 'PREDICT_CALIB_MANIFEST': ''},
        {'PREDICT_CALIB_MODE': 'manifest-required', 'PREDICT_CALIB_MANIFEST': '/tmp/required.json'},
    ]:
        with patch.dict(os.environ, values, clear=True):
            cases.append({'env': values, 'argv': namespace['_calib_argv']('PREDICT')})
    return {
        'cases': cases,
        'reproduced': cases[0]['argv'] == [] and cases[1]['argv'] == [],
        'scope': 'Exact current _calib_argv AST, no subprocess and no manifest file read',
    }

if __name__ == '__main__':
    print(json.dumps({'cancellation': cancellation_probe(), 'required_manifest_missing': manifest_probe()}, indent=2, ensure_ascii=False))
